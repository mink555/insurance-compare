"""
라이나생명 상품요약서 전용 파서 - find_tables() 기반 테이블 파싱.

구조:
  1. 암보험 주계약 상품요약서 (linalife/암보험/상품요약서/...)
     - 상품 구성 목록 (주계약 + 의무부가특약 + 선택특약 목록)
     - 주계약 급부 테이블 + 주석
     - 의무부가특약 급부 테이블 + 주석
  2. 선택특약 상품요약서 (linalife/암특약/상품요약서/...)
     - 각 특약별 급부 테이블 + 주석

출력 JSON 구조:
  {
    "product_name": ...,
    "management_no": ...,
    "components": {
      "riders": [...]
    },
    "contracts": [
      {
        "name": ...,
        "type": "rider",
        "source_pdf": ...,
        "reference_amount": ...,
        "benefits": [
          {
            "benefit_names": [...],  # 급부명 (merged cell 포함)
            "trigger": ...,          # 지급사유
            "amounts": [             # 지급금액 (조건별 여러 개)
              {"condition": ..., "amount": ..., "reduction_note": ...}
            ]
          }
        ],
        "notes": [...]  # 주) 번호 매긴 주석들
      }
    ]
  }
"""
from __future__ import annotations

import re
import json
import logging
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from .utils import clean as _clean, normalize_benefit_name as _normalize_benefit_name

logger = logging.getLogger(__name__)


def _normalize_note_text(text: str) -> str:
    """주석·reduction_note 전용 정규화: PDF 텍스트 레이어 분절을 구조적 규칙으로 복원한다.

    복원 전략 (적용 순서):
      1. 번호 경계 보호   : \\n + 숫자.공백 → 마커로 치환 (분리 기준 보존)
      2. 구조적 \\n 제거  : 아래 규칙에 해당하는 \\n 만 제거
           규칙 A) 한글 ↔ 한글       : "보험계\\n약" → "보험계약"
           규칙 B) 한글 ↔ 영숫자     : "보험\\n2년", "2년\\n이내"
           규칙 C) 숫자/단위 + \\n + 조사/어미
                   : "2년\\n이", "50%\\n를", "1회\\n에"
           규칙 D) 닫는 따옴표/괄호 앞 한글 + \\n
                   : "갑상선암\\n"으로" → "갑상선암"으로"
           규칙 E) 영숫자/한글 + \\n + 특수구두점이 아닌 문자
                   (남은 \\n 중 문장 구분이 아닌 것)
      3. 연속 공백 정리   : 탭·공백·줄바꿈 → 단일 공백
      4. 번호 경계 복원   : 마커 → 공백 (연속 주석 구분)
      5. whitelist 복원   : 구조 규칙으로 잡지 못한 공백 분절만 보정

    새 상품 PDF 추가 시 이 함수는 수정 불필요.
    공백 분절 패턴이 새로 발견되면 _BROKEN_PHRASE_MAP 에만 추가.
    """
    if not text:
        return ""

    # ── 1. 번호 경계 보호 ──────────────────────────────────────────────────────
    # "\\n10. ", "\\n11. " 같은 패턴을 마커로 치환해 이후 규칙에 의해 제거되지 않도록 보호
    text = re.sub(r"\n(?=\d{1,2}\.\s)", "\x00NEWNUM\x00", text)

    # ── 2. 구조적 \n 제거 ─────────────────────────────────────────────────────

    # 규칙 A: 한글 ↔ 한글 사이 개행 (단어 중간 쪼개짐)
    text = re.sub(r"(?<=[가-힣])\n(?=[가-힣])", "", text)

    # 규칙 B: 한글 ↔ 영숫자 사이 개행
    text = re.sub(r"(?<=[가-힣])\n(?=[A-Za-z0-9])", "", text)
    text = re.sub(r"(?<=[A-Za-z0-9])\n(?=[가-힣])", "", text)

    # 규칙 C: 숫자·단위(년/개월/회/%) + 개행 + 한글 조사·어미
    # 예: "2년\n이내", "50%\n를", "1회\n에 한하여"
    # 조사·어미: 이/가/을/를/은/는/의/에/에서/으로/로/과/와/이나/나/도/만/까지/부터/이후
    _JOSA = r"[이가을를은는의에으]"
    text = re.sub(
        r"(?<=[0-9년개월회%])\n(?=" + _JOSA + r")",
        "",
        text,
    )

    # 규칙 D: 닫는 인용부호("·') 또는 닫는 괄호 + 한글/영숫자 + 개행
    # 예: `"갑상선암"\n으로` → `"갑상선암"으로`
    # (닫는 부호 뒤 한글이 이어지는 경우)
    text = re.sub(r'(?<=["\'\)\]])\n(?=[가-힣])', "", text)
    # 반대: 한글 + 개행 + 여는 인용부호
    text = re.sub(r'(?<=[가-힣])\n(?=["\'\(\[])', "", text)

    # ── 3. 연속 공백 정리 ─────────────────────────────────────────────────────
    text = re.sub(r"[ \t\n]+", " ", text)

    # ── 4. 번호 경계 복원 ─────────────────────────────────────────────────────
    text = text.replace("\x00NEWNUM\x00", " ")

    # ── 5. whitelist 복원 (공백 분절, 구조 규칙으로 처리 불가한 핵심 어휘만) ──
    text = _repair_broken_korean_phrases(text)

    return text.strip()


# ---------------------------------------------------------------------------
# 보험 도메인 whitelist 기반 공백 분절 복원
# ---------------------------------------------------------------------------
#
# 적용 대상: PDF 원문 텍스트 레이어에서 이미 공백으로 쪼개진 어절 (개행이 아닌 공백)
# 적용 제외: _normalize_note_text 구조 규칙으로 잡히는 개행(\n) 패턴
#
# 유지 기준:
#   - 보험 약관 전반에 반복 등장하는 핵심 어휘만 등록
#   - "보험계 약" 처럼 어절 내부에 공백이 삽입된 경우 (원문 텍스트 레이어 문제)
#   - 새 상품 PDF 추가 시 이 목록은 거의 변경 불필요
#     (개행 분절은 구조 규칙이 처리, 공백 분절만 이곳에서 처리)
#
# 추가 방법: ("깨진 패턴", "복원 대상") 튜플을 리스트에 append
_BROKEN_PHRASE_MAP: list[tuple[str, str]] = [
    # ── 계약 주체 ──────────────────────────────────────────────────────────
    ("보험계 약",       "보험계약"),   # 보험계약일, 보험계약해당일 등 모두 커버
    ("피보험 자",       "피보험자"),
    ("계약 자",         "계약자"),
    ("수익 자",         "수익자"),
    # ── 금액/사유 ──────────────────────────────────────────────────────────
    ("금 액",           "금액"),
    ("사 유",           "사유"),
    # ── 보험금/납입 ────────────────────────────────────────────────────────
    ("보험 금",         "보험금"),
    ("납 입",           "납입"),
    # ── 진단 ───────────────────────────────────────────────────────────────
    ("진단 확정",       "진단확정"),
    ("진단확 정",       "진단확정"),
]

