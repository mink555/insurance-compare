"""라이나생명 상품요약서 전용 파서.

라이나 PDF 구조:
  1. 주계약 요약서: "□ 무배당 ..." 단위로 계약 섹션 분할
     - "① 상품의 구성" 에 특약 목록
     - 각 섹션에 급부 테이블 + 주석
  2. 선택특약 요약서: "① 보험금 지급사유 예시" or "◆ 보험금 지급사유" 섹션 1개

라이나 전용 로직:
  - ContractSectionFinder: "□ 무배당" 패턴으로 섹션 경계(page+y) 탐색
  - BenefitSectionParser: find_tables 1차 → 좌표 기반 fallback 2차
  - 좌표 상수 (COL_NAME_MAX 등): 라이나 PDF 레이아웃 기준
  - _normalize_note_text: 주석 텍스트 내 한글 어절 분절 복원
  - 주석 블록 수집: 페이지 경계를 넘는 번호 주석 처리

공통 로직 (utils.py):
  - is_benefit_table, find_benefit_columns
  - split_benefit_names, extract_reference_amount, parse_amounts_from_cell
  - clean, normalize_benefit_name
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

import fitz

from .product_bundle_parser import BaseSummaryParser, register_summary_parser
from .utils import (
    clean,
    extract_reference_amount,
    find_benefit_columns,
    is_benefit_table,
    normalize_benefit_name,
    parse_amounts_from_cell,
    split_benefit_names,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 라이나 전용: 주석 텍스트 정규화
# ---------------------------------------------------------------------------

def _normalize_note_text(text: str) -> str:
    """주석·reduction_note 전용 정규화: PDF 텍스트 레이어 분절 복원.

    복원 전략:
      1. 번호 경계 보호   : \\n + 숫자.공백 → 마커로 치환
      2. 구조적 \\n 제거  : 한글↔한글, 한글↔영숫자, 숫자+조사, 따옴표/괄호 경계
      3. 연속 공백 정리
      4. 번호 경계 복원
      5. whitelist 기반 공백 분절 복원
    """
    if not text:
        return ""

    text = re.sub(r"\n(?=\d{1,2}\.\s)", "\x00NEWNUM\x00", text)

    text = re.sub(r"(?<=[가-힣])\n(?=[가-힣])", "", text)
    text = re.sub(r"(?<=[가-힣])\n(?=[A-Za-z0-9])", "", text)
    text = re.sub(r"(?<=[A-Za-z0-9])\n(?=[가-힣])", "", text)

    _JOSA = r"[이가을를은는의에으]"
    text = re.sub(r"(?<=[0-9년개월회%])\n(?=" + _JOSA + r")", "", text)

    text = re.sub(r'(?<=["\'\)\]])\n(?=[가-힣])', "", text)
    text = re.sub(r'(?<=[가-힣])\n(?=["\'\(\[])', "", text)

    text = re.sub(r"[ \t\n]+", " ", text)
    text = text.replace("\x00NEWNUM\x00", " ")
    text = _repair_broken_korean_phrases(text)

    return text.strip()


# 공백 분절 복원 whitelist (PDF 렌더링 단위 쪼개짐으로 생긴 어절 내 공백만 등록)
_BROKEN_PHRASE_MAP: list[tuple[str, str]] = [
    ("보험계 약", "보험계약"),
    ("피보험 자", "피보험자"),
    ("계약 자",   "계약자"),
    ("수익 자",   "수익자"),
    ("금 액",     "금액"),
    ("사 유",     "사유"),
    ("보험 금",   "보험금"),
    ("납 입",     "납입"),
    ("진단 확정", "진단확정"),
    ("진단확 정", "진단확정"),
]

_BROKEN_PHRASE_PATTERNS = [
    (re.compile(re.escape(broken)), fixed)
    for broken, fixed in _BROKEN_PHRASE_MAP
]


def _repair_broken_korean_phrases(text: str) -> str:
    if not text:
        return text
    result = text
    for pattern, fixed in _BROKEN_PHRASE_PATTERNS:
        new = pattern.sub(fixed, result)
        if new != result:
            logger.debug("_repair: '%s' → '%s'", pattern.pattern.replace("\\", ""), fixed)
            result = new
    return result


# ---------------------------------------------------------------------------
# 라이나 전용: 상품명 / 관리번호 추출
# ---------------------------------------------------------------------------

def _get_management_no(text: str) -> str:
    m = re.search(r"관리번호\s*([\w\-]+)", text)
    return m.group(1).strip() if m else ""


def _get_product_name(full_text: str) -> str:
    m = re.search(r"(무배당\s+.+?)(?:\s*상품요약\s*서|\s*상품\s*요약\s*서)", full_text)
    return clean(m.group(1)) if m else ""


# ---------------------------------------------------------------------------
# 라이나 전용: 급부 테이블 파싱
# ---------------------------------------------------------------------------

RE_MANAGEMENT_NO = re.compile(r"^관리번호\s+[\d\-]+")


def _parse_benefit_table(tab: fitz.table.Table) -> list[dict]:
    """find_tables()로 찾은 테이블에서 급부 리스트 추출.

    merged cell(None) → 이전 행 값 carry-over.
    """
    rows = tab.extract()
    if not rows:
        return []

    cols = find_benefit_columns(rows[0])
    name_idx = cols["benefit"]
    trigger_idx = cols["trigger"]
    amount_cond_idx = cols["amount"]
    amount_val_idx = cols["condition"]  # merged cell 구조의 금액값 컬럼

    if name_idx == -1:
        n = len(rows[0])
        if n >= 3:
            name_idx, trigger_idx, amount_cond_idx = 0, 1, 2
        else:
            return []

    benefits: list[dict] = []
    current_name = ""
    current_trigger = ""

    for row in rows[1:]:
        def get(idx: int) -> str:
            if idx is None or idx < 0 or idx >= len(row):
                return ""
            return clean(row[idx] or "")

        def get_name(idx: int) -> str:
            if idx is None or idx < 0 or idx >= len(row):
                return ""
            return normalize_benefit_name(row[idx] or "")

        name_cell = get_name(name_idx)
        trigger_cell = normalize_benefit_name(row[trigger_idx] or "") if trigger_idx is not None and 0 <= trigger_idx < len(row) else ""
        amount_cond_cell = get(amount_cond_idx)
        amount_val_cell = get(amount_val_idx) if amount_val_idx is not None else ""

        if name_cell:
            current_name = name_cell
        if trigger_cell:
            current_trigger = trigger_cell

        if amount_val_idx is not None and amount_val_cell:
            combined = f"{amount_cond_cell} {amount_val_cell}".strip()
            amounts = parse_amounts_from_cell(combined)
        else:
            amounts = parse_amounts_from_cell(amount_cond_cell)

        if name_cell:
            benefits.append({
                "benefit_names": split_benefit_names(current_name),
                "trigger": current_trigger,
                "amounts": amounts,
            })
        elif amounts and benefits:
            benefits[-1]["amounts"].extend(amounts)

    return benefits


# ---------------------------------------------------------------------------
# 라이나 전용: 품질 점수 (fallback 판단용)
# ---------------------------------------------------------------------------

def _benefit_quality_score(benefits: list[dict]) -> float:
    """파싱 결과 품질 점수 (0~1).

    채점: 급부 1건 이상(+0.3) + 모두 이름 있음(+0.3) + 절반 이상 금액 있음(+0.4)
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
# 라이나 전용: 좌표 기반 Fallback
# ---------------------------------------------------------------------------

