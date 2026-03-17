"""파서 공통 유틸리티 — 모든 파서에서 재사용되는 헬퍼 함수."""
from __future__ import annotations

import re


def clean(text: str) -> str:
    """연속 공백·탭·줄바꿈 → 단일 공백."""
    return re.sub(r"[ \t\n]+", " ", text or "").strip()


def normalize_benefit_name(text: str) -> str:
    """급부명 전용 정규화: 셀 내 줄바꿈으로 쪼개진 단어를 이어붙인다.

    find_tables()는 셀 내 텍스트를 줄바꿈(\\n)으로 반환하는데,
    한글·영숫자 단어 중간에서 쪼개진 경우만 제거하고
    단어 사이의 공백·줄바꿈은 유지한다.

    예: "유방암/전립\\n선암 치료보\\n험금" → "유방암/전립선암 치료보험금"
    예: "갑상선암\\n∙\\n기타피부암" → "갑상선암 ∙ 기타피부암"
    """
    if not text:
        return ""
    text = re.sub(r"(?<=[가-힣A-Za-z0-9\)])\n(?=[가-힣A-Za-z0-9\(])", "", text)
    text = re.sub(r"[ \t\n]+", " ", text)
    return text.strip()
