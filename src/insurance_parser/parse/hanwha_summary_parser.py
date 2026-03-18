"""한화생명 상품요약서 전용 파서.

한화 PDF 구조:
  - 페이지마다 "■ 특약명(코드) 무배당" 헤더 → 1페이지 = 1특약
  - "(기준 : 특약가입금액 N만원)" 기준금액
  - "급부명칭 / 지급사유 / 지급금액(경과기간)" 테이블
    - 3열: 급부명칭 / 지급사유 / 지급금액
    - 4열: 급부명칭 / 지급사유 / 경과기간 / 금액
  - 소액질병 테이블: 동일 급부명 내 trigger(질병군)가 바뀌면 별도 행으로 분리

한화 전용 로직:
  - letter-spacing 제거 (_strip_spacing)
  - "■ 특약명(코드)" 헤더 + 코드 추출
  - 경과기간 컬럼 처리 (2행 헤더 대응)
  - 질병군 단위 benefit 분리

공통 로직 (utils.py):
  - is_benefit_table, find_benefit_columns
  - split_benefit_names, extract_reference_amount
  - clean, normalize_benefit_name
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import fitz

from .product_bundle_parser import BaseSummaryParser, register_summary_parser
from .utils import (
    clean,
    extract_reference_amount,
    find_benefit_columns,
    is_benefit_table,
    normalize_benefit_name,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 한화 전용 정규식
# ---------------------------------------------------------------------------

# ■ 특약명(코드) 무배당  (코드: KA4.1 형식)
RE_CONTRACT_HEADER = re.compile(
    r"■\s*(.+?)\(([A-Za-z0-9]+\.\d+(?:\.\d+)?)\)\s*무배당"
)
# ■ 주계약 (코드 없음) — 파싱 대상 제외
RE_MAIN_CONTRACT_HEADER = re.compile(r"■\s*(한화생명[^\n(]+무배당|주계약)")
# 페이지 번호 (N / M 형태)
RE_PAGE_NO = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")


# ---------------------------------------------------------------------------
# 한화 전용 유틸
# ---------------------------------------------------------------------------

def _strip_spacing(text: str) -> str:
    """한화 PDF의 letter-spacing 제거: '급 부 명 칭' → '급부명칭'."""
    return re.sub(r"(?<=[\uAC00-\uD7A3])\s+(?=[\uAC00-\uD7A3])", "", text)


def _normalize_amount(text: str) -> str:
    """금액 텍스트 공백 정리."""
    return re.sub(r"[ \t\n]+", " ", text or "").strip()


# ---------------------------------------------------------------------------
# 헤더 / 기준금액 추출
# ---------------------------------------------------------------------------

def _extract_contract_header(page_text: str) -> dict | None:
    """페이지 텍스트에서 ■ 특약명(코드) 무배당 + 기준금액을 추출한다.

    주계약 헤더(코드 없음)는 None 반환 — 파싱 대상이 아님.
    """
    m_rider = RE_CONTRACT_HEADER.search(page_text)
    if not m_rider:
        return None

    ref_amount = extract_reference_amount(page_text)
    full_name = clean(m_rider.group(0).lstrip("■").strip())
    return {
        "name": clean(m_rider.group(1)),
        "code": m_rider.group(2),
        "full_name": full_name,
        "reference_amount": ref_amount,
        "type": "rider",
    }


# ---------------------------------------------------------------------------
# 급부 테이블 파싱
# ---------------------------------------------------------------------------

def _resolve_columns(tab) -> tuple[dict, int, bool]:
    """테이블에서 컬럼 인덱스와 헤더 행 번호를 결정한다.

    한화는 헤더가 2행으로 나뉘어 경과기간 컬럼이 두 번째 행에 나타나는
    경우가 있으므로 최대 2행을 검사한다.

    Returns:
        (cols_dict, header_idx, has_condition)
    """
    rows = tab.extract()
    header_idx = 0
    for idx, row in enumerate(rows[:3]):
        joined = _strip_spacing(" ".join(str(c or "") for c in row))
        if "급부" in joined or "지급사유" in joined:
            header_idx = idx
            break

    header = [str(c or "") for c in rows[header_idx]]
    cols = find_benefit_columns(header)
    has_condition = cols["condition"] is not None

    # 2행 헤더: 다음 행에 '경과기간' 이 있는 경우
    if not has_condition and header_idx + 1 < len(rows):
        next_row = [str(c or "") for c in rows[header_idx + 1]]
        for idx2, cell in enumerate(next_row):
            if "경과기간" in _strip_spacing(cell):
                cols["condition"] = idx2
                cols["amount"] = min(idx2 + 1, len(header) - 1)
                has_condition = True
                header_idx += 1
                break

    return cols, header_idx, has_condition


def _is_valid_benefit(benefit: dict) -> bool:
    """파싱된 benefit이 실제 급부 데이터인지 검증."""
    names = benefit.get("benefit_names", [])
    if not names or not names[0]:
        return False
    name = names[0].strip()
    # 헤더 텍스트나 주석 헤더가 benefit_name으로 들어온 경우 제거
    if re.search(r"^■|^주\s*\)|급\s*부\s*명|지\s*급\s*사\s*유|지\s*급\s*금\s*액", name):
        return False
    if name.startswith("보험기간 중 피보험자") and not benefit.get("amounts"):
        return False
    return True


def _parse_benefit_table(tab) -> list[dict]:
    """find_tables() 결과 1개 테이블 → benefit 리스트.

    한화 소액질병 구조 처리:
      동일 benefit_name 내에서 trigger(질병군)가 바뀌면
      → 별도 benefit dict로 분리 (질병군 단위 1행 원칙).
    """
    rows = tab.extract()
    if not rows:
        return []

    cols, header_idx, has_condition = _resolve_columns(tab)

    benefits: list[dict] = []
    current_benefit: dict | None = None

    for row in rows[header_idx + 1:]:
        cells = [str(c or "").strip() for c in row]
        if not any(cells):
            continue

        def get(idx: int | None) -> str:
            if idx is None or idx < 0 or idx >= len(cells):
                return ""
            return cells[idx]

        benefit_name = normalize_benefit_name(get(cols["benefit"]))
        trigger = clean(get(cols["trigger"]))
        amount_raw = _normalize_amount(get(cols["amount"]))
        cond_raw = _normalize_amount(get(cols["condition"]))

        if benefit_name:
            current_benefit = {
                "benefit_names": [benefit_name],
                "trigger": trigger,
                "amounts": [],
            }
            benefits.append(current_benefit)

        elif trigger and current_benefit is not None:
            # 동일 benefit_name에 새 질병군 trigger → 별도 dict
            current_benefit = {
                "benefit_names": list(benefits[-1]["benefit_names"]) if benefits else [""],
                "trigger": trigger,
                "amounts": [],
            }
            benefits.append(current_benefit)

        if current_benefit is None:
            continue

        if not amount_raw and not cond_raw:
            # trigger 텍스트 연속 행 (셀 내 줄바꿈이 행으로 나뉜 경우)
            if trigger and not benefit_name:
                current_benefit["trigger"] = (current_benefit["trigger"] + " " + trigger).strip()
            continue

        # 금액 항목 생성
        if has_condition and cond_raw:
            amt_entry = {"condition": cond_raw, "amount": amount_raw, "reduction_note": ""}
        else:
            # 조건 컬럼 없음 → 줄바꿈으로 조건/금액 분리 시도
            parts = [p.strip() for p in amount_raw.split("\n") if p.strip()]
            if len(parts) >= 2 and re.search(r"\d+[만백천]?\s*원|\d+회당|%", parts[-1]):
                amt_entry = {"condition": " ".join(parts[:-1]), "amount": parts[-1], "reduction_note": ""}
            else:
                amt_entry = {"condition": "", "amount": amount_raw, "reduction_note": ""}

        current_benefit["amounts"].append(amt_entry)

    return [b for b in benefits if _is_valid_benefit(b)]


# ---------------------------------------------------------------------------
# 상품 구성 섹션 파싱
# ---------------------------------------------------------------------------

def _parse_components(doc: fitz.Document) -> dict:
    """PDF에서 '가. 상품의 구성' 테이블을 찾아 특약 목록을 반환한다.

    상품 구성 섹션은 보통 PDF 앞쪽(최대 25페이지) 에 위치한다.
    급부 테이블이 나타나는 페이지가 7페이지를 넘기면 섹션이 끝난 것으로 판단한다.
    """
    components: dict = {"riders": []}
    RE_SECTION = re.compile(r"상품의\s*구성|보험금\s*지급사유")
    RE_MAIN = re.compile(r"^주\s*계\s*약$|^주계약$")
    RE_RIDER = re.compile(r"선택\s*특약|부가\s*특약")
    RE_TABLE_HEADER = re.compile(r"구\s*분|내\s*용")
    RE_BENEFIT_TABLE = re.compile(r"급부명칭|지급사유|급부명")

    found_section = False
    mode = ""

    for pidx in range(min(25, len(doc))):
        page = doc[pidx]
        text = page.get_text("text")

        if RE_SECTION.search(text):
            found_section = True

        # 급부 테이블이 나오면 종료 (상품 구성 섹션 이후)
        if found_section and RE_BENEFIT_TABLE.search(text) and pidx > 6:
            break

        if not found_section:
            continue

        tabs = page.find_tables()
        if not tabs or not tabs.tables:
            _parse_components_from_text(text, components)
            continue

        for tab in tabs.tables:
            rows = tab.extract()
            if not rows:
                continue
            header = [clean(str(c or "")) for c in rows[0]]
            if not any(RE_TABLE_HEADER.search(h) for h in header):
                continue
            for row in rows[1:]:
                cells_raw = [str(c or "") for c in row]
                cells_clean = [clean(c) for c in cells_raw]
                key = cells_clean[0] if cells_clean else ""
                val_raw = cells_raw[1] if len(cells_raw) > 1 else ""

                if RE_MAIN.search(key):
                    mode = "main"
                elif RE_RIDER.search(key):
                    mode = "rider"
                    if val_raw:
                        _add_riders(val_raw, components)
                elif mode == "rider" and val_raw:
                    _add_riders(val_raw, components)

    return components


def _parse_components_from_text(text: str, components: dict) -> None:
    """테이블 없을 때 텍스트에서 특약 목록 파싱."""
    in_rider = False
    for line in text.split("\n"):
        l = line.strip()
        if not l:
            continue
        if re.match(r"주\s*계\s*약", l):
            in_rider = False
        elif re.match(r"선택\s*특약|부가\s*특약", l):
            in_rider = True
        elif in_rider and l.startswith("+"):
            name = l.lstrip("+ ").strip()
            if name and name not in components["riders"]:
                components["riders"].append(name)


def _add_riders(text: str, components: dict) -> None:
    """선택특약 셀 텍스트에서 + 기호로 구분된 특약명 추출."""
    for part in re.split(r"\n\+\s*", text):
        for sub in re.split(r"\s{0,2}\+\s+(?=[가-힣A-Za-z])", part):
            name = clean(re.sub(r"^\+\s*", "", sub))
            if name and len(name) > 3 and name not in components["riders"]:
                components["riders"].append(name)


# ---------------------------------------------------------------------------
# 주석 추출
# ---------------------------------------------------------------------------

def _extract_notes(page_text: str) -> list[str]:
    """페이지 텍스트에서 '주)' 이후 번호 주석을 추출한다."""
    notes: list[str] = []
    in_notes = False
    current: list[str] = []
    RE_NOTE_SECTION = re.compile(r"^주\s*\)")
    RE_NUMBERED = re.compile(r"^\d{1,2}\.\s")

    for line in page_text.split("\n"):
        l = line.strip()
        if not l or RE_PAGE_NO.match(l):
            continue

        if RE_NOTE_SECTION.match(l):
            in_notes = True
            rest = re.sub(r"^주\s*\)\s*", "", l).strip()
            if rest:
                current = [rest]
            continue

        if not in_notes:
            continue

        if RE_NUMBERED.match(l):
            if current:
                notes.append(" ".join(current))
            current = [l]
        else:
            if current:
                current.append(l)

    if current:
        notes.append(" ".join(current))

    return [n for n in notes if len(n) > 5]


# ---------------------------------------------------------------------------
# 메인 파서
# ---------------------------------------------------------------------------

class HanwhaProductSummaryParser(BaseSummaryParser):
    """한화생명 상품요약서 전용 파서."""

    def parse_pdf(self, pdf_path: Path) -> dict:
        pdf_path = Path(pdf_path)
        doc = fitz.open(str(pdf_path))

        product_name = self._extract_product_name(doc)
        components = _parse_components(doc)
        contracts: list[dict] = []
        self._parse_all_contracts(doc, contracts, str(pdf_path))

        doc.close()
        return {
            "product_name": product_name,
            "management_no": "",
            "components": components,
            "contracts": contracts,
        }

    def _extract_product_name(self, doc: fitz.Document) -> str:
        for pidx in range(min(3, len(doc))):
            text = doc[pidx].get_text("text")
            m = re.search(r"한화생명[^\n]+(?:암보험|건강보험)[^\n]*무배당", text)
            if m:
                return clean(m.group(0))
        return doc.metadata.get("title", "").strip() or Path(doc.name).stem

    def _parse_all_contracts(
        self,
        doc: fitz.Document,
        contracts: list[dict],
        source_pdf: str,
    ) -> None:
        seen_names: set[str] = set()

        for pidx in range(len(doc)):
            page = doc[pidx]
            page_text = page.get_text("text")

            if RE_PAGE_NO.match(page_text.strip()):
                continue

            hdr = _extract_contract_header(page_text)
            if not hdr:
                continue

            key = hdr["full_name"]
            if key in seen_names:
                continue
            seen_names.add(key)

            benefits: list[dict] = []
            tabs = page.find_tables()
            if tabs and tabs.tables:
                for tab in tabs.tables:
                    rows = tab.extract()
                    if not rows:
                        continue
                    header_text = _strip_spacing(" ".join(str(c or "") for c in rows[0]))
                    if "급부" in header_text or "지급사유" in header_text:
                        benefits.extend(_parse_benefit_table(tab))

            if not benefits:
                benefits = self._fallback_parse_page(page_text)

            contracts.append({
                "name": hdr["name"],
                "code": hdr["code"],
                "type": hdr["type"],
                "full_name": hdr["full_name"],
                "source_pdf": source_pdf,
                "reference_amount": hdr["reference_amount"],
                "benefits": benefits,
                "notes": _extract_notes(page_text),
            })

            logger.debug("파싱: %s (급부 %d개)", hdr["name"], len(benefits))

    def _fallback_parse_page(self, page_text: str) -> list[dict]:
        """테이블 인식 실패 시 텍스트 기반 간이 파싱."""
        benefits = []
        RE_AMOUNT = re.compile(r"\d[\d,]*\s*만?\s*원|\d+회당|\d+%")
        trigger_buf: list[str] = []

        for line in (l.strip() for l in page_text.split("\n") if l.strip()):
            if RE_PAGE_NO.match(line):
                continue
            if RE_AMOUNT.search(line) and trigger_buf:
                benefits.append({
                    "benefit_names": [trigger_buf[0]],
                    "trigger": " ".join(trigger_buf[1:]),
                    "amounts": [{"condition": "", "amount": line, "reduction_note": ""}],
                })
                trigger_buf = []
            elif len(line) > 3:
                trigger_buf.append(line)

        return benefits


# ---------------------------------------------------------------------------
# 레지스트리 자동 등록
# ---------------------------------------------------------------------------

def _register() -> None:
    try:
        register_summary_parser("한화생명", HanwhaProductSummaryParser)
        register_summary_parser("hanwhalife", HanwhaProductSummaryParser)
    except Exception:
        pass


_register()
