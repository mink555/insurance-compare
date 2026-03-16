"""
insurance_parser — 보험 상품 PDF 파싱 패키지.

PDF에서 보험 상품의 특약·급부·면책 등 핵심 정보를 추출하여
구조화된 데이터로 변환한다.

핵심 파이프라인 (summary_pipeline):
  parse → normalize → classify → export
"""

__version__ = "4.0.0"
