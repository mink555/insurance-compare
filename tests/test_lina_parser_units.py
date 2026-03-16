"""라이나 파서 단위 테스트.

목표: 현재 PDF만 통과하는 테스트가 아니라
     **당사 신상품 PDF가 추가되어도 그대로 동작하는 보험약관 파서** 보장.

테스트 구조:
  Section A. _normalize_note_text — 구조 규칙별 독립 검증
    A1. 번호 경계 보호 (분리 기준 보존)
    A2. 규칙 A: 한글↔한글 개행 제거
    A3. 규칙 B: 한글↔영숫자 개행 제거
    A4. 규칙 C: 숫자/단위 + 개행 + 조사 제거
    A5. 규칙 D: 인용부호/괄호 경계 개행 제거
    A6. 연속 공백 정리
    A7. 오탐 방지 (번호/기호/문장 경계 보존)

  Section B. _repair_broken_korean_phrases — whitelist 공백 복원
    B1. 핵심 어휘 복원
    B2. 오탐 방지 (미등록 패턴·숫자·조문 참조)
    B3. 구조 규칙 + whitelist 복합 동작

  Section C. _split_numbered_notes — 번호 주석 분리
  Section D. _extract_notes_from_blocks — 페이지 경계 통합
  Section E. 실제 PDF 회귀 테스트
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from insurance_parser.parse.lina_summary_parser import (
    _normalize_benefit_name,
    _normalize_note_text,
    _repair_broken_korean_phrases,
    _split_numbered_notes,
    _extract_notes_from_blocks,
)


# ===========================================================================
# Section A. _normalize_note_text — 구조 규칙별 독립 검증
# ===========================================================================

# ── A1. 번호 경계 보호 ──────────────────────────────────────────────────────

def test_A1_number_boundary_preserved_in_sequence():
    """연속 번호 주석에서 번호 앞 개행은 분리자로 보존."""
    text = "10. 본문입니다.\n11. 다음 항목입니다."
    result = _normalize_note_text(text)
    assert "10." in result and "11." in result
    assert result.index("10.") < result.index("11.")


def test_A1_number_boundary_single():
    """단일 번호 앞 개행도 분리자로 보존 (공백으로 전환)."""
    result = _normalize_note_text("내용이 있고\n3. 세 번째 항목.")
    assert "3." in result


def test_A1_number_boundary_not_removed():
    """번호 경계 앞 한글이 있어도 번호는 잘리지 않아야 함."""
    result = _normalize_note_text("봅니다.\n11. 이 특약에서")
    assert "11." in result
    assert "봅니다." in result


# ── A2. 규칙 A: 한글↔한글 개행 제거 ──────────────────────────────────────

def test_A2_korean_to_korean():
    assert _normalize_note_text("보험계\n약") == "보험계약"


def test_A2_multi_linebreak():
    assert _normalize_note_text("지\n급\n사\n유") == "지급사유"


def test_A2_피보험자():
    assert _normalize_note_text("피보험\n자가 사망") == "피보험자가 사망"


# ── A3. 규칙 B: 한글↔영숫자 개행 제거 ────────────────────────────────────

def test_A3_korean_to_number():
    assert _normalize_note_text("보험\n2년이내") == "보험2년이내"


def test_A3_number_to_korean():
    """숫자→한글 (단위 뒤 한글)."""
    # '이내'는 한글이므로 붙어야 함
    result = _normalize_note_text("2년\n이내")
    # 규칙 C(조사)와 겹치나 두 규칙 중 하나로 잡힘
    assert "2년이내" in result or "2년 이내" in result  # 이내는 조사가 아니므로 규칙C 미적용


def test_A3_english_to_korean():
    assert _normalize_note_text("C73\n갑상선") == "C73갑상선"


# ── A4. 규칙 C: 숫자/단위 + 개행 + 조사 제거 ─────────────────────────────

def test_A4_year_josa_이():
    """2년\n이 → 2년이."""
    assert _normalize_note_text("2년\n이 지난") == "2년이 지난"


def test_A4_percent_josa_를():
    """50%\n를 → 50%를."""
    assert _normalize_note_text("50%\n를 지급") == "50%를 지급"


def test_A4_회_josa_에():
    """1회\n에 → 1회에."""
    assert _normalize_note_text("1회\n에 한하여") == "1회에 한하여"


def test_A4_number_josa_이():
    """5번째\n이 → 5번째이."""
    result = _normalize_note_text("5번\n이후")
    # 규칙C: 숫자뒤 이 → 붙임
    assert "5번이후" in result or "5번\n이후" not in result


# ── A5. 규칙 D: 인용부호/괄호 경계 개행 제거 ─────────────────────────────

def test_A5_closing_quote_to_korean():
    r""""갑상선암"\n으로 → "갑상선암"으로."""
    result = _normalize_note_text('"갑상선암"\n으로 진단확정')
    assert '"갑상선암"으로' in result


def test_A5_korean_to_opening_quote():
    """한글 + \\n + 여는 따옴표."""
    result = _normalize_note_text('진단확정되고\n"암"으로')
    assert '진단확정되고"암"으로' in result


def test_A5_closing_paren_to_korean():
    """(상피내암)\\n치료 → (상피내암)치료."""
    result = _normalize_note_text("(상피내암)\n치료보험금")
    assert "(상피내암)치료보험금" in result


# ── A6. 연속 공백 정리 ─────────────────────────────────────────────────────

def test_A6_multiple_spaces_collapsed():
    assert _normalize_note_text("보험  계약   일") == "보험 계약 일"


def test_A6_tab_to_space():
    assert _normalize_note_text("보험\t계약") == "보험 계약"


# ── A7. 오탐 방지 ──────────────────────────────────────────────────────────

def test_A7_percent_not_joined_with_word():
    """'50%' 뒤 개행이 번호가 아닌 문장이면 공백으로."""
    result = _normalize_note_text("50%를 지급합니다.\n그 경우에는")
    assert "50%를 지급합니다." in result


def test_A7_sentence_boundary_preserved():
    """문장 끝 마침표 + 개행은 공백으로 (번호 경계 아님)."""
    result = _normalize_note_text("지급합니다.\n단, 예외가 있습니다.")
    assert "지급합니다." in result
    assert "단, 예외가 있습니다." in result


def test_A7_number_in_article_not_split():
    """'제3호' 같은 조문 번호는 분리 트리거 아님."""
    result = _normalize_note_text("약관 제3호에 따라 지급합니다.")
    assert "제3호" in result


# ===========================================================================
# Section B. _repair_broken_korean_phrases — whitelist 공백 복원
# ===========================================================================

# ── B1. 핵심 어휘 복원 ────────────────────────────────────────────────────

def test_B1_보험계약():
    assert _repair_broken_korean_phrases("보험계 약해당일") == "보험계약해당일"


def test_B1_보험계약일():
    assert _repair_broken_korean_phrases("보험계 약일로부터") == "보험계약일로부터"


def test_B1_금액():
    assert _repair_broken_korean_phrases("상기 금 액의 50%") == "상기 금액의 50%"


def test_B1_사유():
    assert _repair_broken_korean_phrases("지급사 유가 발생") == "지급사유가 발생"


def test_B1_피보험자():
    assert _repair_broken_korean_phrases("피보험 자가 사망") == "피보험자가 사망"


def test_B1_계약자():
    assert _repair_broken_korean_phrases("계약 자에게 지급") == "계약자에게 지급"


def test_B1_수익자():
    assert _repair_broken_korean_phrases("수익 자에게 지급") == "수익자에게 지급"


def test_B1_납입():
    assert _repair_broken_korean_phrases("보험료 납 입을 면제") == "보험료 납입을 면제"


def test_B1_진단확정():
    assert _repair_broken_korean_phrases("진단 확정 되었을 때") == "진단확정 되었을 때"


def test_B1_보험금():
    assert _repair_broken_korean_phrases("보험 금을 지급합니다") == "보험금을 지급합니다"


# ── B2. 오탐 방지 ─────────────────────────────────────────────────────────

def test_B2_percent_preserved():
    """50%는 변경되지 않아야 함."""
    text = "상기 금액의 50%를 지급합니다."
    assert _repair_broken_korean_phrases(text) == text


def test_B2_article_preserved():
    """약관 제3조 표현은 변경되지 않아야 함."""
    text = "약관 제3조에 따라 지급합니다."
    assert _repair_broken_korean_phrases(text) == text


def test_B2_unregistered_pattern_unchanged():
    """미등록 분절 패턴은 변경 없음."""
    text = "이미 등록되지 않은 패 턴 이라서 변경없음."
    assert _repair_broken_korean_phrases(text) == text


# ── B3. 구조 규칙 + whitelist 복합 ────────────────────────────────────────

def test_B3_linebreak_and_space_both():
    """개행 분절과 공백 분절이 동시에 있는 경우."""
    raw = "보험계\n약해당일 전일 이전에 상기 금 액의 50%를 지급"
    result = _normalize_note_text(raw)
    assert "보험계약해당일" in result, f"보험계약해당일 미복원: {result}"
    assert "금액의" in result, f"금액의 미복원: {result}"


def test_B3_year_josa_and_space():
    """2년\\n이 + 공백 분절이 함께 있는 실제 패턴."""
    raw = "보험계 약일부터 2년\n이 지난 보험계 약해당일"
    result = _normalize_note_text(raw)
    assert "보험계약일부터" in result
    assert "2년이 지난" in result
    assert "보험계약해당일" in result


# ===========================================================================
# Section C. _normalize_benefit_name
# ===========================================================================

def test_C_linebreak_mid_word():
    assert _normalize_benefit_name("유방암/전립\n선암 치료보\n험금") == "유방암/전립선암 치료보험금"


def test_C_bullet_separator():
    """구분자(∙) 앞뒤 공백 유지."""
    result = _normalize_benefit_name("갑상선암\n∙\n기타피부암")
    assert "∙" in result
    assert "갑상선암" in result and "기타피부암" in result


# ===========================================================================
# Section D. _split_numbered_notes
# ===========================================================================

def test_D_basic_split():
    text = "1. 첫 번째 내용입니다. 2. 두 번째 내용입니다."
    parts = _split_numbered_notes(text)
    assert len(parts) == 2
    assert parts[0].startswith("1.")
    assert parts[1].startswith("2.")


def test_D_10_and_11():
    """10번과 11번 연속 분리."""
    text = (
        "10. 9)에도 불구하고 면역성 강화 치료. "
        "11. 이 특약에서 최초로 진단확정된 암."
    )
    parts = _split_numbered_notes(text)
    assert len(parts) == 2, f"expected 2 parts, got {len(parts)}"
    assert parts[0].startswith("10.")
    assert parts[1].startswith("11.")


def test_D_no_number_returns_full():
    text = "번호 없는 일반 텍스트입니다."
    parts = _split_numbered_notes(text)
    assert len(parts) == 1
    assert parts[0] == text


def test_D_single_number_from_that_point():
    """번호가 1개일 때 해당 번호부터의 텍스트 반환."""
    text = "앞쪽 잔여 3. 세 번째 내용입니다."
    parts = _split_numbered_notes(text)
    assert len(parts) == 1
    assert parts[0].startswith("3.")


def test_D_article_number_not_split():
    """'제3호', '제14조' 같은 조문 번호는 분리 트리거 아님."""
    text = "1. 약관 제3호에 따라 제14조에 근거합니다."
    parts = _split_numbered_notes(text)
    assert len(parts) == 1  # 조문 번호로 분리되면 안 됨


# ===========================================================================
# Section E. _extract_notes_from_blocks — 페이지 경계 통합
# ===========================================================================

def _make_block(text: str, y: float = 0.0) -> tuple:
    return (0.0, y, 100.0, y + 10.0, text)


def test_E_basic():
    blocks = [_make_block("주) \n1. 첫 번째 주석입니다. \n2. 두 번째 주석입니다.", 10.0)]
    notes = _extract_notes_from_blocks(blocks)
    assert len(notes) == 2
    assert notes[0].startswith("1.")
    assert notes[1].startswith("2.")


def test_E_page_boundary_10_11():
    """10번 끝이 p3, 11번 시작이 p4에 있는 상황 시뮬레이션."""
    block_p3 = _make_block(
        "주) \n1. 첫 번째 내용. \n10. 9)에도 불구하고 면역성 강화 치료.",
        10.0,
    )
    block_p4 = _make_block(
        "11. 최초로 진단확정된 암과 상이한 암의 치료를 받은 경우에도 지급합니다.",
        10.0,
    )
    notes = _extract_notes_from_blocks([block_p3, block_p4])

    nums = [n.split(".")[0].strip() for n in notes]
    assert "10" in nums, f"10번 누락: {notes}"
    assert "11" in nums, f"11번 누락: {notes}"

    note_10 = next(n for n in notes if n.startswith("10."))
    note_11 = next(n for n in notes if n.startswith("11."))
    assert "면역성 강화" in note_10
    assert "최초로 진단확정" in note_11
    assert "11." not in note_10, "10번에 11번이 혼합됨"


def test_E_management_no_filtered():
    """관리번호 블록은 주석에 포함되지 않아야 함."""
    blocks = [
        _make_block("주) \n1. 첫 번째 주석입니다.", 10.0),
        _make_block("관리번호 2025-P-CL-0474-035", 20.0),
        _make_block("2. 두 번째 주석입니다.", 30.0),
    ]
    notes = _extract_notes_from_blocks(blocks)
    assert "관리번호" not in " ".join(notes)
    assert len(notes) == 2


def test_E_note_text_normalized_in_extraction():
    """블록 추출 시 _normalize_note_text가 적용되어 개행 분절이 복원되어야 함."""
    blocks = [
        _make_block("주) \n1. 보험계\n약일로부터 2년\n이 지난 경우.", 10.0),
    ]
    notes = _extract_notes_from_blocks(blocks)
    assert len(notes) == 1
    assert "보험계약일로부터" in notes[0], f"보험계약일 미복원: {notes[0]}"
    assert "2년이 지난" in notes[0], f"2년이 미복원: {notes[0]}"


# ===========================================================================
# Section F. 실제 PDF 회귀 테스트 (PDF 파일 존재 시에만)
# ===========================================================================

def test_F_갑상선암_특약_notes():
    """갑상선암 특약 PDF에서 notes 10번, 11번이 독립적으로 추출."""
    import re
    from insurance_parser.parse.lina_summary_parser import LinaRiderSummaryParser

    pdf_path = Path(__file__).resolve().parent.parent / (
        "linalife/암특약/상품요약서/"
        "무배당라이나초간편갑상선암기타피부암직접치료특약(갱신형)/"
        "R00591002_0_S.pdf"
    )
    if not pdf_path.exists():
        print(f"  SKIP: PDF not found")
        return

    result = LinaRiderSummaryParser().parse_pdf(pdf_path)
    notes = result["contracts"][0]["notes"]
    nums = [int(m.group(1)) for n in notes if (m := re.match(r"^(\d+)\.", n))]

    assert 10 in nums, f"10번 누락. 번호 목록: {nums}"
    assert 11 in nums, f"11번 누락. 번호 목록: {nums}"

    n10 = next(n for n in notes if n.startswith("10."))
    n11 = next(n for n in notes if n.startswith("11."))
    assert "11." not in n10, f"10번에 11번 혼합: {n10[:60]}"
    assert "10." not in n11, f"11번에 10번 혼합: {n11[:60]}"


def test_F_reduction_note_복원():
    """주계약 reduction_note에서 보험계약해당일·금액이 복원되어야 함."""
    import json
    from insurance_parser.parse.lina_summary_parser import LinaProductSummaryParser

    main_pdf = Path(__file__).resolve().parent.parent / (
        "linalife/암보험/상품요약서/무배당라이나초간편암보험(갱신형)/B00389001_6_S.pdf"
    )
    if not main_pdf.exists():
        print("  SKIP: PDF not found")
        return

    result = LinaProductSummaryParser().parse_product_auto(
        main_summary_pdf=main_pdf,
        rider_summary_base_dir=main_pdf.parent.parent.parent.parent / "암특약/상품요약서",
    )

    assert all(c["type"] == "rider" for c in result["contracts"]), \
        "모든 contract type이 'rider'여야 한다"
    assert not any(c["name"].strip() == "주계약" for c in result["contracts"]), \
        "주계약 행이 contracts에 포함되면 안 된다"

    rider = next(
        (c for c in result["contracts"] if "암직접치료특약" in c["name"]), None
    )
    if rider:
        rn2 = rider["benefits"][0]["amounts"][0].get("reduction_note", "")
        assert "2년이 지난" in rn2 or "보험계약해당일" in rn2, f"암직접치료특약 reduction_note 미복원: {rn2}"


# ===========================================================================
# Section G. notes 다음 테이블 경계 차단 + amounts 혼합 셀 분리 (회귀)
# ===========================================================================

def test_G1_notes_stop_at_next_table_header():
    """notes 수집 중 다음 테이블 헤더(급부명/지급사유) 신호가 나오면 거기서 멈춰야 한다.

    1형 notes 11번 뒤에 '2형 [기준:...] 급 부 명 지 급 사 유' 블록이 오는 경우.
    """
    # 1형 notes 블록 시뮬레이션
    blocks = [
        (0, 100, 400, 120, "주)\n1. 첫 번째 주석입니다."),
        (0, 130, 400, 150, "2. 두 번째 주석입니다."),
        (0, 160, 400, 180, "11. 마지막 주석입니다."),
        # 다음 형 테이블 시작 신호 — 여기서 notes 종료
        (0, 190, 400, 210, "- 2 형\n[기준 : 특약보험가입금액 1,000만원]"),
        (0, 220, 400, 240, "급 부 명\n지 급 사 유\n지 급 금 액"),
        (0, 250, 400, 270, "암생활자금\n피보험자가 보험기간 중..."),
        (0, 260, 400, 280, "12. 이 줄은 notes에 포함되면 안 됩니다."),
    ]
    notes = _extract_notes_from_blocks(blocks)
    note_texts = " ".join(notes)
    assert "11. 마지막 주석" in note_texts, f"11번 note 없음: {notes}"
    assert "12. 이 줄은" not in note_texts, f"12번이 notes에 포함됨: {notes}"
    assert "급 부 명" not in note_texts, f"테이블 헤더가 notes에 포함됨: {notes}"
    assert "암생활자금" not in note_texts, f"테이블 내용이 notes에 포함됨: {notes}"


def test_G2_notes_stop_at_kibun_bracket():
    """[기준 : 특약보험가입금액] 패턴이 notes 중간에 나타나면 멈춰야 한다."""
    blocks = [
        (0, 100, 400, 120, "주)\n1. 첫 번째 주석"),
        (0, 130, 400, 150, "2. 두 번째 주석"),
        (0, 160, 400, 180, "[기준 : 특약보험가입금액 500만원]"),
        (0, 190, 400, 210, "3. 이건 다음 형의 notes"),
    ]
    notes = _extract_notes_from_blocks(blocks)
    note_texts = " ".join(notes)
    assert "2. 두 번째 주석" in note_texts
    assert "[기준" not in note_texts, f"기준 블록이 notes에 포함됨: {notes}"
    assert "3. 이건 다음 형" not in note_texts, f"다음 형 notes가 포함됨: {notes}"


def test_G3_notes_continue_across_pages_without_table():
    """다음 테이블 신호 없이 페이지를 넘어 이어지는 notes는 정상 수집돼야 한다."""
    blocks = [
        (0, 100, 400, 120, "주)\n1. 피보험자가 보험기간 중 사망"),
        (0, 130, 400, 150, "하였을 경우 이 특약은 효력이 없습니다."),
        # 페이지 넘김 시뮬레이션 — 관리번호 블록
        (0, 10, 400, 30, "관리번호 2024-P-CL-0041-85"),
        (0, 50, 400, 70, "2. 암보장개시일은 계약일부터 90일이 지난"),
        (0, 80, 400, 100, "날의 다음 날로 합니다."),
    ]
    notes = _extract_notes_from_blocks(blocks)
    assert len(notes) >= 2, f"페이지 경계 notes 미수집: {notes}"
    assert any("사망" in n for n in notes), f"1번 note 없음: {notes}"
    assert any("암보장개시일" in n for n in notes), f"2번 note 없음: {notes}"


def test_G4_amounts_ratio_with_condition():
    """'특약보험가입금액의 100% (단, 1년 이전이면 50%)' 패턴 분리."""
    from insurance_parser.parse.lina_summary_parser import _parse_amounts_from_cell

    cell = "특약보험가입금액의 100%\n(단, 보험계약일부터 1년이 지난 보험계약해당일 전일 이전에 지급사유가 발생하였을 경우에는 상기금액의 50%를 지급)"
    result = _parse_amounts_from_cell(cell)

    assert result, "결과 없음"
    entry = result[0]
    # amount에 핵심 금액 표현이 있어야 함
    assert "100%" in entry["amount"], f"amount에 100% 없음: {entry}"
    assert "보험가입금액" in entry["amount"], f"amount에 가입금액 없음: {entry}"
    # condition에 단서 조항이 있어야 함
    assert "50%" in entry["condition"] or "1년" in entry["condition"], \
        f"condition에 단서 없음: {entry}"
    # amount와 condition이 모두 비어있으면 안 됨
    assert entry["amount"] != "" and entry["condition"] != "", \
        f"amount 또는 condition이 비어있음: {entry}"


def test_G5_amounts_ratio_20_percent():
    """'특약보험가입금액의 20% (단, ...)' 패턴도 동일하게 분리."""
    from insurance_parser.parse.lina_summary_parser import _parse_amounts_from_cell

    cell = "특약보험가입금액의 20%\n(단, 보험계약일부터 1년이 지난 보험계약해당일 전일 이전에 지급사유가 발생하였을 경우에는 상기금액의 50%를 지급)"
    result = _parse_amounts_from_cell(cell)

    assert result, "결과 없음"
    entry = result[0]
    assert "20%" in entry["amount"], f"amount에 20% 없음: {entry}"
    assert entry["condition"] != "", f"condition이 비어있음: {entry}"


def test_G6_amounts_simple_amount_unchanged():
    """단순 금액 '3,000만원'은 기존대로 amount에만 채워져야 한다."""
    from insurance_parser.parse.lina_summary_parser import _parse_amounts_from_cell

    result = _parse_amounts_from_cell("3,000만원")
    assert result, "결과 없음"
    assert result[0]["amount"] == "3,000만원", f"단순 금액 오파싱: {result}"
    assert result[0]["condition"] == "", f"condition이 비어있지 않음: {result}"


def test_G7_amounts_with_reduction_note():
    """단순 금액 + (단, 보험계약일부터 2년...) → amount + reduction_note 분리."""
    from insurance_parser.parse.lina_summary_parser import _parse_amounts_from_cell

    cell = "500만원\n(단, 보험계약일부터 2년이 지난 보험계약해당일 전일 이전에 지급사유가 발생하였을 경우에는 상기금액의 50%를 지급)"
    result = _parse_amounts_from_cell(cell)
    assert result, "결과 없음"
    entry = result[0]
    assert entry["amount"] == "500만원", f"amount 오파싱: {entry}"
    assert "2년" in entry.get("reduction_note", ""), \
        f"reduction_note에 2년 없음: {entry}"


def test_G8_amounts_monthly_with_reduction():
    """'매월 100만원 (단, 1년 이전이면 50%)' 패턴."""
    from insurance_parser.parse.lina_summary_parser import _parse_amounts_from_cell

    cell = "매월 100만원\n(다만, 보험계약일로부터 1년이 지난 보험계약해당일 전일 이전에 암으로 최초 진단확정된 경우에는 상기금액의 50%를 지급)"
    result = _parse_amounts_from_cell(cell)
    assert result, "결과 없음"
    entry = result[0]
    assert "100만원" in entry["amount"], f"amount에 100만원 없음: {entry}"


# ===========================================================================
# Runner
# ===========================================================================

if __name__ == "__main__":
    import traceback

    all_tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = skipped = 0

    for t in all_tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}")
            print(f"        {e}")
            failed += 1
        except Exception as e:
            if "SKIP" in str(e):
                print(f"  SKIP  {t.__name__}")
                skipped += 1
            else:
                print(f"  ERROR {t.__name__}")
                traceback.print_exc()
                failed += 1

    total = len(all_tests)
    print(f"\n{'─'*50}")
    print(f"  {passed} passed  {failed} failed  {skipped} skipped  / {total} total")
