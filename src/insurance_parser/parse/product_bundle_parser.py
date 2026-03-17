"""
범용 상품 번들 파서.

목적:
  회사별 전용 파서 레지스트리 + GenericSummaryParser fallback 구조.
  새 회사 추가 시 register_summary_parser()만 호출하면 됨.

설계 원칙:
  - x좌표·페이지번호·특정 상품명 하드코딩 없음
  - 회사 전용 patch는 strategy 레이어로만 격리
  - find_tables 기반 구조 유지 (lina_summary_parser 위임)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

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
# 편의 함수 (스크립트용)
# ---------------------------------------------------------------------------

def get_or_generic_parser(company_name: str):
    """회사명으로 등록 파서를 조회하고 없으면 GenericSummaryParser를 반환한다."""
    cls = _get_summary_parser(company_name)
    return cls() if cls else GenericSummaryParser()
