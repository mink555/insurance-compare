"""Layer 1: 급부명 정규화 + canonical key 생성 + 매칭.

LLM 호출 없음. 순수 Python + config/synonyms.json 외부 사전.

확장 방법:
  - 새 동의어: config/synonyms.json에 추가 (코드 수정 불필요)
  - 새 슬롯 카테고리: _SLOT_ORDER에 추가 + synonyms.json에 카테고리 추가
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_SYNONYMS_PATH = Path(__file__).resolve().parents[3] / "config" / "synonyms.json"

_KEY_SLOTS = ("condition", "disease", "action")

_TYPE_SUFFIXES = ("지원금", "급여금", "보험금", "치료비", "진단비", "자금", "비용")


# ---------------------------------------------------------------------------
# Synonyms loader (lazy singleton)
# ---------------------------------------------------------------------------

_synonyms_cache: dict | None = None


def _load_synonyms() -> dict[str, dict[str, list[str]]]:
    global _synonyms_cache
    if _synonyms_cache is not None:
        return _synonyms_cache

    if not _SYNONYMS_PATH.is_file():
        logger.warning("synonyms.json not found: %s — using empty dict", _SYNONYMS_PATH)
        _synonyms_cache = {}
        return _synonyms_cache

    with open(_SYNONYMS_PATH, encoding="utf-8") as f:
        _synonyms_cache = json.load(f)
    logger.info("Loaded synonyms.json: %s", {k: len(v) for k, v in _synonyms_cache.items()})
    return _synonyms_cache


def reload_synonyms() -> None:
    """Force reload synonyms.json (e.g. after editing config)."""
    global _synonyms_cache
    _synonyms_cache = None
    _load_synonyms()


# ---------------------------------------------------------------------------
# Build reverse lookup: variant → canonical label (per slot category)
# ---------------------------------------------------------------------------

_reverse_cache: dict[str, dict[str, str]] | None = None


def _build_reverse_map() -> dict[str, dict[str, str]]:
    """Returns {category: {variant_normalized: canonical_label}}."""
    global _reverse_cache
    if _reverse_cache is not None:
        return _reverse_cache

    synonyms = _load_synonyms()
    _reverse_cache = {}
    for category, entries in synonyms.items():
        rev: dict[str, str] = {}
        for canonical_label, variants in entries.items():
            for v in variants:
                norm_v = re.sub(r"\s+", "", v)
                rev[norm_v] = canonical_label
        _reverse_cache[category] = rev
    return _reverse_cache


# ---------------------------------------------------------------------------
# normalize_text: 급부명 정규화
# ---------------------------------------------------------------------------

_RE_PAREN = re.compile(r"\([^)]*\)")
_RE_SPECIAL = re.compile(r"[·∙/,\s\u3000]+")
def normalize_text(text: str) -> str:
    """급부명 텍스트 정규화: 공백/괄호/특수문자/조사 제거.

    >>> normalize_text("비급여(전액본인부담 포함) 항암약물 ·방사선치료자금")
    '비급여항암약물방사선치료자금'
    """
    if not text:
        return ""
    text = _RE_PAREN.sub("", text)
    text = _RE_SPECIAL.sub("", text)
    text = text.strip()
    return text


# ---------------------------------------------------------------------------
# canonical_key: 정규화된 이름에서 의미 슬롯 추출 → 키 생성
# ---------------------------------------------------------------------------

def _extract_slot(normalized: str, category: str) -> str:
    """normalized 텍스트에서 category의 가장 긴 매칭 variant를 찾아 canonical label 반환."""
    reverse_map = _build_reverse_map()
    cat_map = reverse_map.get(category, {})
    if not cat_map:
        return ""

    best_label = ""
    best_len = 0
    for variant, label in cat_map.items():
        if variant in normalized and len(variant) > best_len:
            best_label = label
            best_len = len(variant)
    return best_label


def _strip_type_suffix(text: str) -> str:
    """Remove type suffix (자금/보험금/급여금/...) to prevent false condition matches.

    "급여금" contains "급여" which would be falsely detected as a condition.
    """
    for suffix in _TYPE_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def canonical_key(benefit_name: str) -> str:
    """급부명 → canonical key 문자열.

    1. 정규화 (공백/괄호/특수문자 제거)
    2. type 접미사 제거 (자금/보험금/급여금 — 회사별 명명 차이일 뿐)
    3. condition | disease | action 슬롯 추출
       - stripped 텍스트 우선, 슬롯 없으면 norm(접미사 제거 전)으로 재시도
    4. 빈 슬롯 제외하고 '|'로 결합

    >>> canonical_key("비급여(전액본인부담 포함) 항암약물 ·방사선치료자금")
    '비급여|항암약물'
    """
    norm = normalize_text(benefit_name)
    if not norm:
        return ""

    stripped = _strip_type_suffix(norm)

    slots: list[str] = []
    for cat in _KEY_SLOTS:
        # action 슬롯은 type suffix 제거 전 norm으로도 재시도
        # (예: '항암치료비' → stripped='항암', norm에서 '항암치료비' variant 매칭)
        # condition/disease는 stripped만 사용 (norm fallback 시 '급여금' → '급여' 오탐 방지)
        if cat == "action":
            val = _extract_slot(stripped, cat) or _extract_slot(norm, cat)
        else:
            val = _extract_slot(stripped, cat)
        if val:
            slots.append(val)

    if not slots:
        return norm

    return "|".join(slots)


# ---------------------------------------------------------------------------
# match_benefits: canonical key 기반 매칭
# ---------------------------------------------------------------------------

@dataclass
class MatchedPair:
    canonical_key: str
    our_row: dict | None = None
    comp_row: dict | None = None
    match_type: str = ""  # "matched" | "our_only" | "comp_only"
    our_name: str = ""
    comp_name: str = ""


@dataclass
class MatchResult:
    pairs: list[MatchedPair] = field(default_factory=list)
    our_count: int = 0
    comp_count: int = 0
    matched_count: int = 0
    our_only_count: int = 0
    comp_only_count: int = 0


def _build_key_map(rows: list[dict]) -> dict[str, list[dict]]:
    """rows → {canonical_key: [row, ...]}."""
    key_map: dict[str, list[dict]] = {}
    for row in rows:
        bn = row.get("benefit_name", "")
        ck = canonical_key(bn)
        key_map.setdefault(ck, []).append(row)
    return key_map


def match_benefits(our_rows: list[dict], comp_rows: list[dict]) -> MatchResult:
    """canonical key 기반으로 당사/타사 급부를 매칭.

    1:1 매칭 우선. 1:N일 경우 금액 유사도로 greedy 매칭.
    """
    our_map = _build_key_map(our_rows)
    comp_map = _build_key_map(comp_rows)

    all_keys = dict.fromkeys(list(our_map) + list(comp_map))

    pairs: list[MatchedPair] = []
    matched = 0
    our_only = 0
    comp_only = 0

    for ck in all_keys:
        ours = our_map.get(ck, [])
        comps = comp_map.get(ck, [])

        if ours and comps:
            used_comp: set[int] = set()
            for o in ours:
                best_idx = _find_best_match(o, comps, used_comp)
                if best_idx is not None:
                    c = comps[best_idx]
                    used_comp.add(best_idx)
                    pairs.append(MatchedPair(
                        canonical_key=ck,
                        our_row=o,
                        comp_row=c,
                        match_type="matched",
                        our_name=o.get("benefit_name", ""),
                        comp_name=c.get("benefit_name", ""),
                    ))
                    matched += 1
                else:
                    pairs.append(MatchedPair(
                        canonical_key=ck,
                        our_row=o,
                        match_type="our_only",
                        our_name=o.get("benefit_name", ""),
                    ))
                    our_only += 1

            for idx, c in enumerate(comps):
                if idx not in used_comp:
                    pairs.append(MatchedPair(
                        canonical_key=ck,
                        comp_row=c,
                        match_type="comp_only",
                        comp_name=c.get("benefit_name", ""),
                    ))
                    comp_only += 1

        elif ours:
            for o in ours:
                pairs.append(MatchedPair(
                    canonical_key=ck,
                    our_row=o,
                    match_type="our_only",
                    our_name=o.get("benefit_name", ""),
                ))
                our_only += 1

        else:
            for c in comps:
                pairs.append(MatchedPair(
                    canonical_key=ck,
                    comp_row=c,
                    match_type="comp_only",
                    comp_name=c.get("benefit_name", ""),
                ))
                comp_only += 1

    logger.info(
        "match_benefits: our=%d comp=%d → matched=%d our_only=%d comp_only=%d",
        len(our_rows), len(comp_rows), matched, our_only, comp_only,
    )

    return MatchResult(
        pairs=pairs,
        our_count=len(our_rows),
        comp_count=len(comp_rows),
        matched_count=matched,
        our_only_count=our_only,
        comp_only_count=comp_only,
    )


def _find_best_match(
    our_row: dict,
    comp_rows: list[dict],
    used: set[int],
) -> int | None:
    """comp_rows 중 사용되지 않은 것에서 가장 유사한 행을 찾아 index 반환."""
    our_amt = our_row.get("amount", "")

    best_idx: int | None = None
    best_score = -1

    for idx, c in enumerate(comp_rows):
        if idx in used:
            continue
        score = _amount_similarity(our_amt, c.get("amount", ""))
        if score > best_score:
            best_score = score
            best_idx = idx

    return best_idx


def _amount_similarity(a: str, b: str) -> float:
    """두 금액 문자열의 유사도 (0-1). 같으면 1, 다르면 글자 겹침 비율."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    common = len(set(a) & set(b))
    return common / max(len(set(a)), len(set(b)))
