"""
범용 상품 번들 파서.

목적:
  "현재 PDF 통과"가 아니라 "신상품 PDF가 추가돼도 그대로 동작하는 범용 파서"

설계 원칙:
  - 입력 단위 = 상품 번들 (company_name + product_name + summary_pdf + terms_pdf)
  - 상태 판정 강제: COMPLETE / INCOMPLETE_SUMMARY_ONLY / INCOMPLETE_TERMS_ONLY
  - 최종 비교/분석은 COMPLETE 상태에서만 허용
  - x좌표·페이지번호·특정 상품명 하드코딩 없음
  - 회사 전용 patch는 strategy 레이어로만 격리
  - find_tables 기반 구조 유지 (lina_summary_parser 위임)
  - 약관은 미래 확장용 (현재 파싱 결과는 None)

역할 분리:
  - 상품요약서: 비교용 급부 요약 정보
  - 약관: 근거·예외 검증용 (향후 TermsParser 연동)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..models import (
    BundleStatus,
    BundleParseResult,
    ProductBundle,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 회사별 요약서 파서 레지스트리
# ---------------------------------------------------------------------------
# 새 회사 추가 시 이 dict에만 등록하면 됨.
# value: callable(pdf_path: Path) -> dict  (상품요약서 파싱 함수)
#
# 등록 규칙:
#   - company_name 정규화 키(공백 제거, 소문자)를 key로 사용
#   - 파싱 함수는 lina_summary_parser의 parse_pdf 시그니처를 따름
#
_SUMMARY_PARSER_REGISTRY: dict[str, type] = {}


def _normalize_company_key(company_name: str) -> str:
    return company_name.strip().lower().replace(" ", "").replace("　", "")


def register_summary_parser(company_name: str, parser_cls: type) -> None:
    """새 회사의 상품요약서 파서를 등록한다.

    parser_cls는 parse_pdf(pdf_path: Path) -> dict 메서드를 가져야 한다.
    """
    key = _normalize_company_key(company_name)
    _SUMMARY_PARSER_REGISTRY[key] = parser_cls
    logger.debug("요약서 파서 등록: %s → %s", company_name, parser_cls.__name__)


def _get_summary_parser(company_name: str) -> Optional[type]:
    key = _normalize_company_key(company_name)
    return _SUMMARY_PARSER_REGISTRY.get(key)


# ---------------------------------------------------------------------------
# 범용 상품요약서 파서 (find_tables 기반 fallback)
# ---------------------------------------------------------------------------

class GenericSummaryParser:
    """회사 전용 파서가 없을 때 사용하는 범용 파서.

    PyMuPDF find_tables()를 이용해 테이블 구조를 추출한다.
    특정 회사/상품에 대한 하드코딩 없이 동작한다.
    """

    def parse_pdf(self, pdf_path: Path) -> dict:
        """상품요약서 PDF를 파싱해 범용 dict를 반환한다.

        반환 스키마:
        {
          "product_name": str,
          "source_pdf": str,
          "contracts": [
            {
              "name": str,
              "benefits": [...],
              "notes": [...],
            }
          ]
        }
        """
        import fitz  # PyMuPDF

        pdf_path = Path(pdf_path)
        doc = fitz.open(str(pdf_path))

        contracts: list[dict] = []
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            tabs = page.find_tables()
            if not tabs or not tabs.tables:
                continue
            for tab in tabs.tables:
                rows = tab.extract()
                if not rows:
                    continue
                # 헤더 행에서 급부 테이블 여부 판별 (범용)
                header = [str(c or "").strip() for c in rows[0]]
                if not any("급부" in h or "보장" in h or "지급" in h for h in header):
                    continue
                benefits = []
                for row in rows[1:]:
                    cells = [str(c or "").strip() for c in row]
                    if not any(cells):
                        continue
                    benefits.append({
                        "cells": cells,
                        "header": header,
                    })
                if benefits:
                    contracts.append({
                        "page": page_idx + 1,
                        "header": header,
                        "benefits": benefits,
                        "notes": [],
                    })

        doc.close()
        return {
            "product_name": pdf_path.stem,
            "source_pdf": str(pdf_path),
            "contracts": contracts,
        }


# ---------------------------------------------------------------------------
# ProductBundleParser — 번들 단위 파싱 오케스트레이터
# ---------------------------------------------------------------------------

class ProductBundleParser:
    """상품 번들 단위로 파싱을 수행하는 오케스트레이터.

    입력: ProductBundle (company_name + product_name + summary_pdf + terms_pdf)
    출력: BundleParseResult (status + summary_data + terms_data)

    상태 강제:
      - COMPLETE → 상품요약서 + 약관 모두 파싱
      - INCOMPLETE_SUMMARY_ONLY → 상품요약서만 파싱
      - INCOMPLETE_TERMS_ONLY → 약관만 (현재 미파싱, 향후 확장)

    최종 비교/분석:
      - parse_and_analyze()는 COMPLETE 상태에서만 실행
      - parse_only()는 상태에 관계없이 가능한 파싱만 수행
    """

    def __init__(self):
        self._generic_summary_parser = GenericSummaryParser()

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def parse_only(self, bundle: ProductBundle) -> BundleParseResult:
        """상태에 관계없이 가능한 파싱을 수행한다.

        COMPLETE: 상품요약서 + 약관(미래 확장)
        INCOMPLETE_SUMMARY_ONLY: 상품요약서만
        INCOMPLETE_TERMS_ONLY: 약관만 (현재 파싱 미지원 → 경고)
        """
        status = bundle.status
        result = BundleParseResult(bundle=bundle, status=status)

        if status == BundleStatus.INCOMPLETE_TERMS_ONLY:
            result.warnings.append(
                f"[{bundle.company_name}/{bundle.product_name}] "
                "약관만 있음 (INCOMPLETE_TERMS_ONLY). "
                "현재 약관 단독 파싱은 미지원입니다. "
                "상품요약서를 추가하면 급부 정보를 추출할 수 있습니다."
            )
            return result

        if bundle.summary_pdf:
            summary_data, errors = self._parse_summary(bundle)
            result.summary_data = summary_data
            result.errors.extend(errors)

        if status == BundleStatus.INCOMPLETE_SUMMARY_ONLY:
            result.warnings.append(
                f"[{bundle.company_name}/{bundle.product_name}] "
                "상품요약서만 있음 (INCOMPLETE_SUMMARY_ONLY). "
                "약관이 없어 근거·예외 검증은 불가합니다."
            )

        return result

    def parse_and_analyze(self, bundle: ProductBundle) -> BundleParseResult:
        """COMPLETE 상태의 번들에 대해 파싱 + 비교 분석을 수행한다.

        COMPLETE가 아니면 ValueError를 발생시킨다.
        """
        bundle.validate_for_analysis()  # COMPLETE 아니면 ValueError

        result = self.parse_only(bundle)

        # TODO: 약관 파싱 + 급부 교차 검증 (TermsParser 연동 후 구현)
        result.warnings.append(
            "약관 교차 검증은 아직 미구현입니다. (TermsParser 연동 예정)"
        )

        return result

    def parse_bundles(
        self,
        bundles: list[ProductBundle],
        *,
        require_complete: bool = False,
    ) -> list[BundleParseResult]:
        """여러 번들을 일괄 파싱한다.

        require_complete=True이면 COMPLETE 상태의 번들만 처리한다.
        """
        results = []
        for bundle in bundles:
            if require_complete and not bundle.is_complete():
                logger.warning(
                    "스킵 (COMPLETE 아님): %s / %s — %s",
                    bundle.company_name,
                    bundle.product_name,
                    bundle.status.value,
                )
                results.append(BundleParseResult(
                    bundle=bundle,
                    status=bundle.status,
                    warnings=[f"COMPLETE 아님 — {bundle.status.value}. 파싱 스킵."],
                ))
                continue
            try:
                r = self.parse_only(bundle)
                results.append(r)
            except Exception as exc:
                logger.error(
                    "번들 파싱 실패: %s / %s — %s",
                    bundle.company_name,
                    bundle.product_name,
                    exc,
                    exc_info=True,
                )
                results.append(BundleParseResult(
                    bundle=bundle,
                    status=bundle.status,
                    errors=[str(exc)],
                ))
        return results

    # ------------------------------------------------------------------
    # 내부 구현
    # ------------------------------------------------------------------

    def _parse_summary(
        self, bundle: ProductBundle
    ) -> tuple[Optional[dict], list[str]]:
        """상품요약서를 파싱한다.

        회사별 파서가 등록된 경우 해당 파서를 사용하고,
        없으면 GenericSummaryParser로 fallback한다.
        """
        errors: list[str] = []
        parser_cls = _get_summary_parser(bundle.company_name)

        if parser_cls is not None:
            parser = parser_cls()
            label = parser_cls.__name__
        else:
            parser = self._generic_summary_parser
            label = "GenericSummaryParser"
            logger.info(
                "[%s] 등록된 파서 없음 → %s 사용",
                bundle.company_name,
                label,
            )

        try:
            data = parser.parse_pdf(bundle.summary_pdf)
            logger.info(
                "[%s / %s] 요약서 파싱 완료 (%s)",
                bundle.company_name,
                bundle.product_name,
                label,
            )
            return data, errors
        except Exception as exc:
            msg = (
                f"[{bundle.company_name}/{bundle.product_name}] "
                f"요약서 파싱 실패 ({label}): {exc}"
            )
            logger.error(msg, exc_info=True)
            errors.append(msg)
            return None, errors


# ---------------------------------------------------------------------------
# 편의 함수
# ---------------------------------------------------------------------------

def make_bundle(
    company_name: str,
    product_name: str,
    summary_pdf: Optional[str | Path] = None,
    terms_pdf: Optional[str | Path] = None,
) -> ProductBundle:
    """ProductBundle 생성 편의 함수."""
    return ProductBundle(
        company_name=company_name,
        product_name=product_name,
        summary_pdf=str(summary_pdf) if summary_pdf else None,
        terms_pdf=str(terms_pdf) if terms_pdf else None,
    )


def parse_bundle(
    company_name: str,
    product_name: str,
    summary_pdf: Optional[str | Path] = None,
    terms_pdf: Optional[str | Path] = None,
    *,
    require_complete: bool = False,
) -> BundleParseResult:
    """단일 번들을 파싱하는 최상위 편의 함수.

    사용 예:
        result = parse_bundle(
            company_name="라이나생명",
            product_name="무배당 라이나 암보험",
            summary_pdf="path/to/summary.pdf",
            terms_pdf="path/to/terms.pdf",
        )
        print(result.status)          # BundleStatus.COMPLETE
        print(result.summary_data)    # 파싱된 급부 정보 dict
    """
    bundle = make_bundle(company_name, product_name, summary_pdf, terms_pdf)
    parser = ProductBundleParser()

    if require_complete:
        return parser.parse_and_analyze(bundle)
    return parser.parse_only(bundle)