# 정규식 사전 컴파일 (모듈 로드 시 1회만 수행)
_BROKEN_PHRASE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(re.escape(broken)), fixed)
    for broken, fixed in _BROKEN_PHRASE_MAP
]


def _repair_broken_korean_phrases(text: str) -> str:
    """보험 도메인 whitelist 기반 공백 분절 복원.

    PDF 원문 텍스트 레이어에서 렌더링 단위 쪼개짐으로 인해
    어절 내부에 공백이 삽입된 경우만 복원한다.
    전역 한글 공백 제거가 아닌 whitelist 패턴만 적용해 오탐을 최소화한다.

    반환: 복원된 문자열 (변경 없을 경우 원본 그대로)
    debug 로그: 변경된 패턴만 출력
    """
    if not text:
        return text

    result = text
    for pattern, fixed in _BROKEN_PHRASE_PATTERNS:
        new = pattern.sub(fixed, result)
        if new != result:
            logger.debug(
                "_repair_broken_korean_phrases: '%s' → '%s'",
                pattern.pattern.replace("\\", ""),
                fixed,
            )
            result = new

    return result


def _get_management_no(text: str) -> str:
    m = re.search(r"관리번호\s*([\w\-]+)", text)
    return m.group(1).strip() if m else ""


def _get_product_name(full_text: str) -> str:
    """첫 줄 근처의 '무배당 ... 상품요약서' 패턴에서 상품명 추출."""
    m = re.search(
        r"(무배당\s+.+?)(?:\s*상품요약\s*서|\s*상품\s*요약\s*서)",
        full_text
    )
    if m:
        return _clean(m.group(1))
    return ""


RE_AMOUNT = re.compile(r"(\d[\d,]*)\s*만\s*원")
RE_NOTE_BLOCK_START = re.compile(r"^주\s*\)")
RE_NOTE_NUMBERED = re.compile(r"^(\d+)\.\s*(.+)", re.DOTALL)
RE_MANAGEMENT_NO = re.compile(r"^관리번호\s+[\d\-]+")


# ---------------------------------------------------------------------------
# 급부 테이블 파싱 (find_tables 기반)
# ---------------------------------------------------------------------------

def _is_benefit_table(tab: fitz.table.Table) -> bool:
    """급부 테이블인지 확인 (헤더에 급부명/지급사유/지급금액 포함)."""
    if tab.row_count < 2:
        return False
    header = tab.extract()[0]
    header_text = " ".join(c or "" for c in header)
    return bool(re.search(r"급\s*부\s*명.*지\s*급\s*사\s*유.*지\s*급\s*금\s*액", header_text))


def _find_col_indices(header_row: list) -> tuple[int, int, int, int]:
    """헤더 행에서 급부명/지급사유/지급금액(조건)/지급금액(값) 컬럼 인덱스 반환.

    지급금액이 merged cell로 두 컬럼에 걸친 경우(조건 | 금액값)를 처리.
    Returns: (name_idx, trigger_idx, amount_cond_idx, amount_val_idx)
    """
    name_idx = trigger_idx = amount_idx = -1
    for i, cell in enumerate(header_row):
        if cell is None:
            continue
        c = _clean(cell)
        if re.search(r"급\s*부\s*명", c):
            name_idx = i
        elif re.search(r"지\s*급\s*사\s*유", c):
            trigger_idx = i
        elif re.search(r"지\s*급\s*금\s*액", c):
            amount_idx = i

    # 지급금액 컬럼 다음이 None이면 merged cell (조건 | 금액값 구조)
    amount_val_idx = -1
    if amount_idx != -1 and amount_idx + 1 < len(header_row):
        if header_row[amount_idx + 1] is None:
            amount_val_idx = amount_idx + 1

    return name_idx, trigger_idx, amount_idx, amount_val_idx


