"""Layer 2: LLM 기반 급부 슬롯 추출 (Enrichment).

파싱 시점에 1회 실행 후 결과가 SummaryRow에 slots 필드로 저장됨.
비교 시점에는 LLM 호출 없음.

Graceful degradation:
  - OPENROUTER_API_KEY 미설정: enrichment 스킵, slots=None
  - LLM 응답 파싱 실패: 해당 row만 slots=None, 나머지 정상
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

logger = logging.getLogger(__name__)

_BATCH_SIZE = 8


# ---------------------------------------------------------------------------
# Slot schema
# ---------------------------------------------------------------------------

class BenefitSlots(BaseModel):
    """급부 1건의 구조화된 조건 슬롯."""
    trigger: str = ""
    start_condition: str = ""
    payment_freq: str = ""
    payment_limit: str = ""
    reduction_rule: str = ""
    amount_value: int = 0
    amount_display: str = ""


# ---------------------------------------------------------------------------
# LLM prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
/no_think
당신은 보험 약관 분석 전문가입니다.
주어진 보험 급부 정보에서 아래 슬롯을 추출하여 JSON 배열로 반환하세요.

각 급부마다 아래 필드를 추출합니다:
- trigger: 보험금 지급 사유 (예: "암 진단확정", "항암방사선 치료 시행", "암수술")
- start_condition: 보장 개시 조건 (예: "암보장개시일 이후", "계약일+90일")
- payment_freq: 지급 횟수 (예: "최초 1회", "연 1회", "매년 1회", "제한 없음")
- payment_limit: 지급 한도 (예: "최대 5년", "최대 10회", "한도 없음")
- reduction_rule: 감액 규칙 (예: "1년 이내 50%", "없음")
- amount_value: 금액 숫자 (원 단위 정수, 불명확하면 0)
- amount_display: 금액 표시 (예: "1,000만원", "매년 1,000만원")

★★★ 핵심 규칙 ★★★
1. 약관 원문을 그대로 복사하지 마세요. 핵심 의미만 추출하여 짧게 쓰세요.
2. 각 필드 값은 15자 이내로 작성하세요.
3. 비교 표에 바로 들어갈 수 있는 깔끔한 표현이어야 합니다.

좋은 예:
  trigger: "암 진단확정"
  start_condition: "암보장개시일 이후"
  payment_freq: "매년 1회"

나쁜 예 (절대 금지):
  trigger: "보험기간 중 피보험자가 암보장개시일 이후에 '암'으로 최초 진단이 확정되고..."
  start_condition: "보험기간 중 피보험자가..."

반드시 JSON 배열만 출력하세요. 설명이나 마크다운 없이 순수 JSON만.
급부 수와 동일한 개수의 객체를 순서대로 반환하세요.
정보가 없는 필드는 빈 문자열("")이나 0으로 채우세요."""


def _build_user_prompt(rows: list[dict]) -> str:
    """배치의 각 row를 사람이 읽을 수 있는 형태로 변환."""
    parts: list[str] = []
    for i, row in enumerate(rows):
        lines = [
            f"[급부 {i + 1}]",
            f"급부명: {row.get('benefit_name', '')}",
            f"지급사유: {row.get('trigger', '')}",
            f"금액: {row.get('amount', '')}",
            f"금액조건: {row.get('amount_condition', '')}",
            f"대기기간: {row.get('waiting_period', '')}",
            f"지급한도: {row.get('coverage_limit', '')}",
            f"감액: {row.get('reduction_note', '')}",
        ]
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# LLM call + parse
# ---------------------------------------------------------------------------

def _parse_llm_response(text: str, expected_count: int) -> list[BenefitSlots | None]:
    """LLM JSON 응답을 BenefitSlots 리스트로 파싱."""
    text = text.strip()
    json_match = re.search(r"\[.*\]", text, re.DOTALL)
    if not json_match:
        logger.warning("LLM 응답에서 JSON 배열을 찾을 수 없음: %s", text[:200])
        return [None] * expected_count

    try:
        raw_list = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        logger.warning("LLM JSON 파싱 실패: %s — %s", e, text[:200])
        return [None] * expected_count

    results: list[BenefitSlots | None] = []
    for i in range(expected_count):
        if i < len(raw_list) and isinstance(raw_list[i], dict):
            try:
                results.append(BenefitSlots(**raw_list[i]))
            except Exception as e:
                logger.warning("슬롯 객체 생성 실패 (index %d): %s", i, e)
                results.append(None)
        else:
            results.append(None)
    return results


_ENRICH_MODEL = "qwen/qwen3-30b-a3b"


def _call_llm_batch(rows: list[dict]) -> list[BenefitSlots | None]:
    """LLM을 호출하여 배치의 슬롯을 추출. 가벼운 모델 사용."""
    try:
        from insurance_parser.llm.openrouter import generate
    except ImportError:
        logger.warning("openrouter 모듈 임포트 실패 — enrichment 스킵")
        return [None] * len(rows)

    user_prompt = _build_user_prompt(rows)
    try:
        resp = generate(
            system_prompt=_SYSTEM_PROMPT,
            user_content=user_prompt,
            model=_ENRICH_MODEL,
            temperature=0.1,
            max_tokens=4096,
        )
        return _parse_llm_response(resp.content, len(rows))
    except Exception as e:
        logger.error("LLM 호출 실패: %s", e)
        return [None] * len(rows)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _is_llm_available() -> bool:
    """API 키가 설정되어 있는지 확인."""
    return bool(os.environ.get("OPENROUTER_API_KEY", ""))


