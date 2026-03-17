"""Layer 1: 급부명 정규화 + canonical key 생성 + 매칭.

LLM 호출 없음. 순수 Python + config/synonyms*.json 외부 사전.

확장 방법:
  - 업계 공통 표현: config/synonyms.json에 추가
  - 회사별 특수 표현: config/synonyms_<보험사명>.json에 추가 (파일 없으면 자동 무시)
  - 새 슬롯 카테고리: _KEY_SLOTS에 추가 + synonyms.json에 카테고리 추가
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
_SYNONYMS_GENERIC = _CONFIG_DIR / "synonyms.json"

_KEY_SLOTS = ("condition", "disease", "action")
_TYPE_SUFFIXES = ("지원금", "급여금", "보험금", "치료비", "진단비", "자금", "비용")


# ---------------------------------------------------------------------------
# Synonyms loader: generic + 회사별 파일 자동 병합
# ---------------------------------------------------------------------------

_synonyms_cache: dict | None = None


def _load_synonyms() -> dict[str, dict[str, list[str]]]:
    """generic + 회사별 synonyms 파일을 모두 로드해 병합 반환.

    병합 규칙: 같은 슬롯·canonical_label이 있으면 variants를 union.
    """
    global _synonyms_cache
    if _synonyms_cache is not None:
        return _synonyms_cache

    merged: dict[str, dict[str, list[str]]] = {}

    def _merge_file(path: Path) -> None:
        if not path.is_file():
            logger.warning("synonyms file not found: %s", path)
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for slot, entries in data.items():
            if slot.startswith("_"):   # _comment, _version, _insurer 등 메타 키 무시
                continue
            merged.setdefault(slot, {})
            for label, variants in entries.items():
                existing = merged[slot].setdefault(label, [])
                for v in variants:
                    if v not in existing:
                        existing.append(v)

    # 1) generic (필수)
    _merge_file(_SYNONYMS_GENERIC)

    # 2) 회사별 (자동 감지: synonyms_*.json)
    for company_file in sorted(_CONFIG_DIR.glob("synonyms_*.json")):
        _merge_file(company_file)
        logger.info("Loaded company synonyms: %s", company_file.name)

    _synonyms_cache = merged
    logger.info(
        "Synonyms loaded — slots: %s",
        {slot: sum(len(vs) for vs in entries.values()) for slot, entries in merged.items()},
    )
    return _synonyms_cache


def invalidate_synonyms_cache() -> None:
    """synonyms 캐시를 무효화. 파일 수정 후 재로드가 필요할 때 사용."""
    global _synonyms_cache, _reverse_cache
    _synonyms_cache = None
    _reverse_cache = None


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
    """급부명 텍스트 정규화: 공백/괄호/특수문자 제거.

    >>> normalize_text("비급여(전액본인부담 포함) 항암약물 ·방사선치료자금")
    '비급여항암약물방사선치료자금'
    """
    if not text:
        return ""
    text = _RE_PAREN.sub("", text)
    text = _RE_SPECIAL.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# canonical_key: 정규화된 이름에서 의미 슬롯 추출 → 키 생성
# ---------------------------------------------------------------------------

def _extract_slot(normalized: str, category: str) -> str:
    """normalized 텍스트에서 category의 가장 긴 매칭 variant를 찾아 canonical label 반환."""
    cat_map = _build_reverse_map().get(category, {})
    best_label, best_len = "", 0
    for variant, label in cat_map.items():
        if variant in normalized and len(variant) > best_len:
            best_label, best_len = label, len(variant)
    return best_label


def _mask_action_variants(text: str) -> str:
    """disease 추출 전 action variant를 공백으로 마스킹.

    '항암약물방사선치료' 안의 '암' → 일반암 오탐 방지.
    action variant를 먼저 제거하면 disease 후보 범위가 순수해진다.
    """
    action_map = _build_reverse_map().get("action", {})
    # 길이 내림차순 정렬로 긴 것부터 제거 (부분 중복 방지)
    for variant in sorted(action_map, key=len, reverse=True):
        text = text.replace(variant, " " * len(variant))
    return text


def _strip_type_suffix(text: str) -> str:
    """type 접미사 제거 — '급여금' → '급여' 오탐 방지."""
    for suffix in _TYPE_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


_CATEGORY_KO_TO_ACTION = {
    "진단": "진단", "수술": "수술", "치료": "치료",
    "입원": "입원", "통원": "통원", "사망": "사망", "장해": "장해",
}


def canonical_key(benefit_name: str, category_ko: str = "") -> str:
    """급부명 → canonical key 문자열.

    1. 정규화 (공백/괄호/특수문자 제거)
    2. type 접미사 제거 (자금/보험금/급여금 — 회사별 명명 차이)
    3. condition | disease | action 슬롯 추출 (longest-match)
       - disease: action variant를 먼저 마스킹 후 추출 (오탐 방지)
       - action 미추출 시 benefit_category_ko fallback
    4. 빈 슬롯 제외하고 '|'로 결합

    >>> canonical_key("비급여(전액본인부담 포함) 항암약물 ·방사선치료자금")
    '비급여|항암방사선'
    >>> canonical_key("갑상선암", "진단")
    '갑상선암|진단'
    """
    norm = normalize_text(benefit_name)
    if not norm:
        return ""

    stripped = _strip_type_suffix(norm)
    # disease 추출 전용: action variant 마스킹으로 '항암약물' 안의 '암' 오탐 방지
    stripped_for_disease = _mask_action_variants(stripped)

    slots: list[str] = []
    for cat in _KEY_SLOTS:
        if cat == "disease":
            val = _extract_slot(stripped_for_disease, cat)
        elif cat == "action":
            val = _extract_slot(stripped, cat) or _extract_slot(norm, cat)
            if not val and category_ko:
                val = _CATEGORY_KO_TO_ACTION.get(category_ko, "")
        else:
            # condition: stripped 사용 (오탐 방지)
            val = _extract_slot(stripped, cat)
        if val:
            slots.append(val)

    return "|".join(slots) if slots else norm


# ---------------------------------------------------------------------------
# match_benefits: canonical key 기반 매칭
# ---------------------------------------------------------------------------

@dataclass
class MatchedPair:
    canonical_key: str
    our_row: dict | None = None
    comp_row: dict | None = None
    match_type: str = ""   # "matched" | "our_only" | "comp_only"
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
        ck = canonical_key(row.get("benefit_name", ""), row.get("benefit_category_ko", ""))
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
    matched = our_only = comp_only = 0

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
                    pairs.append(MatchedPair(ck, o, c, "matched",
                                             o.get("benefit_name", ""), c.get("benefit_name", "")))
                    matched += 1
                else:
                    pairs.append(MatchedPair(ck, o, None, "our_only", o.get("benefit_name", "")))
                    our_only += 1
            for idx, c in enumerate(comps):
                if idx not in used_comp:
                    pairs.append(MatchedPair(ck, None, c, "comp_only",
                                             comp_name=c.get("benefit_name", "")))
                    comp_only += 1

        elif ours:
            for o in ours:
                pairs.append(MatchedPair(ck, o, None, "our_only", o.get("benefit_name", "")))
                our_only += 1
        else:
            for c in comps:
                pairs.append(MatchedPair(ck, None, c, "comp_only",
                                         comp_name=c.get("benefit_name", "")))
                comp_only += 1

    logger.info(
        "match_benefits: our=%d comp=%d → matched=%d our_only=%d comp_only=%d",
        len(our_rows), len(comp_rows), matched, our_only, comp_only,
    )
    return MatchResult(pairs, len(our_rows), len(comp_rows), matched, our_only, comp_only)


def _find_best_match(our_row: dict, comp_rows: list[dict], used: set[int]) -> int | None:
    """comp_rows 중 미사용 행 중 가장 유사한 것의 index 반환."""
    our_amt = our_row.get("amount", "")
    best_idx, best_score = None, -1.0
    for idx, c in enumerate(comp_rows):
        if idx in used:
            continue
        score = _amount_similarity(our_amt, c.get("amount", ""))
        if score > best_score:
            best_score, best_idx = score, idx
    return best_idx


def _amount_similarity(a: str, b: str) -> float:
    """두 금액 문자열의 유사도 (0~1). 같으면 1.0."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(set(a) & set(b)) / max(len(set(a)), len(set(b)))