# 라이나 PDF 레이아웃 기준 컬럼 x 경계 (포인트)
# 다른 보험사 PDF에는 적용하지 않는다.
_COL_NAME_MAX = 130
_COL_TRIGGER_MIN = 125
_COL_TRIGGER_MAX = 315
_COL_AMOUNT_MIN = 315
_ROW_GAP_THRESHOLD = 40
_TABLE_HEADER_Y_MARGIN = 20

_RE_TABLE_HEADER_LINE = re.compile(r"급\s*부\s*명|지\s*급\s*사\s*유|지\s*급\s*금\s*액")
_RE_AMOUNT = re.compile(r"(\d[\d,]*)\s*만\s*원")


def _col_of(x0: float) -> str:
    if x0 < _COL_NAME_MAX:
        return "name"
    if _COL_TRIGGER_MIN <= x0 < _COL_TRIGGER_MAX:
        return "trigger"
    if x0 >= _COL_AMOUNT_MIN:
        return "amount"
    return "other"


def _parse_amounts_from_texts(texts: list[str]) -> list[dict]:
    """amount 컬럼 블록 텍스트 리스트 → 조건별 금액 추출 (좌표 fallback 전용)."""
    amounts: list[dict] = []
    pending_condition = ""
    for raw in texts:
        text = clean(raw)
        if not text:
            continue
        amount_m = _RE_AMOUNT.search(text)
        if amount_m:
            pre = text[:amount_m.start()].strip()
            condition = clean(f"{pending_condition} {pre}") if pre else clean(pending_condition)
            reduction_note = ""
            rm = re.search(r"\(단[,，]\s*(보험계약일(?:로)?부터\s*\d+년.*?(?:지급|적용)(?:함)?)\)", text)
            if rm:
                reduction_note = clean(rm.group(1))
            entry: dict = {"condition": condition, "amount": clean(amount_m.group(0))}
            if reduction_note:
                entry["reduction_note"] = reduction_note
            amounts.append(entry)
            pending_condition = ""
        else:
            if re.match(r"^\(단[,，]", text) and amounts:
                amounts[-1]["reduction_note"] = text
            else:
                pending_condition = clean(f"{pending_condition} {text}")
    return amounts


