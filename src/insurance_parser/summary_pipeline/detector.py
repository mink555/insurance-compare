"""문서 타입 자동 판별기.

detect_doc_type()는 routing hint 역할만 합니다.
  - unknown이어도 파이프라인을 막지 않습니다.
  - unknown인 경우 파이프라인에서 summary → terms 순으로 fallback 파싱을 시도합니다.

판별 우선순위:
  1. 파일명 패턴 (가장 빠름)
  2. 첫 3페이지 텍스트 점수
  3. UNKNOWN (fallback 파싱 위임)
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import DocType

# ---------------------------------------------------------------------------
# 파일명 패턴
# ---------------------------------------------------------------------------
_SUMMARY_NAME_RE = re.compile(
    r"요약서|summary|_S\.pdf$",
    re.IGNORECASE,
)
_TERMS_NAME_RE = re.compile(
    r"약관|terms|보통약관|특별약관",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# 텍스트 패턴 (첫 3페이지)
# ---------------------------------------------------------------------------
_SUMMARY_TEXT_RE = re.compile(
    r"상품\s*요약서|보험료\s*납입\s*기간|급부명|지급\s*사유|지급\s*금액|보험금\s*지급표",
    re.MULTILINE,
)
_TERMS_TEXT_RE = re.compile(
    r"보통\s*약관|특별\s*약관|제\s*\d+\s*조|제\s*\d+\s*관|제\s*\d+\s*절|별표\s*\d+",
    re.MULTILINE,
)

_DOC_TYPE_KO = {
    DocType.SUMMARY: "상품요약서",
    DocType.TERMS: "약관",
    DocType.UNKNOWN: "알 수 없음",
}


def _read_first_pages_text(pdf_path: str, max_pages: int = 3) -> str:
    try:
        import fitz  # type: ignore
        doc = fitz.open(pdf_path)
        texts = [doc[i].get_text() for i in range(min(max_pages, len(doc)))]
        doc.close()
        return "\n".join(texts)
    except Exception:
        return ""


def detect_doc_type(pdf_path: str) -> DocType:
    """PDF 경로 → DocType routing hint.

    UNKNOWN을 반환해도 파이프라인은 계속 실행됩니다.
    파이프라인에서 summary → terms 순서로 fallback 파싱을 시도합니다.
    """
    name = Path(pdf_path).name

    # 1순위: 파일명
    if _SUMMARY_NAME_RE.search(name):
        return DocType.SUMMARY
    if _TERMS_NAME_RE.search(name):
        return DocType.TERMS

    # 2순위: 텍스트 점수
    text = _read_first_pages_text(pdf_path)
    if text:
        s = len(_SUMMARY_TEXT_RE.findall(text))
        t = len(_TERMS_TEXT_RE.findall(text))
        if s > t and s >= 2:
            return DocType.SUMMARY
        if t > s and t >= 2:
            return DocType.TERMS
        if s > 0:
            return DocType.SUMMARY
        if t > 0:
            return DocType.TERMS

    # 3순위: UNKNOWN → 파이프라인이 fallback 처리
    return DocType.UNKNOWN


def try_parse_unknown(
    pdf_path: str,
    company_name: str,
) -> tuple[DocType, dict | None, list[str]]:
    """UNKNOWN 문서에 대해 summary → terms 순서로 파싱 시도.

    Returns:
        (resolved_doc_type, parse_result_dict, warnings)
    """
    warnings: list[str] = []
    from pathlib import Path as _Path

    # summary 파서로 먼저 시도
    try:
        from insurance_parser.parse.product_bundle_parser import (
            _get_summary_parser,
            GenericSummaryParser,
        )
        parser_cls = _get_summary_parser(company_name) or GenericSummaryParser
        data = parser_cls().parse_pdf(_Path(pdf_path))
        if data and data.get("contracts"):
            warnings.append(f"UNKNOWN 문서 → summary 파서로 파싱 성공: {Path(pdf_path).name}")
            return DocType.SUMMARY, data, warnings
    except Exception as e:
        warnings.append(f"summary 파서 시도 실패: {e}")

    # terms 파서로 fallback (현재 stub)
    warnings.append(f"UNKNOWN 문서 → terms 파서 fallback (stub): {Path(pdf_path).name}")
    return DocType.TERMS, {"contracts": []}, warnings


def classify_upload(pdf_path: str) -> dict:
    """업로드 PDF 분석 결과 dict 반환 (UI용)."""
    doc_type = detect_doc_type(pdf_path)
    return {
        "path": pdf_path,
        "filename": Path(pdf_path).name,
        "doc_type": doc_type.value,
        "doc_type_ko": _DOC_TYPE_KO.get(doc_type, "알 수 없음"),
        "is_hint_only": doc_type == DocType.UNKNOWN,
    }
