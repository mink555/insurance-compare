"""summary_pipeline 전용 데이터 모델.

4단 파이프라인의 각 단계별 데이터 구조를 정의합니다.

  [Stage 1] parse      → RawBenefit      (파서 raw dict 그대로)
  [Stage 2] normalize  → CanonicalBenefit (정규화 완료, 회사 무관)
  [Stage 3] classify   → CanonicalBenefit.benefit_category 채움
  [Stage 4] export     → SummaryRow      (flat DataFrame 행)

DocumentBundle은 입력 단위입니다.
단일 문서도 허용하는 graceful degradation 구조입니다.
"""
from __future__ import annotations

import enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 문서 타입 판별 결과
# ---------------------------------------------------------------------------

class DocType(str, enum.Enum):
    SUMMARY = "summary"   # 상품요약서
    TERMS = "terms"       # 약관
    UNKNOWN = "unknown"   # 판별 불가


# ---------------------------------------------------------------------------
# 입력 단위 — Soft Status (블로킹 없음)
# ---------------------------------------------------------------------------

class BundleStatus(str, enum.Enum):
    """문서 구성 상태. UI 표시 전용 — 파이프라인을 막지 않습니다."""
    BOTH = "BOTH"                   # 요약서 + 약관 둘 다
    SUMMARY_ONLY = "SUMMARY_ONLY"   # 요약서만
    TERMS_ONLY = "TERMS_ONLY"       # 약관만
    UNKNOWN = "UNKNOWN"             # 아무것도 없음

    # 하위 호환: 이전 값 alias
    @classmethod
    def _missing_(cls, value):
        _alias = {
            "COMPLETE": cls.BOTH,
            "INCOMPLETE_SUMMARY_ONLY": cls.SUMMARY_ONLY,
            "INCOMPLETE_TERMS_ONLY": cls.TERMS_ONLY,
        }
        return _alias.get(value)

    @property
    def label_ko(self) -> str:
        return {
            "BOTH": "요약서+약관",
            "SUMMARY_ONLY": "요약서만",
            "TERMS_ONLY": "약관만",
            "UNKNOWN": "문서 없음",
        }.get(self.value, self.value)

    @property
    def badge_color(self) -> str:
        """UI 뱃지 색상."""
        return {
            "BOTH": "#059669",       # emerald
            "SUMMARY_ONLY": "#2563EB",  # blue
            "TERMS_ONLY": "#D97706",    # amber
            "UNKNOWN": "#6B7280",       # gray
        }.get(self.value, "#6B7280")


class DocumentBundle(BaseModel):
    """1개 상품의 문서 세트.

    단일 문서도 허용합니다. status는 표시용이며 파이프라인을 막지 않습니다.
    """
    company_name: str
    product_name: str
    summary_pdf: Optional[str] = None   # 상품요약서 경로
    terms_pdf: Optional[str] = None     # 약관 경로

    @property
    def status(self) -> BundleStatus:
        has_summary = bool(self.summary_pdf and Path(self.summary_pdf).is_file())
        has_terms = bool(self.terms_pdf and Path(self.terms_pdf).is_file())
        if has_summary and has_terms:
            return BundleStatus.BOTH
        if has_summary:
            return BundleStatus.SUMMARY_ONLY
        if has_terms:
            return BundleStatus.TERMS_ONLY
        return BundleStatus.UNKNOWN

    @property
    def is_complete(self) -> bool:
        """하위 호환 alias. status == BOTH."""
        return self.status == BundleStatus.BOTH

    def validate_for_analysis(self) -> None:
        """엄격 모드에서만 호출. require_complete=True 시 사용."""
        if not self.is_complete:
            raise ValueError(
                f"[{self.company_name}] {self.product_name}: "
                f"require_complete=True 모드에서는 BOTH(요약서+약관) 번들만 허용합니다. "
                f"현재 상태: {self.status.value}"
            )


# ---------------------------------------------------------------------------
# Stage 2: 정규화된 급부 (회사 무관 공통 구조)
# ---------------------------------------------------------------------------

