"""계약/특약명 4단계 정규화 파이프라인.

raw_name → display_name → normalized_name → canonical_name

설계 원칙:
  - normalized_name: 매칭용으로 공격적 정규화 허용 (S, II, 갱신형 제거)
  - canonical_name:  출력용으로 의미 있는 구분은 반드시 보존
    → S특약 vs 특약, Ⅱ vs 무인, 갱신형 vs 비갱신형은 별개 상품이므로 보존

canonical_name 확정 우선순위:
  1. 요약서(product_summary)에서 추출한 display_name 기반 정제 (가장 안정)
  2. alias 사전에 등록된 매핑
  3. TOC/약관에서 추출한 이름은 단독으로 canonical이 되지 않음
     → 매칭 후 요약서 이름을 상속받아야 함
"""
from __future__ import annotations

import re
import unicodedata

from ..models import NameRecord


# ---------------------------------------------------------------------------
# 특약 코드 패턴
# ---------------------------------------------------------------------------

RE_RIDER_CODE = re.compile(r"\s*\(([A-Z]{1,3}\d+(?:\.\d+)?)\)\s*")

# ---------------------------------------------------------------------------
# normalized_name용 제거 대상 (매칭 전용, 공격적)
# ---------------------------------------------------------------------------

_MATCH_STRIP_PREFIXES = [
    "무배당",
    "선택특약",
]

_MATCH_STRIP_BRACKETS = re.compile(r"\[.+?\]")  # [일반가입형], [갱신형] 등

_MATCH_STRIP_SUFFIXES = [
    "무배당약관",
    "무배당",
    "약관",
]

# ---------------------------------------------------------------------------
# canonical_name용 제거 대상 (보수적, 상품 변형 보존)
# ---------------------------------------------------------------------------

_CANONICAL_STRIP_PREFIXES = [
    "무배당",
]

_CANONICAL_STRIP_SUFFIXES = [
    "무배당약관",
    "약관",
]

# ---------------------------------------------------------------------------
# alias 사전: normalized_name → canonical_name
#
# 주의: 이 사전은 "같은 상품의 표기 변형"만 등록한다.
# S특약 vs 특약이 별개 상품이면 등록하지 않는다.
# ---------------------------------------------------------------------------

CANONICAL_ALIASES: dict[str, str] = {
    # 한화/라이나 공통 표기 변형 (같은 상품)
    "항암방사선약물치료비특약": "항암방사선·약물 치료비 특약",
    "항암방사선약물치료비S특약": "항암방사선·약물 치료비 S특약",
}


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def build_name_record(
    raw_name: str,
    *,
    source: str = "",
    contract_type: str = "rider",
    code: str = "",
    canonical_override: str = "",
) -> NameRecord:
    """raw_name으로부터 4단계 이름을 모두 생성하여 NameRecord를 반환한다.

    Args:
        canonical_override: 명시적으로 canonical_name을 지정한다.
            요약서 마스터에서 가져온 이름 등을 직접 지정할 때 사용.
            지정하지 않으면 source에 따라 자동 결정:
            - product_summary → display_name 기반 보수적 정제
            - toc / terms → 빈 문자열 (매칭 후 요약서 이름을 상속)
    """
    display = to_display_name(raw_name)
    extracted_code = code or extract_rider_code(raw_name)
    normalized = to_normalized_name(display)

    if canonical_override:
        canonical = canonical_override
    elif source == "product_summary":
        canonical = to_canonical_name(display)
    else:
        # toc/terms 소스는 단독으로 canonical을 확정하지 않음
        # 매칭 후 summary의 canonical을 상속받을 예정
        canonical = ""

    return NameRecord(
        raw_name=raw_name,
        display_name=display,
        normalized_name=normalized,
        canonical_name=canonical,
        source=source,
        contract_type=contract_type,
        code=extracted_code,
    )


