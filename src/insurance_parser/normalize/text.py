"""텍스트 정규화 유틸리티"""
from __future__ import annotations

import re
import unicodedata


def normalize_text(text: str) -> str:
    """일반 텍스트 정규화: 유니코드 NFKC + 공백 정리"""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u3000", " ")  # 전각 스페이스
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_whitespace(text: str) -> str:
    """모든 공백을 단일 스페이스로"""
    return re.sub(r"\s+", " ", text).strip()


def clean_for_comparison(text: str) -> str:
    """비교용 텍스트 — 조사, 기호, 공백 제거"""
    text = normalize_text(text)
    text = re.sub(r"[의를은는이가에서도로](?=\s|$)", "", text)
    text = re.sub(r"[^\w가-힣a-zA-Z0-9]", "", text)
    return text.lower()