class AmountEntry(BaseModel):
    """지급금액 1건.

    한 셀에 amount + condition이 같이 있어도 분리 저장합니다.
    """
    amount: str = ""             # 핵심 금액 (예: "특약보험가입금액의 100%", "3,000만원")
    condition: str = ""          # 지급 조건/시기 (예: "(단, 1년 이전이면 50%)", "1년이상")
    reduction_note: str = ""     # 감액 조건 원문


class CanonicalBenefit(BaseModel):
    """정규화 완료된 급부 1건. 회사/상품 무관 공통 구조.

    Stage 2(normalize) 출력, Stage 3(classify) 에서 benefit_category가 채워집니다.
    """
    # 식별자
    company_name: str = ""
    product_name: str = ""
    contract_type: str = "rider"  # 항상 "rider" (비교 단위 = 특약)
    contract_name: str = ""      # 특약명 (정규화 전)
    contract_code: str = ""      # 특약 코드 (한화 KA1.1 등, 없으면 "")
    reference_amount: str = ""   # 기준금액 (가입금액)

    # 급부 정보
    benefit_name: str = ""       # 급부명 (단수. 복수일 경우 별도 행으로 분리)
    trigger: str = ""            # 지급사유 원문
    amounts: list[AmountEntry] = Field(default_factory=list)

    # 분류 (Stage 3에서 채워짐)
    insurance_type: str = ""     # 암보험 | 치매보험 | 치아보험 | 뇌심장보험 | ...
    benefit_category: str = ""   # diagnosis | surgery | treatment | hospitalization | ...
    benefit_category_ko: str = "" # 진단 | 수술 | 치료 | 입원 | ...

    # 메타
    notes: list[str] = Field(default_factory=list)
    source_pdf: str = ""
    source_type: str = "summary" # summary | terms


# ---------------------------------------------------------------------------
# Stage 4: 최종 flat export 행
# ---------------------------------------------------------------------------

class SummaryRow(BaseModel):
    """최종 비교 테이블의 1행. pandas DataFrame 변환용.

    Stage 4(export) 에서만 생성됩니다.
    """
    # 상품 식별
    insurer: str = ""
    product_name: str = ""
    contract_type: str = "rider"  # 항상 "rider"
    contract_name: str = ""
    contract_code: str = ""
    reference_amount: str = ""

    # 급부
    benefit_name: str = ""
    insurance_type: str = ""
    benefit_category: str = ""
    benefit_category_ko: str = ""
    trigger: str = ""

    # 금액 (첫 번째 AmountEntry 기준; 복수인 경우 amount_detail에 전체)
    amount: str = ""
    amount_condition: str = ""
    reduction_note: str = ""
    amount_detail: str = ""      # JSON string (복수 amount 전체)

    # 보장 조건
    waiting_period: str = ""     # 대기기간 (notes에서 추출)
    coverage_limit: str = ""     # 지급 한도/횟수 (trigger/notes에서 추출)
    renewal_type: str = ""       # 갱신형 | 비갱신형 (contract_name에서 추출)

    # 약관 근거 (terms_pdf 있을 때 채워짐)
    terms_reference: str = ""    # 근거 조문
    exclusions: str = ""         # 면책사항 (;로 구분)

    # 메타
    notes_summary: str = ""      # 주석 전체 (|로 구분)
    source_pdf: str = ""
    source_type: str = "summary"
    bundle_status: str = ""      # BOTH | SUMMARY_ONLY | TERMS_ONLY | UNKNOWN
    partial: bool = False        # True = 일부 필드만 추출됨
    dedupe_key: str = ""         # export 단계에서 생성되는 중복 판별 해시 키

    # Stage 5: LLM enrichment 슬롯 (comparison/enrich.py에서 채워짐)
    slots: Optional[dict] = None


# ---------------------------------------------------------------------------
# 파이프라인 결과
# ---------------------------------------------------------------------------

class PipelineResult(BaseModel):
    """4단 파이프라인 실행 결과."""
    bundle: DocumentBundle
    status: BundleStatus
    canonical_benefits: list[CanonicalBenefit] = Field(default_factory=list)
    summary_rows: list[SummaryRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0 and len(self.canonical_benefits) > 0