def _parse_amounts_from_cell(cell_text: str) -> list[dict]:
    """지급금액 셀 텍스트에서 조건별 금액 리스트 추출.

    패턴 예시:
      "3,000만원\n(단, 보험계약일로부터 2년이 지난 보험..."
      "최초계약의 계약일\n부터 2년 이내\n500만원"
      "500만원"
      "특약보험가입금액의 100%\n(단, 보험계약일부터 1년이 지난...50%를 지급)"
      "매월 100만원\n(다만, 보험계약일로부터 1년이 지난...50%를 지급)"

    분리 규칙 (우선순위):
      1. "조건+금액" 패턴 (최초계약의 계약일부터 N년 이내/후 N만원)
      2. "가입금액의 N%" 패턴 → amount: "가입금액의 N%", condition: 단서조항
      3. "매월/매년 N만원" 패턴
      4. 단순 금액 + (단, ...) 감액 → amount + reduction_note
      5. 금액 없는 경우 → condition에 전체 텍스트
    """
    if not cell_text:
        return []

    # 셀 내 단어 중간 줄바꿈 먼저 제거한 뒤 일반 정리
    text = _clean(_normalize_note_text(cell_text))
    amounts: list[dict] = []

    # "(단, ...)" 감액 조건 추출 (전체 텍스트에서)
    reduction_note = ""
    reduction_m = re.search(r"\(단[,，]\s*(보험계약일(?:로)?부터\s*\d+년.*?(?:지급|적용)(?:함)?)\)", text)
    if reduction_m:
        reduction_note = _normalize_note_text(reduction_m.group(1))

    # ── 패턴 1: "조건+금액" 패턴 (최초계약의 계약일부터 N년 이내/후 N만원) ──
    RE_COND_AMOUNT = re.compile(
        r"(최초계약의\s*계약일\s*부터\s*[\d년]+\s*(?:이내|후))\s*(\d[\d,]+만원)"
    )
    cond_matches = list(RE_COND_AMOUNT.finditer(text))
    if cond_matches:
        for i, m in enumerate(cond_matches):
            entry: dict = {
                "condition": _clean(m.group(1)),
                "amount": _clean(m.group(2)),
                "reduction_note": reduction_note if i == len(cond_matches) - 1 else "",
            }
            amounts.append(entry)
        return amounts

    # ── 패턴 2: "(특약)?보험가입금액의 N%" 또는 "가입금액의 N%" ──
    # "특약보험가입금액의 100% (단, 1년 이전이면 50%를 지급)" 형태
    # "매년(매회) 특약보험가입금액 의 20% ..." 처럼 가입금액과 '의' 사이에 공백이 있는 경우도 처리
    RE_RATIO_AMOUNT = re.compile(
        r"((?:특약)?보험가입금액\s*의\s*\d+(?:\.\d+)?%)"  # 핵심 금액
    )
    ratio_m = RE_RATIO_AMOUNT.search(text)
    if ratio_m:
        # 매칭된 그룹에서 "의" 앞 공백 제거, "의" 뒤 공백은 1개로 정규화
        # 예: "보험가입금액 의 20%" → "보험가입금액의 20%"
        amount_str = re.sub(r"\s+의\s*", "의 ", _clean(ratio_m.group(1))).strip()
        # 핵심 금액 앞에 주기 접두사가 있으면 (예: "매년(매회)") condition에 포함
        prefix_str = text[: ratio_m.start()].strip()
        # 핵심 금액 이후의 텍스트가 단서 조건
        rest = text[ratio_m.end():].strip()
        # "(단, ...50%를 지급)" 같은 단서 추출
        condition_str = ""
        dan_m = re.search(r"\(단[,，]\s*(.+?)\)", rest)
        if dan_m:
            condition_str = _clean(dan_m.group(0))  # "(단, ...)" 전체 보존
        elif rest and not re.match(r"^\s*$", rest):
            condition_str = _clean(rest[:200])
        # 주기 접두사가 있으면 condition 앞에 추가 (예: "매년(매회) (단, ...)")
        if prefix_str and re.match(r"매월|매년|매회", prefix_str):
            condition_str = f"{prefix_str} {condition_str}".strip() if condition_str else prefix_str
        entry = {
            "condition": condition_str,
            "amount": amount_str,
            "reduction_note": reduction_note,
        }
        amounts.append(entry)
        return amounts

    # ── 패턴 3: "매월/매년(매회) N만원" ──
    m_per = re.match(r"(매월|매년\(매회\)|매년)\s*(\d[\d,]+만원)", text)
    if m_per:
        entry = {
            "condition": m_per.group(1),
            "amount": m_per.group(2),
            "reduction_note": reduction_note,
        }
        amounts.append(entry)
        return amounts

    # ── 패턴 4: 단순 금액 (N만원, N원, N억원) ──
    m_simple = RE_AMOUNT.search(text)
    if m_simple:
        entry = {
            "condition": "",
            "amount": _clean(m_simple.group(0)),
            "reduction_note": reduction_note,
        }
        amounts.append(entry)
        return amounts

    # ── 패턴 5: 금액 없음 → 전체를 condition으로 ──
    if text:
        amounts.append({"condition": text, "amount": "", "reduction_note": ""})

    return amounts


def _split_benefit_names(raw: str) -> list[str]:
    """'∙' 또는 '·' 로 구분된 급부명 분리. 각 이름에 _normalize_benefit_name 적용."""
    if not raw:
        return []
    # 먼저 급부명 전용 정규화
    raw = _normalize_benefit_name(raw)
    parts = re.split(r"\s*[∙·]\s*", raw)
    result = []
    for p in parts:
        p = p.strip()
        if p and len(p) >= 2 and not re.match(r"^[\s\d]+$", p):
            result.append(p)
    return result if result else ([raw.strip()] if raw.strip() else [])


def _parse_benefit_table(tab: fitz.table.Table) -> list[dict]:
    """find_tables()로 찾은 테이블에서 급부 리스트 추출.

    지급금액이 두 컬럼으로 분리된 경우(조건 컬럼 + 금액값 컬럼)도 처리.
    """
    rows = tab.extract()
    if not rows:
        return []

    header_row = rows[0]
    name_idx, trigger_idx, amount_cond_idx, amount_val_idx = _find_col_indices(header_row)

    # 컬럼 인덱스 fallback
    if name_idx == -1:
        col_count = len(header_row)
        if col_count >= 3:
            name_idx, trigger_idx, amount_cond_idx = 0, 1, 2
            amount_val_idx = -1
        else:
            return []

    benefits: list[dict] = []
    current_name = ""
    current_trigger = ""

    for row in rows[1:]:
        def get(idx: int) -> str:
            if idx < 0 or idx >= len(row):
                return ""
            return _clean(row[idx] or "")

        def get_name(idx: int) -> str:
            """급부명 셀 전용: 줄바꿈 단어 쪼개짐 복원 후 정규화."""
            if idx < 0 or idx >= len(row):
                return ""
            return _normalize_benefit_name(row[idx] or "")

        name_cell = get_name(name_idx)
        trigger_cell = _normalize_benefit_name(row[trigger_idx] or "") if 0 <= trigger_idx < len(row) else ""
        amount_cond_cell = get(amount_cond_idx)
        amount_val_cell = get(amount_val_idx) if amount_val_idx >= 0 else ""

        # find_tables()는 merged cell을 None으로 반환 → 이전 행 값 유지
        if name_cell:
            current_name = name_cell
        if trigger_cell:
            current_trigger = trigger_cell

        # 금액 파싱: 조건/값 두 컬럼이 분리된 경우 합쳐서 처리
        if amount_val_idx >= 0 and amount_val_cell:
            # "최초계약의 계약일부터 2년 이내 | 500만원" 구조
            combined = f"{amount_cond_cell} {amount_val_cell}".strip()
            amounts = _parse_amounts_from_cell(combined)
        else:
            amounts = _parse_amounts_from_cell(amount_cond_cell)

        if name_cell:
            benefit_names = _split_benefit_names(current_name)
            benefits.append({
                "benefit_names": benefit_names,
                "trigger": current_trigger,
                "amounts": amounts,
            })
        elif amounts and benefits:
            # 같은 급부의 추가 금액 조건 (merged cell 이어받기)
            benefits[-1]["amounts"].extend(amounts)

    return benefits


