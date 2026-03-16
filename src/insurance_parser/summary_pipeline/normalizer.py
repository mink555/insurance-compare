"""Stage 2: 정규화 (parse raw dict → CanonicalBenefit 리스트).
Stage 4: 내보내기 (CanonicalBenefit 리스트 → SummaryRow 리스트).

설계 원칙:
- flatten()에는 의미 분류/예외처리 로직을 넣지 않습니다.
- 분류는 classifier.py (Stage 3), 의미 해석은 이 파일 내 normalize_*() 함수에서만 합니다.
- 회사별 raw dict 구조 차이는 이 파일에서 흡수합니다.
- x좌표/페이지번호/특정 상품명 하드코딩 금지.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from .models import AmountEntry, CanonicalBenefit, SummaryRow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 유틸리티 정규식 (구조 기반, 특정 상품 무관)
# ---------------------------------------------------------------------------
_RE_RENEWAL = re.compile(r"갱신형|비갱신형")
_RE_WAITING = re.compile(
    r"(?:암보장개시일|보장개시일).*?(?:90\s*일|91\s*번째|계약일부터\s*\d+일|갱신일)"
)
_RE_COVERAGE_LIMIT = re.compile(
    r"최초\s*1\s*회|최대\s*\d+\s*년|최대\s*\d+\s*회|연간\s*\d+\s*회|매년\s*\d+\s*회"
)


def _extract_renewal_type(name: str) -> str:
    m = _RE_RENEWAL.search(name)
    return m.group(0) if m else ""


def _extract_waiting_period(trigger: str, notes: list[str]) -> str:
    all_text = trigger + " " + " ".join(notes)
    m = _RE_WAITING.search(all_text)
    return m.group(0)[:80] if m else ""


def _extract_coverage_limit(trigger: str) -> str:
    m = _RE_COVERAGE_LIMIT.search(trigger)
    return m.group(0) if m else ""


# ---------------------------------------------------------------------------
# dedupe_key 생성
# ---------------------------------------------------------------------------

def make_dedupe_key(row: dict) -> str:
    """SummaryRow dict에서 중복 판별용 해시 키를 생성합니다.

    business key: insurer + product_name + contract_name + benefit_name
                  + amount + amount_condition + trigger + source_pdf
    """
    parts = [
        str(row.get("insurer", "") or ""),
        str(row.get("product_name", "") or ""),
        str(row.get("contract_name", "") or ""),
        str(row.get("benefit_name", "") or ""),
        str(row.get("amount", "") or ""),
        str(row.get("amount_condition", "") or ""),
        str(row.get("trigger", "") or "")[:200],
        str(row.get("source_pdf", "") or ""),
    ]
    raw = "|".join(parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Stage 2: raw dict → CanonicalBenefit
# ---------------------------------------------------------------------------

def _normalize_amount_entry(raw: dict) -> AmountEntry:
    return AmountEntry(
        amount=raw.get("amount", ""),
        condition=raw.get("condition", ""),
        reduction_note=raw.get("reduction_note", ""),
    )


def normalize_contract(
    raw_contract: dict,
    company_name: str,
    product_name: str,
) -> list[CanonicalBenefit]:
    """파서 raw contract dict → CanonicalBenefit 리스트.

    benefit_names가 복수인 경우 급부별로 분리합니다 (1급부 = 1행 원칙).
    """
    results: list[CanonicalBenefit] = []

    contract_name = raw_contract.get("name", "")
    contract_type = raw_contract.get("type", "")
    contract_code = raw_contract.get("code", "")
    reference_amount = raw_contract.get("reference_amount", "")
    source_pdf = raw_contract.get("source_pdf", "")
    notes: list[str] = raw_contract.get("notes", [])

    for raw_benefit in raw_contract.get("benefits", []):
        trigger = raw_benefit.get("trigger", "")
        amounts = [_normalize_amount_entry(a) for a in raw_benefit.get("amounts", [])]
        benefit_names: list[str] = raw_benefit.get("benefit_names", [])

        # benefit_names가 비어있으면 빈 이름 1건으로 처리
        if not benefit_names:
            benefit_names = [""]

        for bname in benefit_names:
            results.append(CanonicalBenefit(
                company_name=company_name,
                product_name=product_name,
                contract_type=contract_type,
                contract_name=contract_name,
                contract_code=contract_code,
                reference_amount=reference_amount,
                benefit_name=bname,
                trigger=trigger,
                amounts=amounts,
                notes=notes,
                source_pdf=source_pdf,
                source_type="summary",
            ))

    return results


def normalize_summary_data(
    summary_data: dict,
    company_name: str,
    product_name: str,
) -> list[CanonicalBenefit]:
    """parse_pdf() raw 결과 dict → CanonicalBenefit 리스트.

    라이나/한화 파서 모두 동일한 구조(contracts 리스트)를 사용하므로
    회사 분기 없이 처리합니다.

    지원하는 구조:
      - contracts: list[contract_dict]           (공통)
      - riders: list[contract_dict]              (라이나 번들)
      - riders: list[{"contracts": [...]}]       (라이나 번들 중첩)
    """
    if not summary_data:
        return []

    results: list[CanonicalBenefit] = []

    all_contracts: list[dict] = list(summary_data.get("contracts", []))

    for item in summary_data.get("riders", []):
        if isinstance(item, dict):
            nested = item.get("contracts")
            if nested and isinstance(nested, list):
                all_contracts.extend(nested)
            else:
                all_contracts.append(item)

    # ── [중복 방지] contract 단계 dedup: (name, code, source_pdf) 기준 ──
    seen_contracts: set[tuple] = set()
    deduped_contracts: list[dict] = []
    for contract in all_contracts:
        if not isinstance(contract, dict):
            continue
        key = (
            contract.get("name", ""),
            contract.get("code", ""),
            contract.get("source_pdf", ""),
        )
        if key in seen_contracts:
            logger.debug(
                "[Stage2/normalize] contract 중복 건너뜀: name=%r code=%r src=%r",
                *key,
            )
            continue
        seen_contracts.add(key)
        deduped_contracts.append(contract)

    logger.debug(
        "[Stage2/normalize] %s/%s: all_contracts=%d → deduped=%d",
        company_name, product_name, len(all_contracts), len(deduped_contracts),
    )

    for contract in deduped_contracts:
        results.extend(normalize_contract(contract, company_name, product_name))

    logger.info(
        "[Stage2/normalize] %s/%s: CanonicalBenefit %d건",
        company_name, product_name, len(results),
    )
    return results


# ---------------------------------------------------------------------------
# Stage 4: CanonicalBenefit → SummaryRow
# ---------------------------------------------------------------------------

def export_to_summary_row(benefit: CanonicalBenefit) -> list[SummaryRow]:
    """CanonicalBenefit 1건 → SummaryRow 리스트.

    amounts가 복수(연차별 감액 등)인 경우 각각 1행으로 분리합니다.
    amounts가 없으면 빈 행 1개를 반환합니다.
    각 행에는 dedupe_key가 포함됩니다.
    """
    rows: list[SummaryRow] = []
    notes_summary = " | ".join(benefit.notes)
    renewal_type = _extract_renewal_type(benefit.contract_name)
    waiting_period = _extract_waiting_period(benefit.trigger, benefit.notes)
    coverage_limit = _extract_coverage_limit(benefit.trigger)

    base = dict(
        insurer=benefit.company_name,
        product_name=benefit.product_name,
        contract_type=benefit.contract_type,
        contract_name=benefit.contract_name,
        contract_code=benefit.contract_code,
        reference_amount=benefit.reference_amount,
        benefit_name=benefit.benefit_name,
        insurance_type=benefit.insurance_type,
        benefit_category=benefit.benefit_category,
        benefit_category_ko=benefit.benefit_category_ko,
        trigger=benefit.trigger,
        renewal_type=renewal_type,
        waiting_period=waiting_period,
        coverage_limit=coverage_limit,
        notes_summary=notes_summary,
        source_pdf=benefit.source_pdf,
        source_type=benefit.source_type,
        amount_detail=json.dumps(
            [a.model_dump() for a in benefit.amounts], ensure_ascii=False
        ) if len(benefit.amounts) > 1 else "",
    )

    if not benefit.amounts:
        row_dict = {**base, "amount": "", "amount_condition": "", "reduction_note": ""}
        row_dict["dedupe_key"] = make_dedupe_key(row_dict)
        rows.append(SummaryRow(**row_dict))
        return rows

    for amt in benefit.amounts:
        row_dict = {
            **base,
            "amount": amt.amount,
            "amount_condition": amt.condition,
            "reduction_note": amt.reduction_note,
        }
        row_dict["dedupe_key"] = make_dedupe_key(row_dict)
        rows.append(SummaryRow(**row_dict))

    return rows


def export_to_summary_rows(benefits: list[CanonicalBenefit]) -> list[SummaryRow]:
    """CanonicalBenefit 리스트 → SummaryRow 리스트 (Stage 4 진입점).

    export 직후 dedupe_key 기준 완전 중복 행을 제거합니다.
    이 함수가 반환하는 rows는 "detail rows" — 1 amount condition = 1행.
    비교 화면용 집약 행은 to_comparison_rows()를 사용하세요.
    """
    rows: list[SummaryRow] = []
    for b in benefits:
        rows.extend(export_to_summary_row(b))

    # ── [1차 dedupe] dedupe_key 기준 완전 중복 제거 ──
    before = len(rows)
    seen_keys: set[str] = set()
    deduped: list[SummaryRow] = []
    for row in rows:
        key = row.dedupe_key
        if key in seen_keys:
            logger.debug(
                "[Stage4/export] 중복 행 제거: benefit=%r amount=%r key=%s",
                row.benefit_name, row.amount, key,
            )
            continue
        seen_keys.add(key)
        deduped.append(row)

    after = len(deduped)
    if before != after:
        logger.warning(
            "[Stage4/export] 1차 dedupe: %d → %d행 (%d건 제거)",
            before, after, before - after,
        )
    else:
        logger.info("[Stage4/export] SummaryRow %d건 (중복 없음)", after)

    return deduped


# ---------------------------------------------------------------------------
# 비교 화면용 집약 (comparison rows)
# ---------------------------------------------------------------------------

# comparison_rows grouping key 필드 목록.
# "동일 급부 = 동일 비교 행"을 결정하는 business key.
#
# 설계 원칙:
#   - 너무 좁으면 서로 다른 급부가 합쳐짐 (오집약)
#   - 너무 넓으면 질병군/경과기간별로 행이 다시 분해됨 (집약 효과 없음)
#
# 채택 기준:
#   insurer + product_name + contract_name + benefit_name
#   + benefit_category (진단/수술/치료/입원 — 같은 이름이어도 카테고리 다르면 별개)
#   + renewal_type (갱신형/비갱신형 — 같은 상품 내 두 버전이 있을 때)
#   + coverage_limit (최초1회 vs 연간N회 — 지급 구조 자체가 다른 경우)
#
# 제외 기준 (집약 대상):
#   - amount / amount_condition : 경과기간·조건별 분기 → amount_detail 로 집약
#   - trigger : 질병군별 분기 → trigger_variants 로 집약
#   - reduction_note / source_pdf / dedupe_key : 파생 메타, 집약 불필요
_COMPARISON_GROUP_KEY_FIELDS = (
    "insurer",
    "product_name",
    "contract_name",
    "benefit_name",
    "benefit_category",       # diagnosis/surgery/treatment/…
    "renewal_type",           # 갱신형/비갱신형
    "coverage_limit",         # 최초1회/연간N회/…
)


def to_comparison_rows(detail_rows: list[dict] | list) -> list[dict]:
    """detail_rows (1 amount·condition·질병군 = 1행) → comparison_rows (1 급부 = 1행).

    비교 화면 공식 단위는 comparison_rows입니다.
    detail_rows(amounts×질병군 분해 행)를 급부 단위로 집약합니다.

    Grouping key:
        insurer + product_name + contract_name + benefit_name
        + benefit_category + renewal_type + coverage_limit

    집약 규칙:
      - amount / amount_condition : 첫 번째 행 대표값 표시
      - trigger       : 첫 번째 행 대표값 (단수 질병군이면 그대로)
      - trigger_variants : 질병군 trigger 2종 이상이면 전체 목록 JSON
      - amount_detail : (condition, amount, reduction_note, trigger) 전체 dedupe JSON
      - detail_row_count : 집약된 원본 행 수 (상세 패널 연결용)
      - 나머지 필드   : 첫 번째 행에서 복사
    """
    if not detail_rows:
        return []

    def _to_dict(r) -> dict:
        if hasattr(r, "model_dump"):
            return r.model_dump()
        return dict(r)

    from collections import OrderedDict
    groups: OrderedDict = OrderedDict()
    for row in detail_rows:
        d = _to_dict(row)
        key = tuple(str(d.get(f, "") or "") for f in _COMPARISON_GROUP_KEY_FIELDS)
        if key not in groups:
            groups[key] = []
        groups[key].append(d)

    comparison: list[dict] = []
    for key, rows_in_group in groups.items():
        first = rows_in_group[0]

        # 질병군별 trigger 목록 (dedupe, 순서 유지)
        triggers = list(dict.fromkeys(
            r.get("trigger", "") for r in rows_in_group if r.get("trigger")
        ))

        # (condition, amount) 전체 목록 — 경과기간·질병군 조합별 (dedupe)
        seen_amt: set[str] = set()
        all_amounts: list[dict] = []
        for row in rows_in_group:
            a_key = f"{row.get('amount', '')}|{row.get('amount_condition', '')}|{row.get('trigger', '')[:80]}"
            if a_key not in seen_amt:
                seen_amt.add(a_key)
                all_amounts.append({
                    "amount": row.get("amount", ""),
                    "condition": row.get("amount_condition", ""),
                    "reduction_note": row.get("reduction_note", ""),
                    "trigger": row.get("trigger", "") if len(triggers) > 1 else "",
                })

        comp_row = dict(first)
        comp_row["amount"] = first.get("amount", "")
        comp_row["amount_condition"] = first.get("amount_condition", "")
        comp_row["trigger"] = triggers[0] if triggers else first.get("trigger", "")
        comp_row["trigger_variants"] = (
            json.dumps(triggers, ensure_ascii=False) if len(triggers) > 1 else ""
        )
        comp_row["amount_detail"] = (
            json.dumps(all_amounts, ensure_ascii=False) if len(all_amounts) > 1 else ""
        )
        comp_row["detail_row_count"] = len(rows_in_group)

        comparison.append(comp_row)

    logger.info(
        "[comparison_rows] detail %d행 → comparison %d행 (집약률 %.0f%%)",
        len(detail_rows), len(comparison),
        (1 - len(comparison) / max(len(detail_rows), 1)) * 100,
    )
    return comparison
