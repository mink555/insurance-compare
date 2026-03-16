"""summary_pipeline — 범용 보험 문서 4단 파이프라인.

parse → normalize → classify → export

주요 진입점:
    from insurance_parser.summary_pipeline import run_pipeline, DocumentBundle

    bundle = DocumentBundle(
        company_name="라이나생명",
        product_name="무배당 뉴스타트플러스암보험(갱신형)",
        summary_pdf="path/to/summary.pdf",
        # terms_pdf 없어도 동작 (graceful degradation)
    )
    result = run_pipeline(bundle)

신상품 대응:
    - 새 회사 파서 등록:
        from insurance_parser.parse.product_bundle_parser import register_summary_parser
        register_summary_parser("새회사", MyParser)
    - 새 보험 종류 분류:
        insurance_info/benefit_category_keywords.json 에 섹션 추가
"""
from .models import (
    BundleStatus,
    DocType,
    DocumentBundle,
    CanonicalBenefit,
    AmountEntry,
    SummaryRow,
    PipelineResult,
)
from .normalizer import normalize_summary_data, export_to_summary_rows
from .classifier import classify_benefits, detect_insurance_type, classify_benefit_category
from .detector import detect_doc_type, classify_upload
from .pipeline import run_pipeline, run_pipelines, to_dataframe

__all__ = [
    # 모델
    "BundleStatus",
    "DocType",
    "DocumentBundle",
    "CanonicalBenefit",
    "AmountEntry",
    "SummaryRow",
    "PipelineResult",
    # 파이프라인
    "run_pipeline",
    "run_pipelines",
    "to_dataframe",
    # 유틸리티
    "normalize_summary_data",
    "export_to_summary_rows",
    "classify_benefits",
    "detect_insurance_type",
    "classify_benefit_category",
    # 판별기
    "detect_doc_type",
    "classify_upload",
]
