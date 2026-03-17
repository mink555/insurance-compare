"""Layer 3: 규칙 기반 비교 엔진.

LLM 호출 없음. 순수 Python + config/compare_rules.json.

UI 진입점: build_comparison(our_rows, comp_rows) → ComparisonResult
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import MatchedPair, MatchResult, canonical_key, match_benefits

logger = logging.getLogger(__name__)

_RULES_PATH = Path(__file__).resolve().parents[3] / "config" / "compare_rules.json"


# ---------------------------------------------------------------------------
# Rules loader (lazy singleton)
# ---------------------------------------------------------------------------

_rules_cache: dict | None = None


def _load_rules() -> dict:
    global _rules_cache
    if _rules_cache is not None:
        return _rules_cache
    if not _RULES_PATH.is_file():
        logger.warning("compare_rules.json not found: %s", _RULES_PATH)
        _rules_cache = {}
        return _rules_cache
    with open(_RULES_PATH, encoding="utf-8") as f:
        _rules_cache = json.load(f)
    return _rules_cache


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SlotComparison:
    """슬롯 1개 비교 결과."""
    dimension: str
    label: str
    our_value: str
    comp_value: str
    advantage: str = ""  # "당사우위" / "타사우위" / "동일" / "비교불가"


@dataclass
class ComparedPair:
    """매칭된 급부 쌍의 비교 결과."""
    canonical_key: str
    our_name: str = ""
    comp_name: str = ""
    our_amount: str = ""
    comp_amount: str = ""
    match_type: str = ""  # "matched" / "our_only" / "comp_only"
    slot_comparisons: list[SlotComparison] = field(default_factory=list)
    overall_advantage: str = ""
    rationale: str = ""
    our_row: dict | None = None
    comp_row: dict | None = None


@dataclass
class ComparisonResult:
    """build_comparison()의 최종 결과. UI에서 이 객체 하나만 소비."""
    pairs: list[ComparedPair] = field(default_factory=list)
    only_our: list[ComparedPair] = field(default_factory=list)
    only_comp: list[ComparedPair] = field(default_factory=list)
    slot_table: list[dict] = field(default_factory=list)
    amount_table: list[dict] = field(default_factory=list)
    coverage_summary: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Slot comparison logic
# ---------------------------------------------------------------------------

_RE_NUMBER = re.compile(r"[\d,]+")
_RE_AMOUNT_WON = re.compile(r"([\d,]+)\s*만\s*원")


def _parse_amount_won(text: str) -> int | None:
    """금액 문자열에서 원 단위 숫자 추출. '1,000만원' → 10000000."""
    if not text:
        return None
    m = _RE_AMOUNT_WON.search(text)
    if m:
        return int(m.group(1).replace(",", "")) * 10000
    nums = [n for n in _RE_NUMBER.findall(text) if n.replace(",", "")]
    if nums:
        val = int(nums[0].replace(",", ""))
        if val > 0:
            return val
    return None


def _compare_slot(
    dimension: str,
    our_val: str,
    comp_val: str,
    rule: dict,
) -> str:
    """단일 슬롯 비교 → 우위 판정."""
    if not our_val and not comp_val:
        return "비교불가"

    rule_type = rule.get("type", "display_only")

    # rank/none_is_better 타입은 한쪽이 비어있어도 비교불가 (정보 없음 ≠ 열등)
    # numeric 타입만 한쪽 비어있으면 우위 판정
    if rule_type == "numeric":
        if not our_val:
            return "타사우위"
        if not comp_val:
            return "당사우위"
    else:
        if not our_val or not comp_val:
            return "비교불가"

    if rule_type == "numeric":
        our_num = _parse_amount_won(our_val)
        comp_num = _parse_amount_won(comp_val)
        if our_num is None or comp_num is None:
            return "동일" if our_val.strip() == comp_val.strip() else "비교불가"
        higher = rule.get("higher_is_better", True)
        if our_num == comp_num:
            return "동일"
        if higher:
            return "당사우위" if our_num > comp_num else "타사우위"
        return "당사우위" if our_num < comp_num else "타사우위"

    if rule_type == "rank":
        rank_list = rule.get("rank", [])
        our_rank = _find_rank(our_val, rank_list)
        comp_rank = _find_rank(comp_val, rank_list)
        if our_rank is None or comp_rank is None:
            return "동일" if our_val.strip() == comp_val.strip() else "비교불가"
        if our_rank == comp_rank:
            return "동일"
        higher = rule.get("higher_rank_is_better", True)
        if higher:
            return "당사우위" if our_rank > comp_rank else "타사우위"
        return "당사우위" if our_rank < comp_rank else "타사우위"

    if rule_type == "none_is_better":
        our_empty = _is_empty_or_none(our_val)
        comp_empty = _is_empty_or_none(comp_val)
        if our_empty and comp_empty:
            return "동일"
        if our_empty:
            return "당사우위"
        if comp_empty:
            return "타사우위"
        return "동일" if our_val.strip() == comp_val.strip() else "비교불가"

    # display_only
    return "동일" if our_val.strip() == comp_val.strip() else "상이"


def _find_rank(value: str, rank_list: list[str]) -> int | None:
    """rank_list에서 value와 가장 유사한 항목의 인덱스 반환."""
    normalized = re.sub(r"\s+", "", value)
    for i, item in enumerate(rank_list):
        if re.sub(r"\s+", "", item) == normalized:
            return i
    for i, item in enumerate(rank_list):
        if re.sub(r"\s+", "", item) in normalized or normalized in re.sub(r"\s+", "", item):
            return i
    return None


def _is_empty_or_none(val: str) -> bool:
    return val.strip() in ("", "없음", "—", "-", "해당없음")


# ---------------------------------------------------------------------------
# Pair comparison
# ---------------------------------------------------------------------------

def _get_slot_val(row: dict | None, slot_key: str) -> str:
    """row의 slots에서 슬롯 값을 꺼냄. slots 없으면 raw 필드 fallback."""
    if not row:
        return ""
    slots = row.get("slots")
    if slots and isinstance(slots, dict):
        val = slots.get(slot_key, "")
        if val:
            return str(val)

    # fallback to raw fields
    _FALLBACK = {
        "trigger": "trigger",
        "start_condition": "waiting_period",
        "payment_freq": "amount_condition",
        "payment_limit": "coverage_limit",
        "reduction_rule": "reduction_note",
    }
    if slot_key == "amount_display":
        return _build_amount_display(row)
    raw_field = _FALLBACK.get(slot_key, "")
    if raw_field:
        return str(row.get(raw_field, ""))
    return ""


def _build_amount_display(row: dict | None) -> str:
    """표시용 금액 문자열 생성.

    amount_detail이 있으면 조건별 금액을 '조건 금액' 형태로 줄바꿈 나열.
    단일 금액이면 amount + amount_condition 조합.
    """
    if not row:
        return ""
    detail = _parse_amount_detail(row)
    if detail:
        parts = []
        seen = set()
        for d in detail:
            amt = d.get("amount", "").strip()
            cond = d.get("condition", "").strip()
            trigger = d.get("trigger", "").strip()
            if not amt:
                continue
            # trigger가 여러 개인 경우 trigger를 prefix로
            prefix = f"[{trigger}] " if trigger else ""
            line = f"{prefix}{cond} {amt}".strip() if cond else f"{prefix}{amt}"
            if line not in seen:
                seen.add(line)
                parts.append(line)
        if parts:
            return "\n".join(parts)
        # 파싱 실패 시 첫 항목 그대로
        return detail[0].get("amount", "") or row.get("amount", "")
    amt = row.get("amount", "")
    cond = row.get("amount_condition", "")
    if cond and amt and cond not in amt:
        return f"{cond} {amt}"
    return amt


# ---------------------------------------------------------------------------
# AmountInfo: 조건 인식 금액 파싱
# ---------------------------------------------------------------------------

# 기간 제한 조건 패턴 (1년미만, 2년이내, 계약일부터 N년 이내 등)
_RE_PERIOD_LIMIT = re.compile(
    r"(\d+년\s*(미만|이내|이전|이후))|"
    r"(최초\s*계약.*?\d+년\s*(이내|이전|이후))|"
    r"(계약일부터\s*\d+년)"
)
# 지급 주기 패턴 (판정에서 조건으로 취급 안 함)
_RE_PERIODIC = re.compile(r"매년|매월|매회|연간|연\s*\d+회|매\s*\d+년")


@dataclass
class AmountInfo:
    """비교 판정용 금액 파싱 결과."""
    value: int | None       # 비교 대표 금액 (만원 단위, None이면 숫자 파싱 불가)
    is_conditional: bool    # True = 기간/조건부 지급 (복수 조건 존재)
    condition_note: str     # 조건 요약 문자열 (rationale 표시용)
    detail: list[dict]      # amount_detail 전체 항목 (조건별 금액 목록)


def _parse_amount_detail(row: dict | None) -> list[dict]:
    """amount_detail JSON 파싱. 실패 시 빈 리스트."""
    if not row:
        return []
    detail_raw = row.get("amount_detail", "")
    if not detail_raw:
        return []
    try:
        detail = json.loads(detail_raw) if isinstance(detail_raw, str) else detail_raw
        return detail if isinstance(detail, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _extract_amount_info(row: dict | None) -> AmountInfo:
    """row에서 AmountInfo를 추출한다.

    판정 원칙:
    - amount_detail이 복수 항목이면 조건부로 취급 → 조건별 금액 전체 보존
    - amount_condition에 기간 제한 패턴이 있어도 동일하게 조건부 처리
    - 조건부 금액은 최대값 대신 전체 detail을 그대로 전달
    """
    if not row:
        return AmountInfo(value=None, is_conditional=False, condition_note="", detail=[])

    main_amt_str = row.get("amount", "")
    main_cond = row.get("amount_condition", "").strip()
    detail = _parse_amount_detail(row)

    # 조건부 여부: detail 복수 항목이거나 amount_condition에 기간 제한 패턴
    is_conditional = False
    condition_note = ""

    if len(detail) > 1:
        is_conditional = True
        # 조건 요약: detail의 condition 값들을 나열
        conds = [d.get("condition", "").strip() for d in detail if d.get("condition", "").strip()]
        condition_note = " / ".join(conds[:3])  # 최대 3개만 표시
    elif main_cond:
        if _RE_PERIOD_LIMIT.search(main_cond):
            is_conditional = True
            condition_note = main_cond[:30].rstrip()
        elif not _RE_PERIODIC.search(main_cond):
            is_conditional = True
            condition_note = main_cond[:30].rstrip()

    # 대표 비교 금액: 조건부면 None (금액 단순 비교 불가), 단일이면 파싱
    if is_conditional and len(detail) > 1:
        value = None  # 조건별로 다르므로 단일 비교값 없음
    else:
        value = _parse_amount_won(main_amt_str)

    return AmountInfo(
        value=value,
        is_conditional=is_conditional,
        condition_note=condition_note,
        detail=detail,
    )


def _compare_amounts(our: AmountInfo, comp: AmountInfo) -> tuple[str, str]:
    """두 AmountInfo를 비교하여 (advantage, rationale)을 반환.

    판정 계층:
    1. 어느 한쪽이라도 조건부(복수 조건) → 조건상이
       - 조건별 금액이 다르면 단순 비교 불가이므로 전체 내용을 UI에서 직접 확인
    2. 둘 다 단일 금액 → 숫자 크기 비교
       - 한쪽만 조건부(단일 기간조건) → 조건 없는 쪽 우위
    """
    # 조건부(복수 detail) 항목이 한쪽이라도 있으면 조건상이
    if our.is_conditional or comp.is_conditional:
        our_tag = f"당사: {our.condition_note}" if our.condition_note else "당사: 조건부"
        comp_tag = f"타사: {comp.condition_note}" if comp.condition_note else "타사: 조건부"
        if our.is_conditional and comp.is_conditional:
            return "조건상이", f"{our_tag} / {comp_tag}"
        if our.is_conditional:
            return "조건상이", f"{our_tag}"
        return "조건상이", f"{comp_tag}"

    our_v = our.value
    comp_v = comp.value

    if our_v is None and comp_v is None:
        return "비교불가", ""
    if our_v is None:
        return "타사우위", "금액↓"
    if comp_v is None:
        return "당사우위", "금액↑"

    if our_v > comp_v:
        return "당사우위", "금액↑"
    if our_v < comp_v:
        return "타사우위", "금액↓"
    return "동일", ""


def _compare_pair(pair: MatchedPair, rules: dict) -> ComparedPair:
    """MatchedPair → ComparedPair (슬롯별 비교 포함)."""
    cp = ComparedPair(
        canonical_key=pair.canonical_key,
        our_name=pair.our_name or (pair.our_row or {}).get("benefit_name", ""),
        comp_name=pair.comp_name or (pair.comp_row or {}).get("benefit_name", ""),
        our_amount=_build_amount_display(pair.our_row),
        comp_amount=_build_amount_display(pair.comp_row),
        match_type=pair.match_type,
        our_row=pair.our_row,
        comp_row=pair.comp_row,
    )

    if pair.match_type != "matched":
        cp.overall_advantage = "당사단독" if pair.match_type == "our_only" else "타사단독"
        return cp

    slot_comparisons: list[SlotComparison] = []
    amt_rule = rules.get("amount_display", {"type": "numeric", "higher_is_better": True, "label": "금액"})

    # ── 금액 판정 (AmountInfo 기반) ──
    our_info = _extract_amount_info(pair.our_row)
    comp_info = _extract_amount_info(pair.comp_row)
    adv, rationale = _compare_amounts(our_info, comp_info)

    cp.overall_advantage = adv
    cp.rationale = rationale

    # SlotComparison용 표시 값: 조건부면 detail 최대값 기준 금액 사용
    our_display_val = _build_amount_display(pair.our_row)
    comp_display_val = _build_amount_display(pair.comp_row)
    slot_comparisons.append(SlotComparison(
        dimension="amount_display",
        label=amt_rule.get("label", "금액"),
        our_value=our_display_val,
        comp_value=comp_display_val,
        advantage=adv,
    ))

    # ── 나머지 슬롯은 display_only로 참고 표시 ──
    for dim, rule in rules.items():
        if dim == "amount_display":
            continue
        our_val = _get_slot_val(pair.our_row, dim)
        comp_val = _get_slot_val(pair.comp_row, dim)
        slot_comparisons.append(SlotComparison(
            dimension=dim,
            label=rule.get("label", dim),
            our_value=our_val,
            comp_value=comp_val,
            advantage="동일" if our_val.strip() == comp_val.strip() else "상이",
        ))

    cp.slot_comparisons = slot_comparisons
    return cp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_comparison(our_rows: list[dict], comp_rows: list[dict]) -> ComparisonResult:
    """당사/타사 comparison rows → ComparisonResult.

    UI에서 이 함수 하나만 호출하면 됨. LLM 호출 없음.
    """
    rules = _load_rules()
    match_result: MatchResult = match_benefits(our_rows, comp_rows)

    compared: list[ComparedPair] = []
    only_our: list[ComparedPair] = []
    only_comp: list[ComparedPair] = []

    for pair in match_result.pairs:
        cp = _compare_pair(pair, rules)
        if cp.match_type == "matched":
            compared.append(cp)
        elif cp.match_type == "our_only":
            only_our.append(cp)
        else:
            only_comp.append(cp)

    # slot_table: Card 2 용 — 매칭된 쌍의 슬롯별 비교 행
    slot_table = _build_slot_table(compared)

    # amount_table: Card 3 용 — 전체 급부 금액 비교
    amount_table = _build_amount_table(compared, only_our, only_comp)

    # coverage_summary: Card 1 용 — canonical key 기반 질병 분류 집계
    coverage_summary = _build_coverage_summary(match_result)

    # summary: sticky bar 용
    diff_count = sum(1 for cp in compared if cp.overall_advantage in ("당사우위", "타사우위", "금액상이", "조건상이"))
    summary = {
        "our_only": len(only_our),
        "comp_only": len(only_comp),
        "matched": len(compared),
        "diff": diff_count,
        "total": len(match_result.pairs),
    }

    return ComparisonResult(
        pairs=compared,
        only_our=only_our,
        only_comp=only_comp,
        slot_table=slot_table,
        amount_table=amount_table,
        coverage_summary=coverage_summary,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------

def _build_slot_table(compared: list[ComparedPair]) -> list[dict]:
    """매칭된 쌍들의 슬롯별 비교를 flat table로 변환."""
    rows: list[dict] = []
    for cp in compared:
        for sc in cp.slot_comparisons:
            rows.append({
                "benefit": cp.our_name or cp.comp_name,
                "canonical_key": cp.canonical_key,
                "dimension": sc.dimension,
                "label": sc.label,
                "our_value": sc.our_value,
                "comp_value": sc.comp_value,
                "advantage": sc.advantage,
            })
    return rows


def _build_amount_table(
    compared: list[ComparedPair],
    only_our: list[ComparedPair],
    only_comp: list[ComparedPair],
) -> list[dict]:
    """전체 급부 금액 비교표."""
    rows: list[dict] = []
    for cp in compared:
        rows.append({
            "canonical_key": cp.canonical_key,
            "our_name": cp.our_name,
            "comp_name": cp.comp_name,
            "our_amount": cp.our_amount,
            "comp_amount": cp.comp_amount,
            "status": cp.overall_advantage,
            "rationale": cp.rationale,
        })
    for cp in only_our:
        rows.append({
            "canonical_key": cp.canonical_key,
            "our_name": cp.our_name,
            "comp_name": "—",
            "our_amount": cp.our_amount,
            "comp_amount": "—",
            "status": "당사단독",
            "rationale": "",
        })
    for cp in only_comp:
        rows.append({
            "canonical_key": cp.canonical_key,
            "our_name": "—",
            "comp_name": cp.comp_name,
            "our_amount": "—",
            "comp_amount": cp.comp_amount,
            "status": "타사단독",
            "rationale": "",
        })
    return rows


def rebuild_amount_table(cr: ComparisonResult) -> None:
    """resolve_mixed_pairs 이후 amount_table을 재빌드합니다."""
    cr.amount_table = _build_amount_table(cr.pairs, cr.only_our, cr.only_comp)


def _build_coverage_summary(match_result: MatchResult) -> dict:
    """canonical key에서 disease 슬롯을 추출하여 질병 분류별 집계."""
    disease_counts: dict[str, dict[str, int]] = {}

    for pair in match_result.pairs:
        parts = pair.canonical_key.split("|")
        disease = ""
        for p in parts:
            if p in _DISEASE_LABELS:
                disease = p
                break
        if not disease:
            disease = "기타"

        if disease not in disease_counts:
            disease_counts[disease] = {"our": 0, "comp": 0, "matched": 0}

        if pair.match_type == "matched":
            disease_counts[disease]["matched"] += 1
            disease_counts[disease]["our"] += 1
            disease_counts[disease]["comp"] += 1
        elif pair.match_type == "our_only":
            disease_counts[disease]["our"] += 1
        else:
            disease_counts[disease]["comp"] += 1

    return disease_counts


_DISEASE_LABELS = frozenset({
    "일반암", "갑상선암", "기타피부암", "제자리암", "경계성종양",
    "전이암", "유방암", "전립선암", "대장암", "대장점막내암",
    "위암", "식도암", "간암", "췌장암", "폐암",
    "고액암", "3대암", "소액질병", "여성생식기암", "남성특화암",
    "두경부암", "소장대장항문암", "간담낭담도암췌장암",
    "양성신생물", "특정암", "여성암", "중증갑상선암",
    "유방전립선암", "갑상선암전립선암",
    "갑상선질환", "여성유방질환", "여성생식기질환",
})