# ---------------------------------------------------------------------------
# 품질 판단
# ---------------------------------------------------------------------------

def _benefit_quality_score(benefits: list[dict]) -> float:
    """파싱된 급부 리스트의 품질 점수 반환 (0.0 ~ 1.0).

    0.0: 완전히 빈 결과 또는 이름·금액 모두 없음  → fallback 필요
    1.0: 이름, 지급사유, 금액 모두 완전히 채워짐

    채점 기준:
      - 급부 건수가 1개 이상: +0.3
      - 모든 급부에 benefit_names 있음: +0.3
      - 급부 중 금액 있는 것이 절반 이상: +0.4
    """
    if not benefits:
        return 0.0
    score = 0.3
    if all(b.get("benefit_names") for b in benefits):
        score += 0.3
    has_amount = sum(1 for b in benefits if b.get("amounts") and b["amounts"][0].get("amount"))
    if has_amount >= len(benefits) * 0.5:
        score += 0.4
    return score


# ---------------------------------------------------------------------------
# 좌표 기반 Fallback 파서
# ---------------------------------------------------------------------------

# 라이나 PDF 테이블 컬럼 x 경계 (포인트)
COL_NAME_MAX = 130       # 급부명 컬럼 최대 x_start
COL_TRIGGER_MIN = 125    # 지급사유 컬럼 최소 x_start
COL_TRIGGER_MAX = 315    # 지급사유 컬럼 최대 x_start
COL_AMOUNT_MIN = 315     # 지급금액 컬럼 최소 x_start
ROW_GAP_THRESHOLD = 40   # 새 행 시작 판단 y 간격 (pt)
TABLE_HEADER_Y_MARGIN = 20

RE_TABLE_HEADER_LINE = re.compile(r"급\s*부\s*명|지\s*급\s*사\s*유|지\s*급\s*금\s*액")


def _col_of(x0: float) -> str:
    """x 좌표로 컬럼 구분."""
    if x0 < COL_NAME_MAX:
        return "name"
    if COL_TRIGGER_MIN <= x0 < COL_TRIGGER_MAX:
        return "trigger"
    if x0 >= COL_AMOUNT_MIN:
        return "amount"
    return "other"


def _parse_amounts_from_texts(texts: list[str]) -> list[dict]:
    """amount 컬럼 블록 텍스트 리스트 → 조건별 금액 추출."""
    amounts: list[dict] = []
    pending_condition = ""

    for raw in texts:
        text = _clean(raw)
        if not text:
            continue
        amount_m = RE_AMOUNT.search(text)
        if amount_m:
            pre = text[:amount_m.start()].strip()
            condition = _clean(f"{pending_condition} {pre}") if pre else _clean(pending_condition)
            reduction_note = ""
            rm = re.search(r"\(단[,，]\s*(보험계약일(?:로)?부터\s*\d+년.*?(?:지급|적용)(?:함)?)\)", text)
            if rm:
                reduction_note = _clean(rm.group(1))
            entry: dict = {"condition": condition, "amount": _clean(amount_m.group(0))}
            if reduction_note:
                entry["reduction_note"] = reduction_note
            amounts.append(entry)
            pending_condition = ""
        else:
            if re.match(r"^\(단[,，]", text) and amounts:
                amounts[-1]["reduction_note"] = text
            else:
                pending_condition = _clean(f"{pending_condition} {text}")
    return amounts


def _fallback_parse_by_coords(
    page_blocks: list[tuple[int, tuple]],  # [(page_idx, (x0,y0,x1,y1,text)), ...]
    header_y0: float,
    end_y: float,
) -> list[dict]:
    """x 좌표 기반 fallback 파싱.

    page_blocks: (page_idx, (x0,y0,x1,y1,text)) 형태의 리스트 (y순 정렬)
    header_y0, end_y: 테이블 헤더 y, 주석 시작 y (같은 페이지 기준)
    """
    # 테이블 영역 블록만 추출
    table_blocks: list[tuple[int, tuple]] = []
    for pidx, b in page_blocks:
        x0, y0, x1, y1, text = b
        if y0 <= header_y0 + TABLE_HEADER_Y_MARGIN:
            continue
        if y0 >= end_y:
            continue
        if RE_TABLE_HEADER_LINE.search(_clean(text)):
            continue
        table_blocks.append((pidx, b))

    if not table_blocks:
        return []

    # 급부명 블록 묶기
    name_blocks = sorted(
        [(pidx, b) for pidx, b in table_blocks if _col_of(b[0]) == "name"],
        key=lambda x: (x[0], x[1][1])
    )
    if not name_blocks:
        return []

    # 이름 블록들을 행 그룹으로 묶기
    row_groups: list[list[tuple[int, tuple]]] = []
    cur: list[tuple[int, tuple]] = [name_blocks[0]]
    for i in range(1, len(name_blocks)):
        pp, pb = cur[-1]
        cp, cb = name_blocks[i]
        if cp != pp or (cb[1] - pb[3]) > ROW_GAP_THRESHOLD:
            row_groups.append(cur)
            cur = [(cp, cb)]
        else:
            cur.append((cp, cb))
    row_groups.append(cur)

    benefits: list[dict] = []
    for grp_idx, group in enumerate(row_groups):
        grp_page = group[0][0]
        if grp_idx > 0:
            prev = row_groups[grp_idx - 1]
            search_start_page, search_start_y = prev[-1][0], prev[-1][1][3]
        else:
            search_start_page, search_start_y = grp_page, header_y0 + TABLE_HEADER_Y_MARGIN

        if grp_idx + 1 < len(row_groups):
            next_page, next_y = row_groups[grp_idx + 1][0][0], row_groups[grp_idx + 1][0][1][1]
        else:
            next_page, next_y = 9999, 9999.0

        name_texts, trigger_texts, amount_texts = [], [], []
        for _, b in group:
            name_texts.append(_clean(b[4]))

        for pidx, b in table_blocks:
            col = _col_of(b[0])
            if col == "name":
                continue
            if pidx < search_start_page:
                continue
            if pidx == search_start_page and b[1] < search_start_y:
                continue
            if pidx > next_page:
                break
            if pidx == next_page and b[1] >= next_y:
                break
            if col == "trigger":
                trigger_texts.append(_clean(b[4]))
            elif col == "amount":
                amount_texts.append(_clean(b[4]))

        name_text = _normalize_benefit_name(" ".join(name_texts))
        trigger_text = _clean(" ".join(trigger_texts))
        amounts = _parse_amounts_from_texts(amount_texts)
        bnames = _split_benefit_names(name_text)

        if bnames or trigger_text:
            benefits.append({
                "benefit_names": bnames,
                "trigger": trigger_text,
                "amounts": amounts,
            })

    return benefits