def to_display_name(raw: str) -> str:
    """raw_name → display_name: 줄바꿈·제어문자 제거, 공백 정리.
    의미 있는 내용은 모두 보존한다."""
    text = unicodedata.normalize("NFKC", raw)
    text = re.sub(r"[\x00-\x1f]+", " ", text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def to_normalized_name(display: str) -> str:
    """display_name → normalized_name: 매칭용 공격적 정규화.

    제거 대상 (매칭 정확도를 위해):
      - 특약 코드 (KA4.1)
      - 대괄호 접두사 [일반가입형], [갱신형]
      - "무배당", "선택특약" 접두사
      - "약관" 접미사
      - S특약 → 특약 (S 변형 통일)
      - 특별약관 → 특약
      - 로마숫자 Ⅱ/II (버전 표기 통일)
      - 모든 공백
    """
    name = display

    # 선행 기호/번호 제거: "+", "※", "▪", "- ", 숫자점 접두사
    name = re.sub(r"^[\+※▪▸▹►•\-|]+\s*", "", name)
    name = re.sub(r"^\d+\.\s*", "", name)

    name = RE_RIDER_CODE.sub("", name)
    name = _MATCH_STRIP_BRACKETS.sub("", name)

    for prefix in _MATCH_STRIP_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]

    for suffix in _MATCH_STRIP_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]

    name = name.replace("（", "(").replace("）", ")")

    # "(무)" = 무배당 약어, "(갱신형)" 제거 (매칭용)
    name = re.sub(r"\(무\)", "", name)
    name = re.sub(r"\(갱신형\)", "", name)

    # 가운뎃점(·∙), 하이픈, 슬래시, 쉼표 후 공백 등 구분자 제거 (매칭 방해)
    name = re.sub(r"[·・∙\-/]", "", name)

    name = re.sub(r"S특약", "특약", name)
    name = re.sub(r"특별약관", "특약", name)

    # 로마숫자 제거 — 원본(Ⅱ/Ⅲ) 및 NFKC 후(II/III) 모두 처리
    name = re.sub(r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]", "", name)
    name = re.sub(r"(?<=[가-힣])I{1,4}(?=[가-힣()\[\]]|$)", "", name)

    name = re.sub(r"\s+", "", name)

    return name.strip()


def to_canonical_name(display: str) -> str:
    """display_name → canonical_name: 보수적 정제.

    보존 대상 (실제 상품 변형을 구분하는 요소):
      - S특약 vs 특약 → 별개 상품이므로 보존
      - Ⅱ, Ⅲ → 버전 구분이므로 보존
      - 갱신형 / 비갱신형 / 일반가입형 → 상품 유형이므로 보존

    제거 대상 (순수 노이즈):
      - 특약 코드 (KA4.1)
      - "무배당" 접두사
      - "약관" 접미사
    """
    name = display

    # 선행 기호/번호 제거
    name = re.sub(r"^[\+※▪▸▹►•\-|]+\s*", "", name)
    name = re.sub(r"^\d+\.\s*", "", name)

    # 특약 코드 제거
    name = RE_RIDER_CODE.sub("", name)

    # "무배당" 접두사만 제거 (상품 의미 없음)
    for prefix in _CANONICAL_STRIP_PREFIXES:
        name = re.sub(rf"^\s*{re.escape(prefix)}\s*", "", name)

    # "약관" 접미사만 제거
    for suffix in _CANONICAL_STRIP_SUFFIXES:
        if name.rstrip().endswith(suffix):
            name = name.rstrip()[: -len(suffix)]

    # 전각 괄호 → 반각
    name = name.replace("（", "(").replace("）", ")")

    # "(무)" = 무배당 약어 제거
    name = re.sub(r"\(무\)", "", name)

    # 후행 "무배당" / "무배당[가입형]" 정리 (공백 유무 모두 처리)
    name = re.sub(r"\s*무배당(?:\[.+?\])?\s*$", "", name)

    # alias 사전 확인 (공백 제거 후 비교)
    name_compact = re.sub(r"\s+", "", name)
    if name_compact in CANONICAL_ALIASES:
        return CANONICAL_ALIASES[name_compact]

    name = re.sub(r"\s+", " ", name).strip()

    return name


def extract_rider_code(text: str) -> str:
    """텍스트에서 특약 코드 추출 (KA4.1 등)."""
    m = RE_RIDER_CODE.search(text)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# 하위 호환 API (기존 normalize_product_name / normalize_rider_name)
# ---------------------------------------------------------------------------

def normalize_product_name(name: str) -> str:
    """상품명 정규화 (하위 호환)."""
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"\(무배당\)", "", name)
    name = re.sub(r"\(갱신형\)", "", name)
    name = re.sub(r"（(.+?)）", r"(\1)", name)
    return name.strip()


def normalize_rider_name(name: str) -> str:
    """특약명 정규화 (하위 호환)."""
    name = normalize_product_name(name)
    name = re.sub(r"특별약관$", "특약", name)
    name = re.sub(r"\s*약관\s*$", "", name)
    return name.strip()


def extract_rider_key(name: str) -> str:
    """비교용 특약 키 생성 (하위 호환)."""
    key = normalize_rider_name(name)
    key = re.sub(r"^(한화생명|라이나생명|삼성생명|교보생명|메트라이프)\s*", "", key)
    key = re.sub(r"^(시그니처\w*|라이프\w*)\s*", "", key)
    key = re.sub(r"[\s\(\)（）무배당갱신형]", "", key)
    return key
