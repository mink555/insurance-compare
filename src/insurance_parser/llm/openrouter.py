"""OpenRouter API 클라이언트.

.env 파일에서 API 키와 모델명을 읽어 LLM 호출을 수행한다.
모델 실패 시 자동 fallback chain을 지원한다.

환경변수:
  OPENROUTER_API_KEY  — 필수
  OPENROUTER_MODEL    — 선택 (기본: qwen/qwen3-235b-a22b)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

CHAT_API_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODEL = "qwen/qwen3-235b-a22b"
FALLBACK_MODELS = [
    "google/gemma-3-27b-it",
    "qwen/qwen3-30b-a3b",
]


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


def _get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise EnvironmentError(
            "OPENROUTER_API_KEY 환경변수가 설정되지 않았습니다. "
            ".env 파일을 확인해 주세요."
        )
    return key


def _get_model() -> str:
    return os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_api_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://insurance-comparison-mvp.local",
        "X-Title": "Insurance Rider Comparison MVP",
    }


def generate(
    system_prompt: str,
    user_content: str,
    *,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> LLMResponse:
    """OpenRouter를 통해 LLM 응답을 생성한다. 실패 시 fallback 모델을 시도한다."""
    primary = model or _get_model()
    models_to_try = [primary] + [m for m in FALLBACK_MODELS if m != primary]

    payload_base = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    last_error: Exception | None = None
    for m in models_to_try:
        try:
            payload = {**payload_base, "model": m}
            log.info("OpenRouter 호출: model=%s, prompt_len=%d", m, len(user_content))

            resp = requests.post(
                CHAT_API_URL, headers=_headers(), json=payload, timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                raise RuntimeError(f"API error: {data['error']}")

            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})

            result = LLMResponse(
                content=message.get("content", ""),
                model=data.get("model", m),
                usage=data.get("usage", {}),
                raw=data,
            )
            log.info("OpenRouter 응답: model=%s, tokens=%s", result.model, result.usage)
            return result

        except Exception as e:
            log.warning("모델 %s 실패: %s — fallback 시도", m, e)
            last_error = e

    raise RuntimeError(
        f"모든 모델이 실패했습니다: {[primary] + FALLBACK_MODELS}. "
        f"마지막 오류: {last_error}"
    )