def _extract_page_blocks(page: fitz.Page) -> list[tuple]:
    """(x0, y0, x1, y1, text) 튜플 리스트 반환, y0 순 정렬."""
    blocks = []
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, text, _, block_type = b
        if block_type == 0 and text.strip():
            blocks.append((x0, y0, x1, y1, text.strip()))
    blocks.sort(key=lambda b: (round(b[1] / 5) * 5, b[0]))
    return blocks


# ---------------------------------------------------------------------------
# 주석 추출
# ---------------------------------------------------------------------------

def _extract_notes_from_blocks(blocks: list[tuple]) -> list[str]:
    """주) 이후 번호 매긴 주석 추출.

    전략:
      1. 주) 헤더 이후 블록들의 텍스트를 줄바꿈(\\n)으로 연결해 하나의 원문으로 병합.
      2. _normalize_note_text로 한글 어절 내 분절 복원 (번호 경계 보존).
      3. _split_numbered_notes로 전체 문자열 기준 일괄 분리.
    이 방식으로 페이지 경계에서 쪼개진 10번/11번 같은 케이스를 올바르게 처리.

    종료 경계:
      - 다음 테이블 시작 신호 (급부명/지급사유/지급금액 헤더, [기준:], -N형) 가 블록
        텍스트에 나타나면 해당 블록부터 notes 수집을 멈춘다.
      - 이는 1형/2형처럼 같은 PDF에 여러 형이 있을 때 다음 형 테이블 내용이
        notes에 섞이는 것을 방지한다.
    """
    # 다음 테이블(급부표) 시작을 나타내는 신호 패턴
    # "급 부 명 지 급 사 유 지 급 금 액" 처럼 띄어쓰기가 있는 패턴도 포함
    RE_NEXT_TABLE = re.compile(
        r"급\s*부\s*명.{0,10}지\s*급\s*사\s*유"   # 급부명 ... 지급사유
        r"|지\s*급\s*사\s*유.{0,10}지\s*급\s*금\s*액"  # 지급사유 ... 지급금액
        r"|\[기준\s*[:：]"                         # [기준 :
        r"|-\s*\d+\s*형"                          # - 1형, - 2형
        r"|◆\s*보험금\s*지급사유"                  # ◆ 보험금 지급사유 (새 섹션)
        r"|①\s*보험금\s*지급사유"                  # ① 보험금 지급사유
    )

    raw_parts: list[str] = []
    in_notes = False

    for _, _, _, _, text in blocks:
        text_c = _clean(text)
        if not text_c:
            continue

        # 관리번호 블록 제거
        if RE_MANAGEMENT_NO.match(text_c):
            continue

        # "주)" 헤더 — in_notes 시작, 헤더 뒤 텍스트 포함 (원문 유지)
        if RE_NOTE_BLOCK_START.match(text_c):
            in_notes = True
            rest = re.sub(r"^주\s*\)\s*", "", text, count=1).strip()
            if rest:
                raw_parts.append(rest)
            continue

        if not in_notes:
            continue

        # 다음 테이블 시작 신호 감지 → notes 수집 종료
        if RE_NEXT_TABLE.search(text_c):
            break

        # 원문 그대로 추가 (블록 내부 \n 보존)
        raw_parts.append(text)

    if not raw_parts:
        return []

    # 블록 전체를 \n으로 이어붙여 하나의 원문으로 병합
    merged = "\n".join(raw_parts)

    # 한글 어절 내 분절 복원 (번호 경계 보존)
    normalized = _normalize_note_text(merged)

    # 전체 문자열 기준 일괄 번호 분리
    notes = _split_numbered_notes(normalized)
    return notes


def _split_numbered_notes(text: str) -> list[str]:
    """"10. 내용 11. 내용" 형태의 텍스트를 번호별로 분리.

    분리 기준:
      - 문자열 시작 또는 공백 이후에 나오는 "숫자(1~2자리) + 점 + 공백"
      - 단순 숫자 참조("제3호", "50%", "2년이내") 는 분리하지 않음

    방어 로직:
      - split_points가 1개인 경우에도 해당 번호부터 시작하는 텍스트만 반환
        (앞쪽에 번호 없는 잔여 텍스트가 붙어있는 경우 제거)
      - split_points가 0개이면 전체 텍스트를 그대로 반환
    """
    pattern = re.compile(r"(?:^|(?<=\s))\d{1,2}\.\s")
    split_points = [m.start() for m in pattern.finditer(text)]

    if not split_points:
        # 번호가 없으면 전체 반환
        return [text.strip()] if text.strip() else []

    parts = []
    for i, start in enumerate(split_points):
        end = split_points[i + 1] if i + 1 < len(split_points) else len(text)
        part = text[start:end].strip()
        if part:
            parts.append(part)
    return parts


# ---------------------------------------------------------------------------
# 급부 섹션 파서
# ---------------------------------------------------------------------------

