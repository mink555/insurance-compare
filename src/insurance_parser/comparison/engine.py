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
    insight: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Slot comparison logic
# ---------------------------------------------------------------------------

_RE_NUMBER = re.compile(r"[\d,]+")
_RE_AMOUNT_WON = re.compile(r"([\d,]+)\s*만\s*원")
# 지급한도 파싱: "최대 N년", "최초 N회", "연간 N회" 등에서 단위와 숫자 추출
_RE_LIMIT = re.compile(r"(\d+)\s*(년|회)")
_RE_LIMIT_UNIT_LABEL = re.compile(r"(최대|최초|연간|매년|매회)")


def _parse_limit(text: str) -> tuple[str | None, int | None]:
    """지급한도 문자열 파싱 → (단위_키, 숫자).

    '최대 5년' → ('년', 5)
    '최초 1회' → ('최초_회', 1)   — 최초와 연간은 의미가 달라 다른 단위로 취급
    '연간 1회' → ('연간_회', 1)
    '연간1회'  → ('연간_회', 1)
    파싱 실패 → (None, None)
    """
    if not text:
        return None, None
    normalized = re.sub(r"\s+", "", text)
    m = _RE_LIMIT.search(normalized)
    if not m:
        return None, None
    num = int(m.group(1))
    unit_raw = m.group(2)  # '년' or '회'
    if unit_raw == "년":
        return "년", num
    # 회: 최초/연간/매 구분
    if "최초" in normalized:
        return "최초_회", num
    if "연간" in normalized or "매년" in normalized:
        return "연간_회", num
    return "회", num


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

    # numeric / limit_numeric: 한쪽 비어있으면 있는 쪽 우위
    # none_is_better: 비어있는 쪽이 유리 → 아래 분기에서 처리
    # rank / display_only: 한쪽 비어있으면 비교불가
    if rule_type in ("numeric", "limit_numeric"):
        if not our_val:
            return "타사우위"
        if not comp_val:
            return "당사우위"
    elif rule_type != "none_is_better":
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
        # 둘 다 있으면: 내용 비교 시도 (감액기간 숫자 추출)
        our_num = _parse_amount_won(our_val)
        comp_num = _parse_amount_won(comp_val)
        if our_num is not None and comp_num is not None:
            # 감액기간: 숫자 클수록 불리 (1년 감액 > 2년 감액은 2년이 더 불리)
            if our_num == comp_num:
                return "동일"
            return "당사우위" if our_num < comp_num else "타사우위"
        # 숫자 추출 실패 시: 텍스트가 같으면 동일, 다르면 표시만
        return "동일" if re.sub(r"\s+", "", our_val) == re.sub(r"\s+", "", comp_val) else "비교불가"

    if rule_type == "limit_numeric":
        # 단위 추출 후 같은 단위끼리만 숫자 비교
        our_unit, our_num = _parse_limit(our_val)
        comp_unit, comp_num = _parse_limit(comp_val)
        if our_unit is None or comp_unit is None:
            return "동일" if re.sub(r"\s+", "", our_val) == re.sub(r"\s+", "", comp_val) else "비교불가"
        if our_unit != comp_unit:
            # 단위 다름 (예: 년 vs 회) — 비교 불가
            return "비교불가"
        if our_num == comp_num:
            return "동일"
        higher = rule.get("higher_is_better", True)
        if higher:
            return "당사우위" if our_num > comp_num else "타사우위"
        return "당사우위" if our_num < comp_num else "타사우위"

    # display_only — 표시만, 판정 없음
    return "동일" if re.sub(r"\s+", "", our_val) == re.sub(r"\s+", "", comp_val) else "표시"


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


def _shorten_condition(cond: str) -> str:
    """조건 문자열에서 기간 키워드만 추출. 추출 불가 시 앞 15자 truncate.

    공백이 불규칙하게 들어오는 경우(예: '계약일 부터 2년 이내')도 처리하기 위해
    먼저 공백을 정규화한 뒤 패턴 매칭.
    '2년 후' -> '2년이후' 로 표준화.
    """
    if not cond:
        return ""
    # 공백 정규화 (연속 공백 → 단일 공백, 양끝 제거)
    normalized = re.sub(r"\s+", " ", cond).strip()
    m = _RE_PERIOD_SHORT.search(normalized)
    if m:
        result = m.group(1).replace(" ", "")
        # '2년후' → '2년이후' 표준화
        if result.endswith("후") and not result.endswith("이후"):
            result = result[:-1] + "이후"
        return result
    return normalized[:15].rstrip() + ("…" if len(normalized) > 15 else "")


