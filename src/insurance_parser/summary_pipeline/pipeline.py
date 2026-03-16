"""4단 파이프라인 오케스트레이터.

parse → normalize → classify → export

Graceful degradation 구조:
  - summary_pdf만 있으면 summary 기준 최대 파싱
  - terms_pdf만 있으면 terms 기준 최대 파싱 (향후 구현)
  - 둘 다 있으면 merge 우선
  - require_complete=False(기본값): 단일 문서도 허용
  - require_complete=True: BOTH 아니면 ValueError (엄격 모드)

사용 예시:
    from insurance_parser.summary_pipeline import run_pipeline, DocumentBundle

    bundle = DocumentBundle(
        company_name="라이나생명",
        product_name="무배당 뉴스타트플러스암보험(갱신형)",
        summary_pdf="path/to/summary.pdf",
        # terms_pdf 없어도 동작
    )
    result = run_pipeline(bundle)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .models import (
    BundleStatus,
    CanonicalBenefit,
    DocumentBundle,
    PipelineResult,
    SummaryRow,
)
from .normalizer import normalize_summary_data, export_to_summary_rows
from .classifier import classify_benefits, detect_insurance_type
from .detector import detect_doc_type, try_parse_unknown
from .models import DocType

logger = logging.getLogger(__name__)


def _get_parser(company_name: str):
    """회사명으로 파서 클래스 조회. ProductBundleParser 레지스트리 활용."""
    try:
        from insurance_parser.parse.product_bundle_parser import _get_summary_parser
        return _get_summary_parser(company_name)
    except ImportError:
        return None


def _parse_summary_pdf(pdf_path: str, company_name: str) -> tuple[Optional[dict], list[str]]:
    """Stage 1: summary_pdf 파싱. 회사별 등록 파서 → GenericSummaryParser fallback."""
    warnings: list[str] = []

    if not Path(pdf_path).is_file():
        return None, [f"파일 없음: {pdf_path}"]

    parser_cls = _get_parser(company_name)
    if parser_cls is None:
        from insurance_parser.parse.product_bundle_parser import GenericSummaryParser
        parser_cls = GenericSummaryParser
        warnings.append(f"전용 파서 없음: '{company_name}' → GenericSummaryParser 사용")

    try:
        parser = parser_cls()
        data = parser.parse_pdf(Path(pdf_path))
        return data, warnings
    except Exception as e:
        logger.exception("summary 파싱 실패: %s", pdf_path)
        return None, warnings + [f"파싱 오류: {e}"]


def _parse_terms_pdf(pdf_path: str) -> tuple[Optional[dict], list[str]]:
    """Stage 1 (terms): terms_pdf 파싱. 현재는 stub — 향후 TermsParser 연동.

    TERMS_ONLY 상태를 명시적으로 표기합니다.
    """
    if not Path(pdf_path).is_file():
        return None, [f"파일 없음: {pdf_path}"]
    # TODO: TermsParser 연동 후 실제 파싱으로 교체
    return {"contracts": [], "_terms_stub": True}, [
        "⚠️ terms_pdf 파싱은 현재 제한적 추출(stub) — 급부 정보 없음, 약관 구조만 저장됨"
    ]


def _parse_unknown_pdf(pdf_path: str, company_name: str) -> tuple[Optional[dict], str, list[str]]:
    """UNKNOWN 타입 문서 — summary → terms 순서로 fallback 파싱 시도.

    Returns:
        (parse_result, resolved_doc_type_value, warnings)
    """
    resolved_type, data, warnings = try_parse_unknown(pdf_path, company_name)
    return data, resolved_type.value, warnings


def run_pipeline(
    bundle: DocumentBundle,
    *,
    require_complete: bool = False,
) -> PipelineResult:
    """4단 파이프라인 실행.

    Args:
        bundle: 입력 번들 (DocumentBundle). 단일 문서도 허용.
        require_complete: True이면 BOTH가 아닐 때 ValueError. 기본값 False.

    Returns:
        PipelineResult. 실패 시에도 errors 채운 결과 반환 (앱이 죽지 않음).
    """
    if require_complete:
        bundle.validate_for_analysis()

    result = PipelineResult(bundle=bundle, status=bundle.status)

    # ── graceful degradation: 사용 가능한 PDF 판단 ─────────────────────────
    has_summary = bool(bundle.summary_pdf and Path(bundle.summary_pdf).is_file())
    has_terms = bool(bundle.terms_pdf and Path(bundle.terms_pdf).is_file())

    if not has_summary and not has_terms:
        result.errors.append("파싱 가능한 PDF 없음 (summary_pdf, terms_pdf 모두 없음)")
        return result

    summary_data: Optional[dict] = None
    insurance_type = detect_insurance_type(bundle.product_name, bundle.summary_pdf or bundle.terms_pdf or "")

    # ── Stage 1: parse ──────────────────────────────────────────────────────
    if has_summary:
        # 문서 타입 판별 (routing hint — blocking 없음)
        hint = detect_doc_type(bundle.summary_pdf)

        if hint == DocType.UNKNOWN:
            # unknown: summary → terms 순으로 fallback 파싱 시도
            summary_data, resolved_type, parse_warnings = _parse_unknown_pdf(
                bundle.summary_pdf, bundle.company_name
            )
            result.warnings.extend(parse_warnings)
            result.warnings.append(
                f"문서 타입 불명확 → fallback 파싱 결과: {resolved_type}"
            )
        elif hint == DocType.TERMS:
            # 요약서 경로에 약관이 들어온 경우
            terms_data, terms_warnings = _parse_terms_pdf(bundle.summary_pdf)
            result.warnings.extend(terms_warnings)
            summary_data = terms_data
        else:
            summary_data, parse_warnings = _parse_summary_pdf(bundle.summary_pdf, bundle.company_name)
            result.warnings.extend(parse_warnings)

    if has_terms and not summary_data:
        terms_data, terms_warnings = _parse_terms_pdf(bundle.terms_pdf)
        result.warnings.extend(terms_warnings)
        if terms_data:
            summary_data = terms_data

    if not summary_data:
        result.errors.append("PDF 파싱 결과 없음")
        return result

    # Stage 1 결과 로그
    raw_contracts = list(summary_data.get("contracts", []))
    for item in summary_data.get("riders", []):
        if isinstance(item, dict):
            nested = item.get("contracts")
            raw_contracts.extend(nested if nested else [item])
    raw_benefit_count = sum(
        len(c.get("benefits", [])) for c in raw_contracts if isinstance(c, dict)
    )
    logger.info(
        "[Stage1/parse] %s/%s: contracts=%d, raw benefits=%d | sample: %s",
        bundle.company_name, bundle.product_name,
        len(raw_contracts), raw_benefit_count,
        [c.get("name", "") for c in raw_contracts[:3]],
    )

    # ── Stage 2: normalize ──────────────────────────────────────────────────
    canonical: list[CanonicalBenefit] = normalize_summary_data(
        summary_data,
        company_name=bundle.company_name,
        product_name=bundle.product_name,
    )
    for b in canonical:
        if not b.insurance_type:
            b.insurance_type = insurance_type

    logger.info(
        "[Stage2/normalize] %s/%s: CanonicalBenefit %d건 | sample keys: %s",
        bundle.company_name, bundle.product_name, len(canonical),
        [f"{b.benefit_name}|{b.amounts[0].amount if b.amounts else ''}" for b in canonical[:3]],
    )

    # ── Stage 3: classify ───────────────────────────────────────────────────
    canonical = classify_benefits(canonical)
    result.canonical_benefits = canonical

    # ── Stage 4: export ─────────────────────────────────────────────────────
    rows = export_to_summary_rows(canonical)

    # bundle_status + partial flag 채우기
    bundle_status_val = bundle.status.value
    is_partial = bundle.status != BundleStatus.BOTH
    for row in rows:
        row.bundle_status = bundle_status_val
        row.partial = is_partial

    result.summary_rows = rows

    logger.info(
        "[Stage4/export] %s/%s: SummaryRow %d건 | sample dedupe_keys: %s",
        bundle.company_name, bundle.product_name, len(rows),
        [r.dedupe_key for r in rows[:3]],
    )

    # ── Stage 5: LLM enrichment (slots 추출) ─────────────────────────────
    try:
        from insurance_parser.comparison.enrich import enrich_rows
        row_dicts = [r.model_dump() for r in rows]
        enriched = enrich_rows(row_dicts)
        for row_obj, enriched_dict in zip(rows, enriched):
            row_obj.slots = enriched_dict.get("slots")
        enriched_count = sum(1 for r in rows if r.slots is not None)
        logger.info(
            "[Stage5/enrich] %s/%s: %d/%d rows enriched",
            bundle.company_name, bundle.product_name,
            enriched_count, len(rows),
        )
    except Exception as e:
        logger.warning("[Stage5/enrich] 스킵: %s", e)

    # 상태별 soft warning
    if bundle.status == BundleStatus.SUMMARY_ONLY:
        result.warnings.append("terms_pdf 없음 — terms_reference / exclusions 미채움")
    elif bundle.status == BundleStatus.TERMS_ONLY:
        result.warnings.append(
            "⚠️ TERMS_ONLY: summary_pdf 없음 — 급부 정보 제한적 추출 상태. "
            "상품요약서를 추가하면 더 정확한 결과를 얻을 수 있습니다."
        )

    logger.info(
        "[%s] %s: %d 급부, %d rows (%s)",
        bundle.company_name, bundle.product_name,
        len(canonical), len(result.summary_rows), bundle.status.value,
    )
    return result


def run_pipelines(
    bundles: list[DocumentBundle],
    *,
    require_complete: bool = False,
    skip_errors: bool = True,
) -> list[PipelineResult]:
    """복수 번들 일괄 처리. 기본적으로 오류 건은 건너뜁니다."""
    results: list[PipelineResult] = []
    for bundle in bundles:
        try:
            results.append(run_pipeline(bundle, require_complete=require_complete))
        except (ValueError, Exception) as e:
            if skip_errors:
                logger.warning("번들 파싱 실패 [%s/%s]: %s", bundle.company_name, bundle.product_name, e)
                err_result = PipelineResult(bundle=bundle, status=bundle.status)
                err_result.errors.append(str(e))
                results.append(err_result)
            else:
                raise
    return results


def to_dataframe(results: list[PipelineResult]):
    """PipelineResult 리스트 → pandas DataFrame."""
    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError("pandas 필요: pip install pandas") from e

    rows = []
    for r in results:
        for row in r.summary_rows:
            rows.append(row.model_dump())
    return pd.DataFrame(rows)