def _extract_page_blocks(page: fitz.Page) -> list[tuple]:
    """(x0, y0, x1, y1, text) 튜플 리스트, y0 순 정렬."""
    blocks = []
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, text, _, block_type = b
        if block_type == 0 and text.strip():
            blocks.append((x0, y0, x1, y1, text.strip()))
    blocks.sort(key=lambda b: (round(b[1] / 5) * 5, b[0]))
    return blocks


def _fallback_parse_by_coords(
    page_blocks: list[tuple[int, tuple]],
    header_y0: float,
    end_y: float,
) -> list[dict]:
    """x 좌표 기반 fallback 파싱 (라이나 PDF 전용).

    find_tables() 품질이 낮을 때 사용.
    page_blocks: (page_idx, (x0,y0,x1,y1,text)) 형태, y 순 정렬
    """
    table_blocks: list[tuple[int, tuple]] = [
        (pidx, b) for pidx, b in page_blocks
        if not (b[1] <= header_y0 + _TABLE_HEADER_Y_MARGIN)
        and not (b[1] >= end_y)
        and not _RE_TABLE_HEADER_LINE.search(clean(b[4]))
    ]
    if not table_blocks:
        return []

    name_blocks = sorted(
        [(pidx, b) for pidx, b in table_blocks if _col_of(b[0]) == "name"],
        key=lambda x: (x[0], x[1][1]),
    )
    if not name_blocks:
        return []

    row_groups: list[list[tuple[int, tuple]]] = []
    cur = [name_blocks[0]]
    for i in range(1, len(name_blocks)):
        pp, pb = cur[-1]
        cp, cb = name_blocks[i]
        if cp != pp or (cb[1] - pb[3]) > _ROW_GAP_THRESHOLD:
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
            search_start_page = grp_page
            search_start_y = header_y0 + _TABLE_HEADER_Y_MARGIN

        if grp_idx + 1 < len(row_groups):
            next_page = row_groups[grp_idx + 1][0][0]
            next_y = row_groups[grp_idx + 1][0][1][1]
        else:
            next_page, next_y = 9999, 9999.0

        name_texts, trigger_texts, amount_texts = [], [], []
        for _, b in group:
            name_texts.append(clean(b[4]))

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
                trigger_texts.append(clean(b[4]))
            elif col == "amount":
                amount_texts.append(clean(b[4]))

        name_text = normalize_benefit_name(" ".join(name_texts))
        trigger_text = clean(" ".join(trigger_texts))
        amounts = _parse_amounts_from_texts(amount_texts)
        bnames = split_benefit_names(name_text)

        if bnames or trigger_text:
            benefits.append({
                "benefit_names": bnames,
                "trigger": trigger_text,
                "amounts": amounts,
            })

    return benefits


# ---------------------------------------------------------------------------
# 라이나 전용: 주석 추출
# ---------------------------------------------------------------------------

_RE_NOTE_BLOCK_START = re.compile(r"^주\s*\)")
_RE_NOTE_NUMBERED = re.compile(r"^(\d+)\.\s*(.+)", re.DOTALL)


def _split_numbered_notes(text: str) -> list[str]:
    """"10. 내용 11. 내용" 형태 텍스트를 번호별로 분리."""
    pattern = re.compile(r"(?:^|(?<=\s))\d{1,2}\.\s")
    split_points = [m.start() for m in pattern.finditer(text)]
    if not split_points:
        return [text.strip()] if text.strip() else []
    parts = []
    for i, start in enumerate(split_points):
        end = split_points[i + 1] if i + 1 < len(split_points) else len(text)
        part = text[start:end].strip()
        if part:
            parts.append(part)
    return parts


