"""
한화생명 상품요약서 전용 파서 - find_tables() 기반.

구조:
  한화 상품요약서 PDF는 라이나와 유사하게 디지털 실선 테이블로 구성됨.

  1. 상품 구성 섹션 (가. 상품의 구성):
     - "구 분 / 내 용" 2열 테이블
     - 주계약 이름 + 선택특약 목록 (+ 기호로 시작)

  2. 급부 테이블 섹션 (특약별 1페이지):
     - 페이지 상단: "■ 특약명(코드) 무배당" 헤더
     - "(기준 : 특약가입금액 N만원)" 기준금액
     - "급부명칭 / 지급사유 / 지급금액(경과기간)" 테이블
       - 경과기간 있는 경우: 4열 (급부명칭 / 지급사유 / 경과기간 / 금액)
       - 경과기간 없는 경우: 3열 (급부명칭 / 지급사유 / 지급금액)
       - 지급금액 조건별 여러 행인 경우: 조건 / 금액 2열

출력 JSON 구조:
  {
    "product_name": "한화생명 시그니처H암보험 무배당",
    "management_no": "",
    "components": {
      "riders": [...]
    },
    "contracts": [
      {
        "name": "특약명",
        "code": "KA4.1",
        "type": "rider",
        "source_pdf": "...",
        "reference_amount": "1,000만원",
        "benefits": [
          {
            "benefit_names": ["급부명칭"],
            "trigger": "지급사유",
            "amounts": [
              {"condition": "경과기간 또는 조건", "amount": "금액", "reduction_note": ""}
            ]
          }
        ],
        "notes": []
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


# ---------------------------------------------------------------------------
# 정규식
# ---------------------------------------------------------------------------

# ■ 특약명(코드) 무배당  또는  ■ 특약명(코드) 무배당[...형]
RE_CONTRACT_HEADER = re.compile(
    r"■\s*(.+?)\(([A-Za-z0-9]+\.\d+(?:\.\d+)?)\)\s*무배당"
)
# 주계약: "■ 한화생명 ... 무배당" (코드 없음) 또는 "■ 주계약"
RE_MAIN_CONTRACT_HEADER = re.compile(
    r"■\s*(한화생명[^\n(]+무배당|주계약)"
)
# (기준 : 특약가입금액 N만원) 또는 (기준: 보험가입금액 N만원)
RE_REFERENCE_AMOUNT = re.compile(
    r"기준\s*[:：]\s*(?:특약|보험)?가입금액\s*([\d,]+만?원)"
)
# 페이지 번호 (숫자 / 전체)
RE_PAGE_NO = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
# (숫자) 또는 번호. 형태의 numbered note
RE_NOTE_NUM = re.compile(r"^\s*\(?\d{1,2}\)?\s*\d?\s*\.")


# ---------------------------------------------------------------------------
# 텍스트 정규화 (범용 — 라이나 파서와 동일 역할)
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    return re.sub(r"[ \t\n]+", " ", text or "").strip()


def _normalize_amount(text: str) -> str:
    """금액 텍스트 정규화: 줄바꿈/공백 정리, 불필요 prefix 제거."""
    if not text:
        return ""
    text = re.sub(r"[ \t\n]+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# 컬럼 구조 분석
# ---------------------------------------------------------------------------

def _strip_spacing(text: str) -> str:
    """PDF 자간 벌림(letter-spacing) 제거: '급 부 명 칭' → '급부명칭'."""
    return re.sub(r"(?<=[\uAC00-\uD7A3])\s+(?=[\uAC00-\uD7A3])", "", text)


def _identify_columns(header: list[str]) -> dict:
    """헤더 행에서 컬럼 역할을 파악한다.

    반환: {"benefit": int, "trigger": int, "amount": int, "condition": int | None}
    """
    result = {"benefit": 0, "trigger": 1, "amount": 2, "condition": None}
    for i, h in enumerate(header):
        hc = _strip_spacing(_clean(h))
        if "급부" in hc:
            result["benefit"] = i
        elif "지급사유" in hc or "사유" in hc:
            result["trigger"] = i
        elif "지급금액" in hc or "금액" in hc:
            result["amount"] = i
        elif "경과기간" in hc or "조건" in hc:
            result["condition"] = i
    return result


# ---------------------------------------------------------------------------
# 급부 테이블 파싱
# ---------------------------------------------------------------------------

def _parse_benefit_table(tab, contract_name: str) -> list[dict]:
    """find_tables() 결과 1개 테이블 → benefit 리스트.

    한화 소액질병 테이블 구조:
      benefit_name | trigger_1(질병A) | 1년미만 | 100만원
                   | trigger_2(질병B) | 1년미만 | 100만원
                   |                 | 1년이상 | 200만원
                   ...

    benefit_name 셀이 비어있는 연속 행은 동일 benefit에 속하지만,
    trigger(지급사유) 셀이 새로운 질병명으로 바뀌면 → 별도 질병군 항목으로 분리한다.

    출력: 1 benefit_name에 여러 질병군이 있으면 질병군 수만큼 별도 dict 반환.
    각 dict의 amounts는 해당 질병군의 경과기간별 금액만 포함한다.
    """
    rows = tab.extract()
    if not rows:
        return []

    # 헤더 행 결정 (급부명칭/지급사유 포함 행 — 자간 벌림 대응)
    header_idx = 0
    for idx, row in enumerate(rows[:5]):
        joined = _strip_spacing(" ".join(str(c or "") for c in row))
        if "급부" in joined or "지급사유" in joined:
            header_idx = idx
            break

    header = [str(c or "") for c in rows[header_idx]]
    cols = _identify_columns(header)
    n_cols = len(header)

    # 경과기간 컬럼 처리 (헤더가 2행으로 나뉜 경우 포함)
    has_reduction = cols["condition"] is not None
    if not has_reduction:
        if header_idx + 1 < len(rows):
            next_row = [str(c or "") for c in rows[header_idx + 1]]
            if any("경과기간" in _strip_spacing(c) for c in next_row):
                has_reduction = True
                for idx2, c in enumerate(next_row):
                    if "경과기간" in c:
                        cols["condition"] = idx2
                        cols["amount"] = min(idx2 + 1, n_cols - 1)
                        break
                header_idx += 1

    benefits: list[dict] = []
    current_benefit: dict | None = None
    current_trigger: str = ""          # 현재 질병군 trigger

    for row in rows[header_idx + 1:]:
        cells = [str(c or "").strip() for c in row]
        if not any(cells):
            continue

        benefit_cell = cells[cols["benefit"]] if cols["benefit"] < len(cells) else ""
        trigger_cell = cells[cols["trigger"]] if cols["trigger"] < len(cells) else ""
        amount_col = cols["amount"]
        cond_col = cols["condition"]

        amount_cell = cells[amount_col] if amount_col < len(cells) else ""
        cond_cell = cells[cond_col] if cond_col is not None and cond_col < len(cells) else ""

        benefit_name = _normalize_benefit_name(benefit_cell)
        trigger = _clean(trigger_cell)
        amount = _normalize_amount(amount_cell)
        condition = _normalize_amount(cond_cell)

        if benefit_name:
            # 새 benefit 시작 — trigger도 함께 시작
            current_trigger = trigger
            current_benefit = {
                "benefit_names": [benefit_name],
                "trigger": trigger,
                "amounts": [],
            }
            benefits.append(current_benefit)

        elif trigger and current_benefit is not None:
            # benefit_name 없이 trigger가 새로 등장 = 동일 benefit_name의 새 질병군
            # → 별도 benefit dict로 분리 (질병군 단위 1행 원칙)
            current_trigger = trigger
            current_benefit = {
                "benefit_names": list(benefits[-1]["benefit_names"]) if benefits else [""],
                "trigger": trigger,
                "amounts": [],
            }
            benefits.append(current_benefit)

        if current_benefit is None:
            continue

        if not amount and not condition:
            # 트리거 텍스트 연속 행 (셀 내 줄바꿈이 행으로 나뉜 경우)
            if not benefit_name:
                # trigger 이어붙임 (같은 질병군 내)
                current_benefit["trigger"] = (
                    current_benefit["trigger"] + " " + trigger
                ).strip()
            continue

        # 금액 파싱
        if has_reduction and condition:
            amt_entry = {"condition": condition, "amount": amount, "reduction_note": ""}
        else:
            amt_entry = _split_condition_amount(amount)

        current_benefit["amounts"].append(amt_entry)

    return [b for b in benefits if _is_valid_benefit(b)]


_RE_GARBAGE_BENEFIT = re.compile(
    r"^■|^주\s*\)|급\s*부\s*명\s*칭|급부명칭"
    r"|^지\s*급\s*사\s*유$|^지\s*급\s*금\s*액$"
)


def _is_valid_benefit(benefit: dict) -> bool:
    """파싱된 benefit가 실제 급부 데이터인지 검증."""
    names = benefit.get("benefit_names", [])
    if not names or not names[0]:
        return False
    name = names[0].strip()
    if _RE_GARBAGE_BENEFIT.search(name):
        return False
    if name.startswith("보험기간 중 피보험자") and not benefit.get("amounts"):
        return False
    return True


def _split_condition_amount(text: str) -> dict:
    """'관혈수술\n1회당 500만원' 형태를 condition/amount로 분리."""
    if not text:
        return {"condition": "", "amount": "", "reduction_note": ""}

    # 줄바꿈으로 분리
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if len(parts) >= 2:
        # 마지막 part가 금액처럼 보이면
        if re.search(r"\d+[만백천]?\s*원|\d+회당|%", parts[-1]):
            return {
                "condition": " ".join(parts[:-1]),
                "amount": parts[-1],
                "reduction_note": "",
            }
    return {"condition": "", "amount": text, "reduction_note": ""}


# ---------------------------------------------------------------------------
# 페이지 상단 헤더에서 계약 정보 추출
# ---------------------------------------------------------------------------

def _extract_contract_header(page_text: str) -> dict | None:
    """페이지 텍스트에서 ■ 특약명(코드) 무배당 + 기준금액 추출."""
    # 주계약
    m_main = RE_MAIN_CONTRACT_HEADER.search(page_text)
    m_rider = RE_CONTRACT_HEADER.search(page_text)
    m_ref = RE_REFERENCE_AMOUNT.search(page_text)

    ref_amount = m_ref.group(1) if m_ref else ""

    if m_rider:
        full_name = _clean(m_rider.group(0).lstrip("■").strip())
        return {
            "name": _clean(m_rider.group(1)),
            "code": m_rider.group(2),
            "full_name": full_name,
            "reference_amount": ref_amount,
            "type": "rider",
        }

    if m_main and "■" in page_text:
        return None

    return None


# ---------------------------------------------------------------------------
# 상품 구성 섹션 파싱
# ---------------------------------------------------------------------------

def _parse_components(doc: fitz.Document) -> dict:
    """PDF에서 '가. 상품의 구성' 테이블을 찾아 주계약·선택특약 목록을 반환.

    테이블이 여러 페이지에 걸쳐 있을 수 있으므로
    '구 분 / 내 용' 헤더가 보이는 모든 페이지를 순회한다.
    """
    components: dict = {
        "riders": [],
    }
    RE_COMP_SECTION = re.compile(r"상품의\s*구성|보험금\s*지급사유\s*및\s*지급제한")
    RE_MAIN = re.compile(r"^주\s*계\s*약$|^주계약$")
    RE_RIDER = re.compile(r"선택\s*특약|부가\s*특약")
    RE_COMP_TABLE_HEADER = re.compile(r"구\s*분|내\s*용")
    RE_BENEFIT_TABLE = re.compile(r"급부명칭|지급사유|급부명")

    found_section = False
    mode = ""

    for pidx in range(min(25, len(doc))):
        page = doc[pidx]
        text = page.get_text("text")

        # 섹션 시작 확인
        if RE_COMP_SECTION.search(text):
            found_section = True

        # 급부 테이블이 나오면 상품 구성 섹션 종료
        if found_section and RE_BENEFIT_TABLE.search(text):
            if pidx > 6:  # 상품 구성은 보통 앞쪽에만 있음
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
            header = [_clean(str(c or "")) for c in rows[0]]
            # "구 분 / 내 용" 헤더 테이블만 처리
            if not any(RE_COMP_TABLE_HEADER.search(h) for h in header):
                continue

            for row in rows[1:]:
                # key는 _clean, val은 원본 유지 (줄바꿈 기준 분리 필요)
                cells_raw = [str(c or "") for c in row]
                cells_clean = [_clean(c) for c in cells_raw]
                if not cells_raw:
                    continue
                key = cells_clean[0] if cells_clean else ""
                val_raw = cells_raw[1] if len(cells_raw) > 1 else ""

                if RE_MAIN.search(key):
                    mode = "main"
                elif RE_RIDER.search(key):
                    mode = "rider"
                    if val_raw:
                        _add_riders(val_raw, components)
                elif key in ("", "선택특약") or (mode == "rider" and val_raw):
                    if mode == "rider" and val_raw:
                        _add_riders(val_raw, components)

    return components


def _parse_components_from_text(text: str, components: dict) -> None:
    """테이블 없을 때 텍스트에서 상품 구성 파싱."""
    lines = text.split("\n")
    in_rider = False
    for line in lines:
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
    """선택특약 텍스트에서 + 기호로 구분된 특약명 추출.

    find_tables()는 셀 내 줄바꿈을 '\\n' 으로 반환하므로
    '\\n+ 특약명' 패턴을 기본 구분자로 먼저 처리한다.
    잘린 이름(셀 너비 초과)은 다음 행에서 이어붙는다.
    """
    # 1단계: 줄바꿈+플러스 기준 분리 (원본 text, _clean 전)
    parts = re.split(r"\n\+\s*", text)

    for part in parts:
        # 각 part 내에서 추가로 "공백+플러스+한글" 패턴 분리 (이중 합쳐진 경우)
        sub_parts = re.split(r"\s{0,2}\+\s+(?=[가-힣A-Za-z])", part)
        for sub in sub_parts:
            name = re.sub(r"^\+\s*", "", sub).strip()
            name = _clean(name)
            if name and len(name) > 3 and name not in components["riders"]:
                components["riders"].append(name)


# ---------------------------------------------------------------------------
# 메인 파서
# ---------------------------------------------------------------------------

class HanwhaProductSummaryParser:
    """한화생명 상품요약서 전용 파서.

    ProductBundleParser 인터페이스 준수:
      parse_pdf(pdf_path: Path) -> dict
    """

    def parse_pdf(self, pdf_path: Path) -> dict:
        """상품요약서 PDF → dict."""
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
        """PDF 첫 페이지에서 상품명 추출."""
        for pidx in range(min(3, len(doc))):
            text = doc[pidx].get_text("text")
            # "한화생명 ... 무배당" 패턴
            m = re.search(r"한화생명[^\n]+(?:암보험|건강보험)[^\n]*무배당", text)
            if m:
                return _clean(m.group(0))
        return doc.metadata.get("title", "").strip() or Path(doc.name).stem

    def _parse_all_contracts(
        self,
        doc: fitz.Document,
        contracts: list[dict],
        source_pdf: str,
    ) -> None:
        """전 페이지를 순회하며 계약 섹션을 파싱한다."""
        # 계약 헤더가 있는 페이지 → 해당 페이지의 테이블 파싱
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

            tabs = page.find_tables()
            benefits: list[dict] = []

            if tabs and tabs.tables:
                for tab in tabs.tables:
                    header_row = tab.extract()[0] if tab.extract() else []
                    header_text = _strip_spacing(
                        " ".join(str(c or "") for c in header_row)
                    )
                    if "급부" in header_text or "지급사유" in header_text:
                        parsed = _parse_benefit_table(tab, hdr["name"])
                        benefits.extend(parsed)

            if not benefits:
                # fallback: 블록 텍스트 기반 간이 파싱
                benefits = self._fallback_parse_page(page_text, hdr)

            # 주석 추출 (※, 주) 등으로 시작하는 텍스트)
            notes = self._extract_notes(page_text)

            contracts.append({
                "name": hdr["name"],
                "code": hdr["code"],
                "type": hdr["type"],
                "full_name": hdr["full_name"],
                "source_pdf": source_pdf,
                "reference_amount": hdr["reference_amount"],
                "benefits": benefits,
                "notes": notes,
            })

            logger.debug(
                "파싱: %s (급부 %d개, 주석 %d개)",
                hdr["name"], len(benefits), len(notes)
            )

    def _fallback_parse_page(self, page_text: str, hdr: dict) -> list[dict]:
        """테이블 인식 실패 시 텍스트 기반 간이 파싱."""
        lines = [l.strip() for l in page_text.split("\n") if l.strip()]
        # 급부명 줄과 금액 줄을 휴리스틱하게 찾음
        benefits = []
        RE_AMOUNT = re.compile(r"\d[\d,]*\s*만?\s*원|\d+회당|\d+%")
        trigger_buf: list[str] = []

        for line in lines:
            if RE_PAGE_NO.match(line):
                continue
            if RE_AMOUNT.search(line) and trigger_buf:
                benefits.append({
                    "benefit_names": [trigger_buf[0]] if trigger_buf else [""],
                    "trigger": " ".join(trigger_buf[1:]),
                    "amounts": [{"condition": "", "amount": line, "reduction_note": ""}],
                })
                trigger_buf = []
            elif len(line) > 3:
                trigger_buf.append(line)

        return benefits

    def _extract_notes(self, page_text: str) -> list[str]:
        """페이지 텍스트에서 '주)' 이후 번호 주석만 추출.

        테이블 내용이 notes에 섞이지 않도록
        '주)' 헤더가 나온 뒤부터만 수집한다.
        """
        notes: list[str] = []
        lines = page_text.split("\n")

        in_notes = False
        current: list[str] = []
        RE_NOTE_SECTION = re.compile(r"^주\s*\)")
        RE_NUMBERED = re.compile(r"^\d{1,2}\.\s")

        for line in lines:
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
# 번들 파서 자동 등록
# ---------------------------------------------------------------------------

def _register_hanwha_parsers() -> None:
    try:
        from .product_bundle_parser import register_summary_parser
        register_summary_parser("한화생명", HanwhaProductSummaryParser)
        register_summary_parser("hanwhalife", HanwhaProductSummaryParser)
    except ImportError:
        pass


_register_hanwha_parsers()