class BenefitSectionParser:
    """PDF 문서의 특정 y 범위(섹션)에서 급부 테이블과 주석을 파싱.

    파싱 전략 (혼합형):
      1차: find_tables() — 실선 기반 구조 인식 (빠르고 정확)
      품질 점수 < QUALITY_THRESHOLD 이면:
      2차: 좌표 기반 fallback — x좌표로 컬럼 구분 (선이 없거나 인식 실패 시)
    """

    QUALITY_THRESHOLD = 0.5  # 이 점수 미만이면 fallback 시도

    RE_NOTE_START = re.compile(r"^주\s*\)")
    RE_REFERENCE = re.compile(
        r"\[기준\s*[:：]?\s*(?:특약)?보험가입금액\s*([\d,]+만원)\]"
    )
    RE_TABLE_HEADER = re.compile(r"급\s*부\s*명.*지\s*급\s*사\s*유.*지\s*급\s*금\s*액")

    def parse(
        self,
        doc: fitz.Document,
        section_page_idx: int,
        section_y0: float,
        next_section_page_idx: int,
        next_section_y0: float,
    ) -> dict:
        result: dict = {
            "reference_amount": "",
            "benefits": [],
            "notes": [],
            "_parse_method": "find_tables",  # 디버그용
        }

        # ── 기준금액 ──────────────────────────────────────────────────────────
        for pidx in range(section_page_idx, min(next_section_page_idx + 1, len(doc))):
            ref_m = self.RE_REFERENCE.search(doc[pidx].get_text())
            if ref_m:
                result["reference_amount"] = ref_m.group(1)
                break

        # ── 1차: find_tables() ────────────────────────────────────────────────
        benefits_ft: list[dict] = []
        for pidx in range(section_page_idx, min(next_section_page_idx + 1, len(doc))):
            page = doc[pidx]
            clip = self._page_clip(page, pidx,
                                   section_page_idx, section_y0,
                                   next_section_page_idx, next_section_y0)
            tabs = page.find_tables(clip=clip) if clip else page.find_tables()
            for tab in tabs.tables:
                if _is_benefit_table(tab):
                    benefits_ft.extend(_parse_benefit_table(tab))

        score = _benefit_quality_score(benefits_ft)
        logger.debug("find_tables 품질 점수: %.2f (급부 %d건)", score, len(benefits_ft))

        if score >= self.QUALITY_THRESHOLD:
            result["benefits"] = benefits_ft
        else:
            # ── 2차: 좌표 기반 fallback ──────────────────────────────────────
            logger.debug("품질 점수 %.2f < %.2f → 좌표 기반 fallback 시도",
                         score, self.QUALITY_THRESHOLD)
            result["_parse_method"] = "coord_fallback"

            # 섹션 내 전체 블록 수집
            all_page_blocks: list[tuple[int, tuple]] = []
            header_y0 = section_y0
            end_y = next_section_y0

            for pidx in range(section_page_idx, min(next_section_page_idx + 1, len(doc))):
                for b in _extract_page_blocks(doc[pidx]):
                    x0, y0, x1, y1, text = b
                    if pidx == section_page_idx and y0 < section_y0:
                        continue
                    if pidx == next_section_page_idx and y0 >= next_section_y0:
                        continue
                    # 헤더 y 갱신
                    if self.RE_TABLE_HEADER.search(_clean(text)):
                        header_y0 = y0
                    all_page_blocks.append((pidx, b))

            # 주석 시작 y 찾기 (fallback end_y로 사용)
            for pidx, b in all_page_blocks:
                if self.RE_NOTE_START.match(_clean(b[4])):
                    if pidx == section_page_idx or pidx == next_section_page_idx:
                        end_y = min(end_y, b[1])
                    break

            benefits_cb = _fallback_parse_by_coords(all_page_blocks, header_y0, end_y)
            score_cb = _benefit_quality_score(benefits_cb)
            logger.debug("fallback 품질 점수: %.2f (급부 %d건)", score_cb, len(benefits_cb))

            # 둘 중 더 나은 결과 선택
            if score_cb >= score:
                result["benefits"] = benefits_cb
            else:
                result["benefits"] = benefits_ft
                result["_parse_method"] = "find_tables"

        # ── 주석 ──────────────────────────────────────────────────────────────
        note_blocks = self._collect_note_blocks(
            doc, section_page_idx, section_y0,
            next_section_page_idx, next_section_y0,
        )
        result["notes"] = _extract_notes_from_blocks(note_blocks)

        return result

    # ── 헬퍼 ─────────────────────────────────────────────────────────────────

    def _page_clip(
        self,
        page: fitz.Page,
        pidx: int,
        section_page_idx: int,
        section_y0: float,
        next_section_page_idx: int,
        next_section_y0: float,
    ) -> Optional[fitz.Rect]:
        rect = page.rect
        y0 = section_y0 if pidx == section_page_idx else 0
        y1 = next_section_y0 if pidx == next_section_page_idx else rect.height
        if y0 >= y1:
            return None
        return fitz.Rect(rect.x0, y0, rect.x1, y1)

    def _collect_note_blocks(
        self,
        doc: fitz.Document,
        section_page_idx: int,
        section_y0: float,
        next_section_page_idx: int,
        next_section_y0: float,
    ) -> list[tuple]:
        """주) 블록 이후 주석 텍스트 블록 수집.

        주석(notes)은 섹션 종료 경계(next_section_y0)를 넘어서도 이어질 수 있으므로
        (예: 11번 주석이 다음 페이지 ② 섹션 직전 블록에 포함),
        섹션 종료 경계 대신 비-주석 섹션 마커가 나올 때까지 수집한다.
        """
        RE_STOP = re.compile(
            r"②\s*보험급부별|③\s*보험급부별|◆\s*보험료\s*산출|◆\s*계약자배당|◆\s*해약환급"
        )
        note_blocks: list[tuple] = []
        in_notes = False

        for pidx in range(section_page_idx, len(doc)):
            for b in _extract_page_blocks(doc[pidx]):
                x0, y0, x1, y1, text = b
                tc = _clean(text)

                # 섹션 시작 전은 스킵
                if pidx == section_page_idx and y0 < section_y0:
                    continue

                # 다음 섹션 헤더(□ 무배당 ...) 위치 이후는 주석 영역이 아님
                if pidx == next_section_page_idx and y0 >= next_section_y0:
                    return note_blocks
                if pidx > next_section_page_idx:
                    return note_blocks

                # 비-주석 섹션 마커 처리
                stop_m = RE_STOP.search(tc)
                if stop_m:
                    # 마커 이전 텍스트가 있으면 주석에 포함 후 종료
                    prefix = tc[: stop_m.start()].strip()
                    if in_notes and prefix:
                        note_blocks.append((x0, y0, x1, y1, prefix))
                    return note_blocks

                if self.RE_NOTE_START.match(tc):
                    in_notes = True

                if in_notes:
                    note_blocks.append(b)

        return note_blocks


