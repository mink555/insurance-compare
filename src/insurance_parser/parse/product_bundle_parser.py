"""범용 상품 번들 파서.

회사별 전용 파서 레지스트리 + GenericSummaryParser fallback 구조.
새 회사 추가 시 BaseSummaryParser를 상속해 parse_pdf()를 구현하고
register_summary_parser()를 호출하면 됨.

parse_pdf() 반환 스키마 (모든 파서 공통):
{
    "product_name": str,
    "management_no": str,
    "components": {"riders": [str, ...]},
    "contracts": [
        {
            "name": str,
            "type": str,           # 항상 "rider"
            "source_pdf": str,
            "reference_amount": str,
            "benefits": [
                {
                    "benefit_names": [str, ...],
                    "trigger": str,
                    "amounts": [{"condition": str, "amount": str, "reduction_note": str}]
                }
            ],
            "notes": [str]
        }
    ]
}
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 공통 인터페이스
# ---------------------------------------------------------------------------

class BaseSummaryParser(ABC):
    """회사별 상품요약서 파서의 공통 인터페이스.

    새 보험사 파서 작성 시 이 클래스를 상속하고 parse_pdf()를 구현한다.
    반환 스키마는 모듈 docstring 참조.
    """

    @abstractmethod
    def parse_pdf(self, pdf_path: Path) -> dict:
        """PDF를 파싱해 표준 스키마 dict를 반환한다."""


# ---------------------------------------------------------------------------
# 회사별 파서 레지스트리
# ---------------------------------------------------------------------------

_SUMMARY_PARSER_REGISTRY: dict[str, type[BaseSummaryParser]] = {}


def _normalize_company_key(company_name: str) -> str:
    return company_name.strip().lower().replace(" ", "").replace("\u3000", "")


def register_summary_parser(company_name: str, parser_cls: type[BaseSummaryParser]) -> None:
    """회사의 상품요약서 파서를 레지스트리에 등록한다."""
    key = _normalize_company_key(company_name)
    _SUMMARY_PARSER_REGISTRY[key] = parser_cls
    logger.debug("파서 등록: %s → %s", company_name, parser_cls.__name__)


def _get_summary_parser(company_name: str) -> Optional[type[BaseSummaryParser]]:
    key = _normalize_company_key(company_name)
    return _SUMMARY_PARSER_REGISTRY.get(key)


# ---------------------------------------------------------------------------
# GenericSummaryParser — fallback
# ---------------------------------------------------------------------------

class GenericSummaryParser(BaseSummaryParser):
    """회사 전용 파서가 없을 때 사용하는 범용 파서.

    PyMuPDF find_tables()로 테이블 구조를 추출한다.
    특정 회사/상품 하드코딩 없이 동작하며,
    '급부', '보장', '지급' 키워드가 있는 테이블만 처리한다.
    """

    def parse_pdf(self, pdf_path: Path) -> dict:
        import fitz
        from .utils import is_benefit_table

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
                if not is_benefit_table(rows[0]):
                    continue
                header = [str(c or "").strip() for c in rows[0]]
                benefits = [
                    {"cells": [str(c or "").strip() for c in row], "header": header}
                    for row in rows[1:]
                    if any(row)
                ]
                if benefits:
                    contracts.append({
                        "page": page_idx + 1,
                        "name": f"p{page_idx + 1}",
                        "type": "rider",
                        "source_pdf": str(pdf_path),
                        "reference_amount": "",
                        "benefits": benefits,
                        "notes": [],
                    })

        doc.close()
        return {
            "product_name": pdf_path.stem,
            "management_no": "",
            "components": {"riders": []},
            "contracts": contracts,
        }


# ---------------------------------------------------------------------------
# 편의 함수
# ---------------------------------------------------------------------------

def get_or_generic_parser(company_name: str) -> BaseSummaryParser:
    """등록 파서를 조회하고 없으면 GenericSummaryParser를 반환한다."""
    cls = _get_summary_parser(company_name)
    return cls() if cls else GenericSummaryParser()