def _extract_notes_from_blocks(blocks: list[tuple]) -> list[str]:
    """주) 이후 번호 주석 추출.

    블록 전체를 \\n으로 병합 후 _normalize_note_text → 번호별 분리.
    페이지 경계에서 쪼개진 주석(10번, 11번 등)을 올바르게 처리한다.

    종료 경계: 다음 테이블 시작 신호 감지 시 수집 종료.
    """
    RE_NEXT_TABLE = re.compile(
        r"급\s*부\s*명.{0,10}지\s*급\s*사\s*유"
        r"|지\s*급\s*사\s*유.{0,10}지\s*급\s*금\s*액"
        r"|\[기준\s*[:：]"
        r"|-\s*\d+\s*형"
        r"|◆\s*보험금\s*지급사유"
        r"|①\s*보험금\s*지급사유"
    )

    raw_parts: list[str] = []
    in_notes = False

    for _, _, _, _, text in blocks:
        text_c = clean(text)
        if not text_c:
            continue
        if RE_MANAGEMENT_NO.match(text_c):
            continue
        if _RE_NOTE_BLOCK_START.match(text_c):
            in_notes = True
            rest = re.sub(r"^주\s*\)\s*", "", text, count=1).strip()
            if rest:
                raw_parts.append(rest)
            continue
        if not in_notes:
            continue
        if RE_NEXT_TABLE.search(text_c):
            break
        raw_parts.append(text)

    if not raw_parts:
        return []

    normalized = _normalize_note_text("\n".join(raw_parts))
    return _split_numbered_notes(normalized)


# ---------------------------------------------------------------------------
# 라이나 전용: BenefitSectionParser
# ---------------------------------------------------------------------------

class BenefitSectionParser:
    """PDF 문서의 y 범위(섹션)에서 급부 테이블과 주석을 파싱.

    파싱 전략:
      1차: find_tables() — 실선 기반 구조 인식
      품질 점수 < QUALITY_THRESHOLD 이면:
      2차: 좌표 기반 fallback (라이나 PDF 레이아웃 기준 x 좌표 상수 사용)
    """

    QUALITY_THRESHOLD = 0.5

    _RE_NOTE_START = re.compile(r"^주\s*\)")
    _RE_TABLE_HEADER = re.compile(r"급\s*부\s*명.*지\s*급\s*사\s*유.*지\s*급\s*금\s*액")

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
            "_parse_method": "find_tables",
        }

        for pidx in range(section_page_idx, min(next_section_page_idx + 1, len(doc))):
            ref = extract_reference_amount(doc[pidx].get_text())
            if ref:
                result["reference_amount"] = ref
                break

        # 1차: find_tables
        benefits_ft: list[dict] = []
        for pidx in range(section_page_idx, min(next_section_page_idx + 1, len(doc))):
            page = doc[pidx]
            clip = self._page_clip(page, pidx, section_page_idx, section_y0,
                                   next_section_page_idx, next_section_y0)
            tabs = page.find_tables(clip=clip) if clip else page.find_tables()
            for tab in tabs.tables:
                if is_benefit_table(tab.extract()[0] if tab.extract() else []):
                    benefits_ft.extend(_parse_benefit_table(tab))

        score = _benefit_quality_score(benefits_ft)
        logger.debug("find_tables 품질: %.2f (%d건)", score, len(benefits_ft))

        if score >= self.QUALITY_THRESHOLD:
            result["benefits"] = benefits_ft
        else:
            logger.debug("품질 %.2f < %.2f → 좌표 fallback", score, self.QUALITY_THRESHOLD)
            result["_parse_method"] = "coord_fallback"

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
                    if self._RE_TABLE_HEADER.search(clean(text)):
                        header_y0 = y0
                    all_page_blocks.append((pidx, b))

            for pidx, b in all_page_blocks:
                if self._RE_NOTE_START.match(clean(b[4])):
                    if pidx in (section_page_idx, next_section_page_idx):
                        end_y = min(end_y, b[1])
                    break

            benefits_cb = _fallback_parse_by_coords(all_page_blocks, header_y0, end_y)
            score_cb = _benefit_quality_score(benefits_cb)
            logger.debug("fallback 품질: %.2f (%d건)", score_cb, len(benefits_cb))

            result["benefits"] = benefits_cb if score_cb >= score else benefits_ft
            if score_cb < score:
                result["_parse_method"] = "find_tables"

        note_blocks = self._collect_note_blocks(
            doc, section_page_idx, section_y0, next_section_page_idx, next_section_y0
        )
        result["notes"] = _extract_notes_from_blocks(note_blocks)
        return result

    def _page_clip(self, page, pidx, sec_page, sec_y0, next_page, next_y0) -> Optional[fitz.Rect]:
        rect = page.rect
        y0 = sec_y0 if pidx == sec_page else 0
        y1 = next_y0 if pidx == next_page else rect.height
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
        """주) 블록 이후 주석 텍스트 수집.

        섹션 종료 경계를 넘어서도 이어질 수 있으므로
        비-주석 섹션 마커가 나올 때까지 수집한다.
        """
        RE_STOP = re.compile(
            r"②\s*보험급부별|③\s*보험급부별|◆\s*보험료\s*산출|◆\s*계약자배당|◆\s*해약환급"
        )
        note_blocks: list[tuple] = []
        in_notes = False

        for pidx in range(section_page_idx, len(doc)):
            for b in _extract_page_blocks(doc[pidx]):
                x0, y0, x1, y1, text = b
                tc = clean(text)
                if pidx == section_page_idx and y0 < section_y0:
                    continue
                if pidx == next_section_page_idx and y0 >= next_section_y0:
                    return note_blocks
                if pidx > next_section_page_idx:
                    return note_blocks
                stop_m = RE_STOP.search(tc)
                if stop_m:
                    prefix = tc[: stop_m.start()].strip()
                    if in_notes and prefix:
                        note_blocks.append((x0, y0, x1, y1, prefix))
                    return note_blocks
                if _RE_NOTE_BLOCK_START.match(tc):
                    in_notes = True
                if in_notes:
                    note_blocks.append(b)

        return note_blocks