# ---------------------------------------------------------------------------
# 상품 구성 목록 파서
# ---------------------------------------------------------------------------

class ComponentListParser:
    """'① 상품의 구성' 섹션에서 특약 목록 추출."""

    def parse(self, full_text: str) -> dict:
        result: dict = {"riders": []}

        m = re.search(r"①\s*상품의\s*구성(.+?)(?=②|◆|$)", full_text, re.DOTALL)
        if not m:
            return result

        section = m.group(1)
        lines = [l.strip() for l in section.split("\n") if l.strip()]

        for line in lines:
            if re.match(r"^주계약\s*$", line):
                continue
            opt_m = re.match(r"\+\s*\(무\)(.+?)\s*\(선택특약\)", line)
            if opt_m:
                result["riders"].append(
                    "무배당" + opt_m.group(1).strip()
                )
                continue
            mand_m = re.match(r"\+\s*\(무\)(.+?)\s*\(의무부가특약\)", line)
            if mand_m:
                result["riders"].append(
                    "무배당" + mand_m.group(1).strip()
                )

        return result


# ---------------------------------------------------------------------------
# □ 계약 섹션 탐색
# ---------------------------------------------------------------------------

class ContractSectionFinder:
    """PDF에서 '□ 무배당 ...' 단위 계약 섹션을 찾아 페이지+y 위치 반환."""

    RE_CONTRACT_MARKER = re.compile(r"□\s+무배당")

    def find_sections(self, doc: fitz.Document) -> list[dict]:
        sections = []
        for page_idx in range(len(doc)):
            blocks = _extract_page_blocks(doc[page_idx])
            for x0, y0, x1, y1, text in blocks:
                if self.RE_CONTRACT_MARKER.search(text):
                    name = _clean(text)
                    name = re.sub(r"^□\s*", "", name)
                    name = re.sub(r"\s*\(의무부가특약\)\s*$", "", name)
                    name = re.sub(r"\s*\(선택특약\)\s*$", "", name)
                    sections.append({
                        "name": name.strip(),
                        "page_idx": page_idx,
                        "y0": y0,
                    })
        return sections


# ---------------------------------------------------------------------------
# 메인 파서: 암보험 주계약 상품요약서
# ---------------------------------------------------------------------------

class LinaMainSummaryParser:
    """라이나 암보험 주계약 상품요약서 PDF 파싱."""

    def __init__(self):
        self.section_finder = ContractSectionFinder()
        self.benefit_parser = BenefitSectionParser()
        self.component_parser = ComponentListParser()

    def parse_pdf(self, pdf_path: Path) -> dict:
        pdf_path = Path(pdf_path)
        doc = fitz.open(str(pdf_path))

        full_text = "\n".join(page.get_text() for page in doc)

        result = {
            "product_name": _get_product_name(full_text),
            "management_no": _get_management_no(full_text),
            "source_pdf": str(pdf_path),
            "components": self.component_parser.parse(full_text),
            "contracts": [],
        }

        sections = self.section_finder.find_sections(doc)

        if not sections:
            logger.warning("□ 섹션을 찾지 못함: %s", pdf_path)
            doc.close()
            return result

        # 다음 ◆ 섹션 위치 (전체 섹션 범위 종료 경계)
        RE_NEXT_MAIN = re.compile(
            r"^③\s*보험급부별|^◆\s*보험료\s*산출|^◆\s*계약자배당|^◆\s*해약환급"
        )
        global_end_page, global_end_y = self._find_global_end(doc, RE_NEXT_MAIN)

        for i, sec in enumerate(sections):
            if i + 1 < len(sections):
                next_page_idx = sections[i + 1]["page_idx"]
                next_y0 = sections[i + 1]["y0"]
            else:
                next_page_idx = global_end_page
                next_y0 = global_end_y

            parsed = self.benefit_parser.parse(
                doc,
                sec["page_idx"], sec["y0"],
                next_page_idx, next_y0,
            )

            if re.match(r"^주\s*계\s*약$", sec["name"].strip()):
                continue

            result["contracts"].append({
                "name": sec["name"],
                "type": "rider",
                "source_pdf": str(pdf_path),
                "reference_amount": parsed["reference_amount"],
                "benefits": parsed["benefits"],
                "notes": parsed["notes"],
            })
            if parsed.get("_parse_method") == "coord_fallback":
                logger.info("  → 좌표 fallback 사용: %s", sec["name"])

        doc.close()
        return result

    def _find_global_end(
        self, doc: fitz.Document, pattern: re.Pattern
    ) -> tuple[int, float]:
        for pidx in range(len(doc)):
            for _, y0, _, _, text in _extract_page_blocks(doc[pidx]):
                if pattern.search(_clean(text)):
                    return pidx, y0
        return len(doc) - 1, 9999.0


# ---------------------------------------------------------------------------
# 선택특약 파서
# ---------------------------------------------------------------------------

