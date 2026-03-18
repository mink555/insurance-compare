"""파서 공통 유틸리티 — 모든 파서에서 재사용되는 헬퍼 함수.

Generic 영역 (회사 무관):
  clean, normalize_benefit_name, is_benefit_table,
  find_benefit_columns, split_benefit_names,
  extract_reference_amount, parse_amounts_from_cell

Custom 영역 (각 파서에 유지):
  - 라이나: 좌표 기반 fallback 상수, □ 섹션 탐색, 주석 블록 수집
  - 한화: letter-spacing 제거, ■ 헤더 + 코드 추출, 경과기간 컬럼 처리
"""
from __future__ import annotations

import re
from typing import Optional


# ---------------------------------------------------------------------------
# 기본 텍스트 정규화
# ---------------------------------------------------------------------------

def clean(text: str) -> str:
    """연속 공백·탭·줄바꿈 → 단일 공백."""
    return re.sub(r"[ \t\n]+", " ", text or "").strip()


def normalize_benefit_name(text: str) -> str:
    """급부명 전용 정규화: 셀 내 줄바꿈으로 쪼개진 단어를 이어붙인다.

    find_tables()는 셀 내 텍스트를 줄바꿈(\\n)으로 반환하는데,
    한글·영숫자 단어 중간에서 쪼개진 경우만 제거하고
    단어 사이의 공백·줄바꿈은 유지한다.

    >>> normalize_benefit_name("유방암/전립\\n선암 치료보\\n험금")
    '유방암/전립선암 치료보험금'
    """
    if not text:
        return ""
    text = re.sub(r"(?<=[가-힣A-Za-z0-9\)])\n(?=[가-힣A-Za-z0-9\(])", "", text)
    text = re.sub(r"[ \t\n]+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 테이블 구조 인식
# ---------------------------------------------------------------------------

_RE_STRIP_SPACING = re.compile(r"(?<=[\uAC00-\uD7A3])\s+(?=[\uAC00-\uD7A3])")


def _normalize_header_cell(text: str) -> str:
    """헤더 셀 텍스트 정규화: letter-spacing 제거 + clean."""
    return clean(_RE_STRIP_SPACING.sub("", text or ""))


def is_benefit_table(header_row: list) -> bool:
    """헤더 행에 '급부명/지급사유/지급금액' 키워드가 모두 있으면 True.

    PDF 제조사별로 '급부명칭', '급 부 명' 등 변형이 있으므로
    각 키워드를 독립적으로 체크한다.
    """
    joined = _normalize_header_cell(" ".join(str(c or "") for c in header_row))
    return (
        bool(re.search(r"급부", joined))
        and bool(re.search(r"지급사유|사유", joined))
        and bool(re.search(r"지급금액|금액", joined))
    )


def find_benefit_columns(header_row: list) -> dict[str, Optional[int]]:
    """헤더 행에서 컬럼 역할을 파악한다.

    두 파서의 _find_col_indices() / _identify_columns()를 통합.

    Returns:
        {"benefit": int, "trigger": int, "amount": int, "condition": int | None}
        condition은 경과기간/조건 컬럼이 없으면 None.
    """
    result: dict[str, Optional[int]] = {
        "benefit": 0,
        "trigger": 1,
        "amount": 2,
        "condition": None,
    }
    for i, cell in enumerate(header_row):
        h = _normalize_header_cell(str(cell or ""))
        if re.search(r"급부", h):
            result["benefit"] = i
        elif re.search(r"지급사유|사유", h):
            result["trigger"] = i
        elif re.search(r"지급금액|금액", h):
            result["amount"] = i
            # merged cell 처리: 금액 컬럼 다음이 None이면 조건|금액값 분리 구조
            if i + 1 < len(header_row) and header_row[i + 1] is None:
                result["condition"] = i + 1
        elif re.search(r"경과기간|조건", h):
            result["condition"] = i

    return result


# ---------------------------------------------------------------------------
# 급부명 분리
# ---------------------------------------------------------------------------

def split_benefit_names(raw: str) -> list[str]:
    """'∙' 또는 '·' 로 구분된 급부명 분리. 각 이름에 normalize_benefit_name 적용.

    >>> split_benefit_names("일반암 진단자금∙고액암 진단자금")
    ['일반암 진단자금', '고액암 진단자금']
    """
    if not raw:
        return []
    raw = normalize_benefit_name(raw)
    parts = re.split(r"\s*[∙·]\s*", raw)
    result = [p.strip() for p in parts if p.strip() and len(p.strip()) >= 2 and not re.match(r"^[\s\d]+$", p)]
    return result if result else ([raw.strip()] if raw.strip() else [])


# ---------------------------------------------------------------------------
# 기준금액 추출
# ---------------------------------------------------------------------------

_RE_REFERENCE_AMOUNT = re.compile(
    r"기준\s*[:：]?\s*(?:특약|보험)?가입금액\s*([\d,]+만?원)"
)


def extract_reference_amount(text: str) -> str:
    """페이지 텍스트에서 기준금액 추출.

    라이나: [기준 : 특약보험가입금액 N만원]
    한화:   (기준 : 특약가입금액 N만원)
    → 공통 패턴으로 처리.

    >>> extract_reference_amount("(기준 : 특약가입금액 1,000만원)")
    '1,000만원'
    """
    m = _RE_REFERENCE_AMOUNT.search(text)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# 금액 셀 파싱
# ---------------------------------------------------------------------------

_RE_AMOUNT = re.compile(r"(\d[\d,]*)\s*만\s*원")
_RE_RATIO_AMOUNT = re.compile(r"((?:특약)?보험가입금액\s*의\s*\d+(?:\.\d+)?%)")
_RE_COND_AMOUNT = re.compile(
    r"(최초계약의\s*계약일\s*부터\s*[\d년]+\s*(?:이내|후))\s*(\d[\d,]+만원)"
)


def parse_amounts_from_cell(cell_text: str) -> list[dict]:
    """지급금액 셀 텍스트 → 조건별 금액 딕셔너리 리스트.

    처리 패턴 (우선순위):
      1. "최초계약의 계약일부터 N년 이내 N만원" (라이나형 연차별 금액)
      2. "(특약)보험가입금액의 N%" (비율 지급)
      3. "매월/매년(매회) N만원" (정기 지급)
      4. "N만원" (단순 금액)
      5. 금액 없음 → condition에 전체 텍스트

    반환 형식: [{"condition": str, "amount": str, "reduction_note": str}, ...]
    """
    if not cell_text:
        return []

    text = clean(cell_text)

    # (단, ...) 감액 조건 추출
    reduction_note = ""
    rm = re.search(r"\(단[,，]\s*(보험계약일(?:로)?부터\s*\d+년.*?(?:지급|적용)(?:함)?)\)", text)
    if rm:
        reduction_note = rm.group(1).strip()

    # 패턴 1: 최초계약 연차별 조건+금액
    cond_matches = list(_RE_COND_AMOUNT.finditer(text))
    if cond_matches:
        return [
            {
                "condition": clean(m.group(1)),
                "amount": clean(m.group(2)),
                "reduction_note": reduction_note if i == len(cond_matches) - 1 else "",
            }
            for i, m in enumerate(cond_matches)
        ]

    # 패턴 2: 가입금액의 N%
    ratio_m = _RE_RATIO_AMOUNT.search(text)
    if ratio_m:
        amount_str = re.sub(r"\s+의\s*", "의 ", clean(ratio_m.group(1))).strip()
        prefix = text[: ratio_m.start()].strip()
        rest = text[ratio_m.end():].strip()
        condition_str = ""
        dan_m = re.search(r"\(단[,，]\s*(.+?)\)", rest)
        if dan_m:
            condition_str = clean(dan_m.group(0))
        elif rest:
            condition_str = clean(rest[:200])
        if prefix and re.match(r"매월|매년|매회", prefix):
            condition_str = f"{prefix} {condition_str}".strip() if condition_str else prefix
        return [{"condition": condition_str, "amount": amount_str, "reduction_note": reduction_note}]

    # 패턴 3: 매월/매년 N만원
    m_per = re.match(r"(매월|매년\(매회\)|매년)\s*(\d[\d,]+만원)", text)
    if m_per:
        return [{"condition": m_per.group(1), "amount": m_per.group(2), "reduction_note": reduction_note}]

    # 패턴 4: 단순 금액
    m_simple = _RE_AMOUNT.search(text)
    if m_simple:
        return [{"condition": "", "amount": clean(m_simple.group(0)), "reduction_note": reduction_note}]

    # 패턴 5: 금액 없음 — 조건 텍스트만 보존
    if text:
        return [{"condition": text, "amount": "", "reduction_note": ""}]

    return []