# ---------------------------------------------------------------------------
# 라이나 전용: ContractSectionFinder
# ---------------------------------------------------------------------------

class ContractSectionFinder:
    """PDF에서 '□ 무배당 ...' 단위 계약 섹션을 찾아 페이지+y 위치 반환."""

    _RE_MARKER = re.compile(r"□\s+무배당")

    def find_sections(self, doc: fitz.Document) -> list[dict]:
        sections = []
        for page_idx in range(len(doc)):
            for x0, y0, x1, y1, text in _extract_page_blocks(doc[page_idx]):
                if self._RE_MARKER.search(text):
                    name = clean(text)
                    name = re.sub(r"^□\s*", "", name)
                    name = re.sub(r"\s*\(의무부가특약\)\s*$", "", name)
                    name = re.sub(r"\s*\(선택특약\)\s*$", "", name)
                    sections.append({"name": name.strip(), "page_idx": page_idx, "y0": y0})
        return sections


# ---------------------------------------------------------------------------
# 라이나 전용: ComponentListParser
# ---------------------------------------------------------------------------

class ComponentListParser:
    """'① 상품의 구성' 섹션에서 특약 목록 추출."""

    def parse(self, full_text: str) -> dict:
        result: dict = {"riders": []}
        m = re.search(r"①\s*상품의\s*구성(.+?)(?=②|◆|$)", full_text, re.DOTALL)
        if not m:
            return result
        section = m.group(1)
        for line in (l.strip() for l in section.split("\n") if l.strip()):
            if re.match(r"^주\s*계\s*약\s*$", line):
                continue
            opt_m = re.match(r"\+\s*\(무\)(.+?)\s*\(선택특약\)", line)
            if opt_m:
                result["riders"].append("무배당" + opt_m.group(1).strip())
                continue
            mand_m = re.match(r"\+\s*\(무\)(.+?)\s*\(의무부가특약\)", line)
            if mand_m:
                result["riders"].append("무배당" + mand_m.group(1).strip())
        return result


# ---------------------------------------------------------------------------
# 라이나 주계약 요약서 파서
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

        RE_NEXT_MAIN = re.compile(
            r"^③\s*보험급부별|^◆\s*보험료\s*산출|^◆\s*계약자배당|^◆\s*해약환급"
        )
        global_end_page, global_end_y = self._find_global_end(doc, RE_NEXT_MAIN)

        for i, sec in enumerate(sections):
            next_page_idx = sections[i + 1]["page_idx"] if i + 1 < len(sections) else global_end_page
            next_y0 = sections[i + 1]["y0"] if i + 1 < len(sections) else global_end_y

            parsed = self.benefit_parser.parse(doc, sec["page_idx"], sec["y0"], next_page_idx, next_y0)

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

    def _find_global_end(self, doc: fitz.Document, pattern: re.Pattern) -> tuple[int, float]:
        for pidx in range(len(doc)):
            for _, y0, _, _, text in _extract_page_blocks(doc[pidx]):
                if pattern.search(clean(text)):
                    return pidx, y0
        return len(doc) - 1, 9999.0