class LinaRiderSummaryParser:
    """라이나 선택특약(암특약) 상품요약서 PDF 파싱."""

    def __init__(self):
        self.benefit_parser = BenefitSectionParser()

    RE_BENEFIT_SECTION = re.compile(
        r"①\s*보험금\s*지급사유\s*예시|◆\s*보험금\s*지급사유",
        re.MULTILINE,
    )
    RE_NEXT_SECTION = re.compile(
        r"②\s*보험급부별|③\s*보험급부별|◆\s*보험료\s*산출|◆\s*계약자배당|◆\s*해약환급"
    )

    def parse_pdf(self, pdf_path: Path) -> dict:
        pdf_path = Path(pdf_path)
        doc = fitz.open(str(pdf_path))
        full_text = "\n".join(page.get_text() for page in doc)

        result = {
            "product_name": _get_product_name(full_text),
            "management_no": _get_management_no(full_text),
            "source_pdf": str(pdf_path),
            "type": "rider",
            "contracts": [],
        }

        benefit_page_idx, benefit_y0 = self._find_benefit_section(doc)

        if benefit_page_idx is None:
            logger.warning("급부 섹션 못 찾음: %s", pdf_path)
            doc.close()
            return result

        next_page_idx, next_y0 = self._find_next_section(doc, benefit_page_idx, benefit_y0)

        parsed = self.benefit_parser.parse(
            doc,
            benefit_page_idx, benefit_y0,
            next_page_idx, next_y0,
        )

        result["contracts"].append({
            "name": result["product_name"],
            "type": "rider",
            "source_pdf": str(pdf_path),
            "reference_amount": parsed["reference_amount"],
            "benefits": parsed["benefits"],
            "notes": parsed["notes"],
        })
        if parsed.get("_parse_method") == "coord_fallback":
            logger.info("  → 좌표 fallback 사용: %s", result["product_name"])

        doc.close()
        return result

    def _find_benefit_section(
        self, doc: fitz.Document
    ) -> tuple[Optional[int], float]:
        RE = re.compile(r"①\s*보험금\s*지급사유\s*예시|◆\s*보험금\s*지급사유\s*및\s*지급제한")
        for pidx in range(len(doc)):
            for _, y0, _, _, text in _extract_page_blocks(doc[pidx]):
                if RE.search(_clean(text)):
                    return pidx, y0
        return None, 0.0

    def _find_next_section(
        self, doc: fitz.Document, start_page: int, start_y: float
    ) -> tuple[int, float]:
        for pidx in range(start_page, len(doc)):
            for _, y0, _, _, text in _extract_page_blocks(doc[pidx]):
                if pidx == start_page and y0 <= start_y + 20:
                    continue
                if self.RE_NEXT_SECTION.search(_clean(text)):
                    return pidx, y0
        return len(doc) - 1, 9999.0


# ---------------------------------------------------------------------------
# 통합 파서
# ---------------------------------------------------------------------------

class LinaProductSummaryParser:
    """라이나 암보험 1개 상품 전체 상품요약서 파싱 → JSON 저장."""

    def __init__(self):
        self.main_parser = LinaMainSummaryParser()
        self.rider_parser = LinaRiderSummaryParser()

    def parse_product(
        self,
        main_summary_pdf: Path,
        rider_summary_dirs: list[Path] | None = None,
    ) -> dict:
        main_result = self.main_parser.parse_pdf(Path(main_summary_pdf))
        product = {
            "product_name": main_result["product_name"],
            "management_no": main_result["management_no"],
            "components": main_result["components"],
            "contracts": list(main_result["contracts"]),
        }

        if rider_summary_dirs:
            for rider_dir in rider_summary_dirs:
                for pdf_path in sorted(Path(rider_dir).glob("*.pdf")):
                    try:
                        r = self.rider_parser.parse_pdf(pdf_path)
                        product["contracts"].extend(r["contracts"])
                    except Exception as e:
                        logger.error("선택특약 파싱 실패 %s: %s", pdf_path, e)

        return product

    def parse_product_auto(
        self,
        main_summary_pdf: Path,
        rider_summary_base_dir: Path,
    ) -> dict:
        """메인 PDF 파싱 후 components에서 특약을 자동으로 찾아 파싱."""
        main_result = self.main_parser.parse_pdf(Path(main_summary_pdf))
        product = {
            "product_name": main_result["product_name"],
            "management_no": main_result["management_no"],
            "components": main_result["components"],
            "contracts": list(main_result["contracts"]),
        }

        riders = main_result["components"].get("riders", [])
        rider_base = Path(rider_summary_base_dir)

        for rider_name in riders:
            candidates = self._find_rider_dir(rider_name, rider_base)
            if not candidates:
                logger.warning("선택특약 폴더 못 찾음: %s", rider_name)
                continue
            for rider_dir in candidates:
                for pdf_path in sorted(rider_dir.glob("*.pdf")):
                    try:
                        r = self.rider_parser.parse_pdf(pdf_path)
                        product["contracts"].extend(r["contracts"])
                        logger.info("선택특약 파싱: %s", rider_dir.name)
                    except Exception as e:
                        logger.error("선택특약 파싱 실패 %s: %s", pdf_path, e)

        return product

    def _find_rider_dir(self, rider_name: str, base_dir: Path) -> list[Path]:
        rider_norm = re.sub(r"\s+", "", rider_name)
        results = []
        for d in base_dir.iterdir():
            if not d.is_dir():
                continue
            dir_norm = re.sub(r"\s+", "", d.name)
            if d.name == rider_name or dir_norm == rider_norm:
                return [d]
            if rider_norm in dir_norm or dir_norm in rider_norm:
                results.append(d)
        return results

    def parse_pdf(self, pdf_path: Path) -> dict:
        """ProductBundleParser 호환 인터페이스.

        주계약 요약서 PDF를 파싱하고,
        같은 상품의 선택특약 요약서 디렉토리가 자동으로 감지되면 함께 파싱한다.

        라이나 파일 배치 규칙:
          .../linalife/암보험/상품요약서/{상품명}/*.pdf  ← summary_pdf
          .../linalife/암특약/상품요약서/                ← rider_summary_base_dir (자동 탐색)
        """
        pdf_path = Path(pdf_path)
        # 선택특약 기본 경로 추론: 형제 디렉토리 "암특약/상품요약서"
        # 경로 구조: .../{회사}/암보험/상품요약서/{상품}/파일.pdf
        try:
            company_root = pdf_path.parents[3]  # linalife/
            rider_base = company_root / "암특약" / "상품요약서"
        except IndexError:
            rider_base = None

        if rider_base and rider_base.exists():
            return self.parse_product_auto(
                main_summary_pdf=pdf_path,
                rider_summary_base_dir=rider_base,
            )
        return self.main_parser.parse_pdf(pdf_path)

    @staticmethod
    def save_json(data: dict, output_path: Path, indent: int = 2) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        logger.info("JSON 저장: %s", output_path)


# ---------------------------------------------------------------------------
# ProductBundleParser 자동 등록
# ---------------------------------------------------------------------------
# 이 파일이 import되면 라이나생명 파서를 번들 파서 레지스트리에 자동 등록한다.
# 새 회사를 추가할 때는 해당 회사의 summary_parser 파일 끝에 동일 패턴으로 등록하면 됨.

def _register_lina_parsers() -> None:
    try:
        from .product_bundle_parser import register_summary_parser
        register_summary_parser("라이나생명", LinaProductSummaryParser)
        register_summary_parser("linalife", LinaProductSummaryParser)
    except ImportError:
        pass  # product_bundle_parser 없을 때 graceful skip


_register_lina_parsers()
