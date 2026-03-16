"""Stage 3: 의미 분류기 (benefit_category 분류).

키워드 사전(benefit_category_keywords.json)을 기반으로
trigger + benefit_name 텍스트에서 보험 종류와 급부 카테고리를 자동 분류합니다.

신상품/타 보험 종류 추가 시: benefit_category_keywords.json만 수정하세요.
코드 변경 불필요.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from functools import lru_cache

from .models import CanonicalBenefit

_KEYWORDS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "insurance_info" / "benefit_category_keywords.json"

# 보험 종류 감지용 상품명/파일명 패턴
_INSURANCE_TYPE_PATTERNS: list[tuple[str, list[str]]] = [
    ("치매보험", ["치매", "인지", "알츠하이머", "CDR"]),
    ("치아보험", ["치아", "dental", "임플란트", "스케일"]),
    ("뇌심장보험", ["뇌", "심장", "뇌졸중", "심근경색", "뇌혈관", "심혈관"]),
    ("암보험", ["암", "종양", "신생물", "cancer", "cancer"]),
]


@lru_cache(maxsize=1)
def _load_keywords() -> dict:
    if _KEYWORDS_PATH.exists():
        return json.loads(_KEYWORDS_PATH.read_text(encoding="utf-8"))
    return {}


def detect_insurance_type(product_name: str, source_pdf: str = "") -> str:
    """상품명/PDF 경로에서 보험 종류 추정.

    새 보험 종류는 _INSURANCE_TYPE_PATTERNS에 추가하면 됩니다.
    """
    text = (product_name + " " + source_pdf).lower()
    for ins_type, keywords in _INSURANCE_TYPE_PATTERNS:
        for kw in keywords:
            if kw.lower() in text:
                return ins_type
    return "default"


def classify_benefit_category(
    benefit_name: str,
    trigger: str,
    insurance_type: str,
) -> tuple[str, str]:
    """급부명 + 지급사유 텍스트 → (benefit_category, benefit_category_ko).

    키워드 사전에서 insurance_type 섹션을 먼저 검색하고,
    없으면 default 섹션으로 fallback합니다.
    """
    kw_dict = _load_keywords()
    search_text = benefit_name + " " + trigger

    # insurance_type 전용 사전 → default 순으로 검색
    sections_to_try = []
    if insurance_type and insurance_type in kw_dict:
        sections_to_try.append(kw_dict[insurance_type])
    if "default" in kw_dict:
        sections_to_try.append(kw_dict["default"])

    for section in sections_to_try:
        for cat_key, cat_data in section.items():
            if cat_key.startswith("_"):
                continue
            keywords: list[str] = cat_data.get("keywords", [])
            for kw in keywords:
                if kw in search_text:
                    return cat_key, cat_data.get("label_ko", cat_key)

    return "other", "기타"


def classify_benefits(benefits: list[CanonicalBenefit]) -> list[CanonicalBenefit]:
    """CanonicalBenefit 리스트에 insurance_type, benefit_category 채우기.

    Stage 3 진입점.
    """
    for b in benefits:
        if not b.insurance_type:
            b.insurance_type = detect_insurance_type(b.product_name, b.source_pdf)
        if not b.benefit_category:
            cat, cat_ko = classify_benefit_category(
                b.benefit_name, b.trigger, b.insurance_type
            )
            b.benefit_category = cat
            b.benefit_category_ko = cat_ko
    return benefits