# ---------------------------------------------------------------------------
# 라이나 선택특약 파서
# ---------------------------------------------------------------------------

class LinaRiderSummaryParser:
    """라이나 선택특약(암특약) 상품요약서 PDF 파싱."""

    def __init__(self):
        self.benefit_parser = BenefitSectionParser()

    _RE_BENEFIT_SECTION = re.compile(
        r"①\s*보험금\s*지급사유\s*예시|◆\s*보험금\s*지급사유",
        re.MULTILINE,
    )
    _RE_NEXT_SECTION = re.compile(
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
        parsed = self.benefit_parser.parse(doc, benefit_page_idx, benefit_y0, next_page_idx, next_y0)

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

    def _find_benefit_section(self, doc: fitz.Document) -> tuple[Optional[int], float]:
        RE = re.compile(r"①\s*보험금\s*지급사유\s*예시|◆\s*보험금\s*지급사유\s*및\s*지급제한")
        for pidx in range(len(doc)):
            for _, y0, _, _, text in _extract_page_blocks(doc[pidx]):
                if RE.search(clean(text)):
                    return pidx, y0
        return None, 0.0

    def _find_next_section(self, doc: fitz.Document, start_page: int, start_y: float) -> tuple[int, float]:
        for pidx in range(start_page, len(doc)):
            for _, y0, _, _, text in _extract_page_blocks(doc[pidx]):
                if pidx == start_page and y0 <= start_y + 20:
                    continue
                if self._RE_NEXT_SECTION.search(clean(text)):
                    return pidx, y0
        return len(doc) - 1, 9999.0


# ---------------------------------------------------------------------------
# 라이나 통합 파서 (ProductBundleParser 인터페이스)
# ---------------------------------------------------------------------------

class LinaProductSummaryParser(BaseSummaryParser):
    """라이나 암보험 상품요약서 파싱.

    주계약 PDF 파싱 후, 같은 상품의 선택특약 요약서 디렉토리를 자동 탐색한다.

    라이나 파일 배치 규칙:
      .../linalife/암보험/상품요약서/{상품명}/*.pdf  ← summary_pdf
      .../linalife/암특약/상품요약서/                ← rider_summary_base_dir (자동 탐색)
    """

    def __init__(self):
        self.main_parser = LinaMainSummaryParser()
        self.rider_parser = LinaRiderSummaryParser()

    def parse_pdf(self, pdf_path: Path) -> dict:
        pdf_path = Path(pdf_path)
        try:
            company_root = pdf_path.parents[3]
            rider_base = company_root / "암특약" / "상품요약서"
        except IndexError:
            rider_base = None

        if rider_base and rider_base.exists():
            return self.parse_product_auto(pdf_path, rider_base)
        return self.main_parser.parse_pdf(pdf_path)

    def parse_product_auto(self, main_summary_pdf: Path, rider_summary_base_dir: Path) -> dict:
        """주계약 PDF + 선택특약 기본 경로로 상품 전체를 파싱한다."""
        main_pdf = Path(main_summary_pdf)
        rider_base = Path(rider_summary_base_dir)
        return self._parse_product_auto(main_pdf, rider_base)

    def _parse_product_auto(self, main_pdf: Path, rider_base: Path) -> dict:
        main_result = self.main_parser.parse_pdf(main_pdf)
        product = {
            "product_name": main_result["product_name"],
            "management_no": main_result["management_no"],
            "components": main_result["components"],
            "contracts": list(main_result["contracts"]),
        }

        for rider_name in main_result["components"].get("riders", []):
            for rider_dir in self._find_rider_dir(rider_name, rider_base):
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

    @staticmethod
    def save_json(data: dict, output_path: Path, indent: int = 2) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        logger.info("JSON 저장: %s", output_path)


# ---------------------------------------------------------------------------
# 레지스트리 자동 등록
# ---------------------------------------------------------------------------

def _register() -> None:
    try:
        register_summary_parser("라이나생명", LinaProductSummaryParser)
        register_summary_parser("linalife", LinaProductSummaryParser)
    except Exception:
        pass


_register()