def enrich_rows(
    rows: list[dict],
    *,
    progress_callback: Any | None = None,
) -> list[dict]:
    """SummaryRow dict 리스트에 slots 필드를 추가.

    각 row에 'slots' 키가 추가됨:
      - 성공: BenefitSlots.model_dump()
      - 실패/스킵: None

    LLM API 키 미설정 시 전체 스킵 (graceful degradation).

    Args:
        progress_callback: (done, total) → None 형태. UI 진행률 표시용.
    """
    if not rows:
        return rows

    if not _is_llm_available():
        logger.info("OPENROUTER_API_KEY 미설정 — enrichment 스킵 (%d rows)", len(rows))
        for row in rows:
            row.setdefault("slots", None)
        return rows

    already_enriched = [r for r in rows if r.get("slots") is not None]
    if len(already_enriched) == len(rows):
        logger.info("모든 행이 이미 enriched — 스킵")
        return rows

    to_enrich = [(i, r) for i, r in enumerate(rows) if r.get("slots") is None]
    total = len(to_enrich)
    logger.info("Enriching %d / %d rows (batch_size=%d)", total, len(rows), _BATCH_SIZE)

    for batch_start in range(0, total, _BATCH_SIZE):
        batch = to_enrich[batch_start : batch_start + _BATCH_SIZE]
        batch_rows = [r for _, r in batch]
        slots_list = _call_llm_batch(batch_rows)

        for (orig_idx, _), slots in zip(batch, slots_list):
            rows[orig_idx]["slots"] = slots.model_dump() if slots else None

        if progress_callback:
            progress_callback(min(batch_start + len(batch), total), total)

    enriched_count = sum(1 for r in rows if r.get("slots") is not None)
    logger.info("Enrichment 완료: %d / %d 성공", enriched_count, len(rows))

    return rows


# ---------------------------------------------------------------------------
# 조건상이 해소: LLM 기반 우위 판정
# ---------------------------------------------------------------------------

_RESOLVE_SYSTEM = """\
/no_think
당신은 보험 상품 비교 전문가입니다.
아래 급부별로 양사의 조건 차이가 나열됩니다. 각 급부에 대해:
1. 소비자(가입자) 관점에서 어느 쪽이 종합적으로 유리한지 판단하세요.
2. 판단 근거를 한 문장(30자 이내)으로 작성하세요.

반드시 아래 JSON 배열만 출력하세요. 설명이나 마크다운 없이 순수 JSON만.
[{"advantage": "당사우위" 또는 "타사우위" 또는 "대등", "rationale": "근거 한 문장"}]
급부 수와 동일한 개수의 객체를 순서대로 반환하세요."""


def resolve_mixed_pairs(pairs: list) -> list:
    """조건상이 판정된 ComparedPair 리스트에 LLM 분석을 적용합니다.

    각 pair의 overall_advantage와 rationale을 갱신합니다.
    LLM 미사용 시 원본 그대로 반환합니다.
    """
    if not pairs or not _is_llm_available():
        return pairs

    try:
        from insurance_parser.llm.openrouter import generate
    except ImportError:
        logger.warning("openrouter 모듈 임포트 실패 — resolve 스킵")
        return pairs

    prompt_parts: list[str] = []
    for i, cp in enumerate(pairs):
        diffs = [
            sc for sc in cp.slot_comparisons
            if sc.advantage in ("당사우위", "타사우위")
        ]
        lines = [f"[급부 {i + 1}: {cp.our_name}]"]
        for sc in diffs:
            lines.append(f"  - {sc.label}: 당사 \"{sc.our_value}\" vs 타사 \"{sc.comp_value}\" → {sc.advantage}")
        prompt_parts.append("\n".join(lines))

    user_prompt = "\n\n".join(prompt_parts)

    try:
        resp = generate(
            system_prompt=_RESOLVE_SYSTEM,
            user_content=user_prompt,
            model=_ENRICH_MODEL,
            temperature=0.1,
            max_tokens=2048,
        )
        text = resp.content.strip()
        json_match = re.search(r"\[.*\]", text, re.DOTALL)
        if not json_match:
            logger.warning("resolve LLM 응답에서 JSON을 찾을 수 없음")
            return pairs

        results = json.loads(json_match.group())
        for i, cp in enumerate(pairs):
            if i < len(results) and isinstance(results[i], dict):
                adv = results[i].get("advantage", "")
                rat = results[i].get("rationale", "")
                if adv in ("당사우위", "타사우위"):
                    cp.overall_advantage = adv
                    cp.rationale = rat
                elif adv == "대등":
                    cp.overall_advantage = "조건상이"
                    cp.rationale = rat
                else:
                    cp.rationale = rat
    except Exception as e:
        logger.error("resolve LLM 호출 실패: %s", e)

    return pairs
