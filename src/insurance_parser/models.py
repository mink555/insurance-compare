"""보험 문서 파싱 데이터 모델.

상품 번들:
  ProductBundle → summary_pdf + terms_pdf → BundleStatus
"""
from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# PDF 추출 결과
# ---------------------------------------------------------------------------

class ExtractionResult(BaseModel):
    """PDF 추출 단계 결과"""
    source_path: str
    backend: str = ""           # pymupdf
    markdown: str = ""
    total_pages: int = 0
    tables_found: int = 0
    success: bool = True
    error: str = ""
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# 상품 번들 모델
# ---------------------------------------------------------------------------

class BundleStatus(str, enum.Enum):
    """상품 번들 완전성 상태.

    - COMPLETE: 상품요약서 + 약관 모두 있음
    - INCOMPLETE_SUMMARY_ONLY: 상품요약서만 있음
    - INCOMPLETE_TERMS_ONLY: 약관만 있음
    """
    COMPLETE = "COMPLETE"
    INCOMPLETE_SUMMARY_ONLY = "INCOMPLETE_SUMMARY_ONLY"
    INCOMPLETE_TERMS_ONLY = "INCOMPLETE_TERMS_ONLY"


class ProductBundle(BaseModel):
    """한 상품에 대한 문서 세트.

    상품요약서(summary_pdf)와 약관(terms_pdf)을 하나의 단위로 묶어 관리한다.
    """
    company_name: str = ""
    product_name: str = ""
    summary_pdf: Optional[str] = None
    terms_pdf: Optional[str] = None

    @property
    def status(self) -> BundleStatus:
        from pathlib import Path as _Path
        has_summary = self.summary_pdf is not None and _Path(self.summary_pdf).is_file()
        has_terms = self.terms_pdf is not None and _Path(self.terms_pdf).is_file()
        if has_summary and has_terms:
            return BundleStatus.COMPLETE
        if has_summary:
            return BundleStatus.INCOMPLETE_SUMMARY_ONLY
        return BundleStatus.INCOMPLETE_TERMS_ONLY

    def is_complete(self) -> bool:
        return self.status == BundleStatus.COMPLETE

    def validate_for_analysis(self) -> None:
        if not self.is_complete():
            raise ValueError(
                f"[{self.company_name} / {self.product_name}] "
                f"분석 실행 불가 — 상태: {self.status.value}. "
                "상품요약서와 약관이 모두 필요합니다."
            )


class BundleParseResult(BaseModel):
    """ProductBundle 파싱 결과."""
    bundle: ProductBundle
    status: BundleStatus
    summary_data: Optional[dict] = None
    terms_data: Optional[dict] = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}