def _build_amount_display(row: dict | None) -> str:
    """표시용 금액 문자열 생성.

    amount_detail이ㄱ 있으면 기간 조건 기준으로 그룹핑해서 표시.
    - 같은 기간 조건의 금액이 모두 같으면: '1년미만 100만원'
    - 기간 조건은 같은데 trigger별로 금액 다르면: '1년미만 100만원~200만원'
    단일 금액이면 amount + amount_condition 조합.
    """
    if not row:
        return ""
    detail = _parse_amount_detail(row)
    if detail:
        from collections import defaultdict
        groups: dict[str, list[int]] = defaultdict(list)
        order: list[str] = []
        for d in detail:
            amt_str = d.get("amount", "").strip()
            cond_raw = d.get("condition", "")
            cond = _shorten_condition(cond_raw) if cond_raw else ""
            key = cond or "조건없음"
            parsed = _parse_amount_won(amt_str)
            if parsed is not None:
                if key not in groups:
                    order.append(key)
                groups[key].append(parsed)
        if groups:
            parts = []
            for key in order:
                vals = sorted(set(groups[key]))
                amt_str = (
                    f"{vals[0] // 10000:,}만원"
                    if len(vals) == 1
                    else f"{vals[0] // 10000:,}만원~{vals[-1] // 10000:,}만원"
                )
                label = "" if key == "조건없음" else f"{key} "
                parts.append(f"{label}{amt_str}")
            return "\n".join(parts)
        return detail[0].get("amount", "") or row.get("amount", "")
    amt = row.get("amount", "")
    cond = row.get("amount_condition", "")
    if cond and amt and cond not in amt:
        return f"{_shorten_condition(cond) or cond} {amt}".strip()
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
# 표시용 조건 단축 패턴 (기간 키워드만 추출, 공백 포함 변형 및 '후' 단독 케이스 대응)
_RE_PERIOD_SHORT = re.compile(r"(\d+년\s*(?:미만|이상|이내|이후|초과|후))")


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

    대표값(value) 원칙:
    - 단일 금액이면 그 금액
    - 복수 조건(amount_detail)이면 그 중 최대 금액을 대표값으로 사용
      (비교 시 "상대방이 가장 유리한 조건에서 받을 수 있는 최대금액" 기준)
    - is_conditional: 복수 조건이 있음을 표시 (rationale에서 주석으로 활용)
    """
    if not row:
        return AmountInfo(value=None, is_conditional=False, condition_note="", detail=[])

    main_amt_str = row.get("amount", "")
    main_cond = row.get("amount_condition", "").strip()
    detail = _parse_amount_detail(row)

    is_conditional = False
    condition_note = ""

    if len(detail) > 1:
        is_conditional = True
        # 표시용 조건 요약: 기간만 추출해서 나열
        conds = [_shorten_condition(d.get("condition", "")) for d in detail if d.get("condition", "").strip()]
        conds = [c for c in conds if c]
        condition_note = " / ".join(dict.fromkeys(conds))  # 중복 제거 + 순서 유지
    elif main_cond:
        if _RE_PERIOD_LIMIT.search(main_cond) or not _RE_PERIODIC.search(main_cond):
            is_conditional = True
            condition_note = _shorten_condition(main_cond) or main_cond[:20].rstrip()

    # 대표 비교 금액: 복수 detail이면 최대값, 아니면 main amount
    if detail:
        parsed = [_parse_amount_won(d.get("amount", "")) for d in detail]
        parsed = [v for v in parsed if v is not None]
        value = max(parsed) if parsed else _parse_amount_won(main_amt_str)
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

    판정 원칙:
    - 금액은 항상 비교 가능한 대표값(최대값 기준)으로 비교
    - 금액이 다르면 높은 쪽 우위 (조건 여부와 무관)
    - 금액이 같고 한쪽만 조건부이면 조건 없는 쪽 우위
    - 금액이 같고 둘 다 조건부이면 조건상이
    - 금액 없으면 비교불가
    """
    our_v = our.value
    comp_v = comp.value

    if our_v is None and comp_v is None:
        return "비교불가", ""
    if our_v is None:
        return "타사우위", "금액↓"
    if comp_v is None:
        return "당사우위", "금액↑"

    our_cond = f"({our.condition_note})" if our.is_conditional and our.condition_note else ""
    comp_cond = f"({comp.condition_note})" if comp.is_conditional and comp.condition_note else ""

    if our_v > comp_v:
        note = f"금액↑"
        if comp.is_conditional:
            note += f" · 타사최대{comp_v // 10000:,}만원{comp_cond}"
        return "당사우위", note
    if our_v < comp_v:
        note = f"금액↓"
        if our.is_conditional:
            note += f" · 당사최대{our_v // 10000:,}만원{our_cond}"
        return "타사우위", note

    # 금액 동일
    if our.is_conditional and comp.is_conditional:
        if our.condition_note != comp.condition_note:
            return "조건상이", f"당사{our_cond} / 타사{comp_cond}"
        return "동일", ""
    if our.is_conditional:
        return "타사우위", f"조건없음 (당사{our_cond})"
    if comp.is_conditional:
        return "당사우위", f"조건없음 (타사{comp_cond})"
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
        adv_slot = _compare_slot(dim, our_val, comp_val, rule)
        slot_comparisons.append(SlotComparison(
            dimension=dim,
            label=rule.get("label", dim),
            our_value=our_val,
            comp_value=comp_val,
            advantage=adv_slot,
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

    result = ComparisonResult(
        pairs=compared,
        only_our=only_our,
        only_comp=only_comp,
        slot_table=slot_table,
        amount_table=amount_table,
        coverage_summary=coverage_summary,
        summary=summary,
    )
    result.insight = build_insight_summary(result)
    return result


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
    """canonical key에서 disease 슬롯을 추출하여 질병 분류별 집계.

    복합 disease 슬롯(예: '기타피부암갑상선암복합')은 포함된 개별 질병에도 함께 집계.
    """
    disease_counts: dict[str, dict[str, int]] = {}

    # 복합 슬롯 → 포함 disease 목록 매핑
    _COMPOSITE_DISEASE_MAP: dict[str, list[str]] = {
        "기타피부암갑상선암복합": ["기타피부암", "갑상선암"],
        "유방전립선암": ["유방암", "전립선암"],
        "갑상선암전립선암": ["갑상선암", "전립선암"],
    }

    def _get_diseases(canonical_key: str) -> list[str]:
        parts = canonical_key.split("|")
        for p in parts:
            if p in _COMPOSITE_DISEASE_MAP:
                return _COMPOSITE_DISEASE_MAP[p]
            if p in _DISEASE_LABELS:
                return [p]
        return ["기타"]

    def _inc(disease: str, side: str, is_matched: bool) -> None:
        if disease not in disease_counts:
            disease_counts[disease] = {"our": 0, "comp": 0, "matched": 0}
        disease_counts[disease][side] += 1
        if is_matched:
            disease_counts[disease]["matched"] += 1

    for pair in match_result.pairs:
        diseases = _get_diseases(pair.canonical_key)
        if pair.match_type == "matched":
            for d in diseases:
                _inc(d, "our", True)
                _inc(d, "comp", True)
        elif pair.match_type == "our_only":
            for d in diseases:
                _inc(d, "our", False)
        else:
            for d in diseases:
                _inc(d, "comp", False)

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


# ---------------------------------------------------------------------------
# Insight summary (설계사·관리자·상품개발 관점 한줄평 + Key Selling Points)
# ---------------------------------------------------------------------------

def build_insight_summary(
    result: ComparisonResult,
    our_label: str = "당사",
    comp_label: str = "타사",
) -> dict:
    """ComparisonResult에서 순수 규칙 기반 인사이트 요약을 생성한다.

    LLM 호출 없음. 계산된 수치만 문장 템플릿에 삽입 → 할루시네이션 0%.

    반환:
        headline      — 한줄 포지션 평가 (수치 근거 포함)
        position      — "우위" | "열위" | "혼재" | "단독보장중심"
        key_points    — [{"type": str, "label": str, "desc": str, "badges": [str]}]
                        type: "strength" | "weakness" | "gap" | "condition"
        score         — {our_adv: int, comp_adv: int, equal: int, matched: int}
        top_gaps      — 금액 격차 상위 3건 [{name, our_amt, comp_amt, gap_pct, side}]
        our_only_cats — 당사 단독 보장 카테고리별 집계 {cat: count}
        comp_only_cats — 타사 단독 보장 카테고리별 집계 {cat: count}
        cat_score     — 카테고리별 우위 집계 {cat: {우:n, 열:n, 동:n}}
    """
    from collections import defaultdict

    matched = result.pairs
    only_our = result.only_our
    only_comp = result.only_comp

    our_adv  = [cp for cp in matched if cp.overall_advantage == "당사우위"]
    comp_adv = [cp for cp in matched if cp.overall_advantage == "타사우위"]
    cond_diff = [cp for cp in matched if cp.overall_advantage == "조건상이"]
    equal    = [cp for cp in matched if cp.overall_advantage == "동일"]

    n_matched = len(matched)
    n_our  = len(our_adv)
    n_comp = len(comp_adv)
    our_pct  = n_our  / n_matched * 100 if n_matched else 0
    comp_pct = n_comp / n_matched * 100 if n_matched else 0

    # ── 포지션 판정 ──────────────────────────────────────────
    if n_matched == 0:
        position = "단독보장중심"
    elif n_our > n_comp and our_pct >= 50:
        position = "우위"
    elif n_comp > n_our and comp_pct >= 50:
        position = "열위"
    else:
        position = "혼재"

    # ── 한줄평 ───────────────────────────────────────────────
    if n_matched == 0:
        headline = (
            f"매칭 급부 없음 — "
            f"{our_label} {len(only_our)}건 단독 / {comp_label} {len(only_comp)}건 단독"
        )
    else:
        pct_str = f"{our_pct:.0f}%"
        diff_str = ""
        if only_our:
            diff_str += f" · {our_label} 단독 {len(only_our)}건"
        if only_comp:
            diff_str += f" · {comp_label} 단독 {len(only_comp)}건"
        headline = (
            f"매칭 {n_matched}건 중 {our_label}우위 {n_our}건({pct_str}) "
            f"/ {comp_label}우위 {n_comp}건({comp_pct:.0f}%)"
            f"{diff_str}"
        )

    # ── 금액 격차 Top 3 ──────────────────────────────────────
    gaps: list[dict] = []
    for cp in matched:
        if cp.overall_advantage not in ("당사우위", "타사우위"):
            continue
        our_v  = _parse_amount_won(cp.our_amount or "")
        comp_v = _parse_amount_won(cp.comp_amount or "")
        if not our_v or not comp_v or comp_v == 0:
            continue
        gap_pct = (our_v - comp_v) / comp_v * 100
        gaps.append({
            "name":     cp.our_name or cp.comp_name,
            "our_amt":  f"{our_v // 10000:,}만원",
            "comp_amt": f"{comp_v // 10000:,}만원",
            "gap_pct":  round(gap_pct),
            "side":     cp.overall_advantage,  # "당사우위" or "타사우위"
        })
    gaps.sort(key=lambda x: abs(x["gap_pct"]), reverse=True)
    top_gaps = gaps[:3]

    # ── 카테고리별 우위 집계 ──────────────────────────────────
    cat_score: dict[str, dict] = defaultdict(lambda: {"우": 0, "열": 0, "동": 0})
    for cp in matched:
        row = cp.our_row or cp.comp_row or {}
        cat = row.get("benefit_category_ko") or "기타"
        if cp.overall_advantage == "당사우위":
            cat_score[cat]["우"] += 1
        elif cp.overall_advantage == "타사우위":
            cat_score[cat]["열"] += 1
        else:
            cat_score[cat]["동"] += 1

    # ── 단독 보장 카테고리별 집계 ────────────────────────────
    def _count_cats(pairs: list[ComparedPair], side: str) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for cp in pairs:
            row = (cp.our_row if side == "our" else cp.comp_row) or {}
            cat = row.get("benefit_category_ko") or "기타"
            counts[cat] += 1
        return dict(counts)

    our_only_cats  = _count_cats(only_our,  "our")
    comp_only_cats = _count_cats(only_comp, "comp")

    # ── 조건 우위 (지급한도 / 감액) ──────────────────────────
    limit_adv  = []  # payment_limit 당사우위
    limit_disadv = []  # payment_limit 타사우위
    reduc_adv  = []  # reduction_rule 당사우위 (감액 없음)
    reduc_disadv = []  # reduction_rule 타사우위 (당사만 감액)

    for cp in matched:
        for sc in cp.slot_comparisons:
            if sc.dimension == "payment_limit":
                if sc.advantage == "당사우위":
                    limit_adv.append({"name": cp.our_name, "our": sc.our_value, "comp": sc.comp_value})
                elif sc.advantage == "타사우위":
                    limit_disadv.append({"name": cp.our_name, "our": sc.our_value, "comp": sc.comp_value})
            elif sc.dimension == "reduction_rule":
                if sc.advantage == "당사우위":
                    reduc_adv.append({"name": cp.our_name, "our": sc.our_value, "comp": sc.comp_value})
                elif sc.advantage == "타사우위":
                    reduc_disadv.append({"name": cp.our_name, "our": sc.our_value, "comp": sc.comp_value})

    # ── Key Points 조립 ──────────────────────────────────────
    key_points: list[dict] = []

    # 1. 금액 강점
    if our_adv:
        top = sorted(
            [g for g in top_gaps if g["side"] == "당사우위"],
            key=lambda x: abs(x["gap_pct"]), reverse=True,
        )
        if top:
            best = top[0]
            badges = [f"{our_label}우위 {n_our}건"]
            desc = (
                f"{our_label} {n_our}건에서 금액 우위. "
                f"최대 격차: {best['name']} ({best['our_amt']} vs {best['comp_amt']}, "
                f"{best['gap_pct']:+d}%)"
            )
        else:
            badges = [f"{our_label}우위 {n_our}건"]
            desc = f"매칭 급부 {n_matched}건 중 {n_our}건에서 {our_label} 금액이 높음"
        key_points.append({"type": "strength", "label": "금액 우위", "desc": desc, "badges": badges})

    # 2. 당사 단독 보장
    if only_our:
        cats_str = " · ".join(
            f"{cat} {cnt}건" for cat, cnt in sorted(our_only_cats.items(), key=lambda x: -x[1])
        )
        key_points.append({
            "type":   "strength",
            "label":  f"{our_label} 단독 보장",
            "desc":   f"{comp_label}에 없는 {our_label}만의 급부 {len(only_our)}건 ({cats_str})",
            "badges": [f"단독 {len(only_our)}건"],
        })

    # 3. 조건 우위 (지급한도·감액)
    cond_msgs = []
    if limit_adv:
        cond_msgs.append(f"지급한도 {our_label}우위 {len(limit_adv)}건 ({limit_adv[0]['our']} > {limit_adv[0]['comp']})")
    if reduc_adv:
        cond_msgs.append(f"감액조건 없음 {our_label}우위 {len(reduc_adv)}건")
    if cond_msgs:
        key_points.append({
            "type":   "strength",
            "label":  "조건 우위",
            "desc":   " / ".join(cond_msgs),
            "badges": ["조건유리"],
        })

    # 4. 금액 열위
    if comp_adv:
        top_w = sorted(
            [g for g in top_gaps if g["side"] == "타사우위"],
            key=lambda x: abs(x["gap_pct"]), reverse=True,
        )
        badges = [f"{comp_label}우위 {n_comp}건"]
        if top_w:
            worst = top_w[0]
            desc = (
                f"{comp_label} {n_comp}건에서 금액 우위. "
                f"최대 격차: {worst['name']} ({worst['comp_amt']} vs {worst['our_amt']}, "
                f"{-worst['gap_pct']:+d}%)"
            )
        else:
            desc = f"매칭 급부 {n_matched}건 중 {n_comp}건에서 {comp_label} 금액이 높음"
        key_points.append({"type": "weakness", "label": "금액 열위", "desc": desc, "badges": badges})

    # 5. 조건 열위 (감액 불리)
    cond_weak = []
    if limit_disadv:
        cond_weak.append(f"지급한도 {comp_label}우위 {len(limit_disadv)}건")
    if reduc_disadv:
        cond_weak.append(f"감액조건 {comp_label}유리 {len(reduc_disadv)}건 ({reduc_disadv[0]['name']})")
    if cond_weak:
        key_points.append({
            "type":   "weakness",
            "label":  "조건 열위",
            "desc":   " / ".join(cond_weak),
            "badges": ["조건불리"],
        })

    # 6. 갭 분석 (상품개발용)
    if only_comp:
        cats_str = " · ".join(
            f"{cat} {cnt}건" for cat, cnt in sorted(comp_only_cats.items(), key=lambda x: -x[1])[:4]
        )
        key_points.append({
            "type":  "gap",
            "label": "보장 갭 (도입 검토)",
            "desc":  f"{comp_label} 단독 {len(only_comp)}건 미보장 — {cats_str}",
            "badges": [f"갭 {len(only_comp)}건"],
        })

    return {
        "headline":      headline,
        "position":      position,
        "key_points":    key_points,
        "score":         {"our_adv": n_our, "comp_adv": n_comp, "equal": len(equal), "cond_diff": len(cond_diff), "matched": n_matched},
        "top_gaps":      top_gaps,
        "our_only_cats": our_only_cats,
        "comp_only_cats": comp_only_cats,
        "cat_score":     dict(cat_score),
    }
