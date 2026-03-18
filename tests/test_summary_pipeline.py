"""summary_pipeline regression tests.

4단 파이프라인의 핵심 경로를 회귀 테스트합니다.

테스트 그룹:
  P1~P5: DocumentBundle / BundleStatus 모델
  N1~N5: normalize_summary_data (Stage 2)
  C1~C4: classify_benefits (Stage 3)
  E1~E3: export_to_summary_rows (Stage 4)
  I1~I3: 파이프라인 통합 (실제 PDF 있을 때만 실행)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from insurance_parser.summary_pipeline.models import (
    AmountEntry,
    BundleStatus,
    DocType,
    CanonicalBenefit,
    DocumentBundle,
    PipelineResult,
    SummaryRow,
)
from insurance_parser.summary_pipeline.normalizer import (
    export_to_summary_rows,
    normalize_summary_data,
    export_to_summary_row,
)
from insurance_parser.summary_pipeline.classifier import (
    classify_benefit_category,
    detect_insurance_type,
    classify_benefits,
)

# ---------------------------------------------------------------------------
# P1~P5: DocumentBundle / BundleStatus
# ---------------------------------------------------------------------------

class TestDocumentBundle:
    def test_P1_complete_when_both_files_exist(self, tmp_path):
        """P1: 요약서 + 약관 파일 모두 존재 → BOTH"""
        s = tmp_path / "summary.pdf"; s.write_text("x")
        t = tmp_path / "terms.pdf";   t.write_text("x")
        b = DocumentBundle(
            company_name="테스트생명", product_name="테스트상품",
            summary_pdf=str(s), terms_pdf=str(t),
        )
        assert b.status == BundleStatus.BOTH
        assert b.is_complete

    def test_P2_incomplete_summary_only(self, tmp_path):
        """P2: 요약서만 있으면 SUMMARY_ONLY"""
        s = tmp_path / "summary.pdf"; s.write_text("x")
        b = DocumentBundle(
            company_name="A", product_name="B",
            summary_pdf=str(s), terms_pdf=None,
        )
        assert b.status == BundleStatus.SUMMARY_ONLY

    def test_P3_incomplete_terms_only(self, tmp_path):
        """P3: 약관만 있으면 TERMS_ONLY"""
        t = tmp_path / "terms.pdf"; t.write_text("x")
        b = DocumentBundle(
            company_name="A", product_name="B",
            summary_pdf=None, terms_pdf=str(t),
        )
        assert b.status == BundleStatus.TERMS_ONLY

    def test_P4_validate_for_analysis_raises_for_incomplete(self, tmp_path):
        """P4: BOTH가 아닌 번들에 validate_for_analysis() → ValueError"""
        s = tmp_path / "summary.pdf"; s.write_text("x")
        b = DocumentBundle(
            company_name="A", product_name="B",
            summary_pdf=str(s), terms_pdf=None,
        )
        with pytest.raises(ValueError, match="BOTH|require_complete"):
            b.validate_for_analysis()

    def test_P5_nonexistent_file_treated_as_missing(self, tmp_path):
        """P5: 경로는 있지만 파일 없으면 UNKNOWN 또는 SUMMARY_ONLY"""
        b = DocumentBundle(
            company_name="A", product_name="B",
            summary_pdf="/nonexistent/path/summary.pdf",
            terms_pdf=None,
        )
        assert b.status not in (BundleStatus.BOTH, BundleStatus.TERMS_ONLY)

    def test_P6_soft_status_not_blocking(self, tmp_path):
        """P6: SUMMARY_ONLY는 파이프라인 실행을 막지 않음 (graceful degradation)"""
        s = tmp_path / "x.pdf"; s.write_text("x")
        b = DocumentBundle(company_name="A", product_name="B", summary_pdf=str(s))
        assert b.status == BundleStatus.SUMMARY_ONLY
        # validate_for_analysis 호출 없이 status만 확인

    def test_P7_bundle_status_label_ko(self):
        """P7: BundleStatus.label_ko 반환"""
        assert BundleStatus.BOTH.label_ko == "요약서+약관"
        assert BundleStatus.SUMMARY_ONLY.label_ko == "요약서만"
        assert BundleStatus.TERMS_ONLY.label_ko == "약관만"
        assert BundleStatus.UNKNOWN.label_ko == "문서 없음"


# ---------------------------------------------------------------------------
# N1~N5: normalize_summary_data (Stage 2)
# ---------------------------------------------------------------------------

def _make_raw_contract(
    name="테스트특약",
    contract_type="rider",
    benefits=None,
    notes=None,
) -> dict:
    return {
        "name": name,
        "type": contract_type,
        "code": "",
        "reference_amount": "3,000만원",
        "source_pdf": "test.pdf",
        "benefits": benefits or [
            {
                "benefit_names": ["진단보험금"],
                "trigger": "암으로 진단확정",
                "amounts": [{"amount": "3,000만원", "condition": "", "reduction_note": ""}],
            }
        ],
        "notes": notes or [],
    }


class TestNormalizeSummaryData:
    def test_N1_basic_contract(self):
        """N1: 기본 contract 1건 → CanonicalBenefit 1건"""
        data = {"contracts": [_make_raw_contract()]}
        result = normalize_summary_data(data, "라이나생명", "테스트암보험")
        assert len(result) == 1
        assert result[0].benefit_name == "진단보험금"
        assert result[0].contract_name == "테스트특약"
        assert result[0].company_name == "라이나생명"

    def test_N2_multiple_benefit_names_split_to_separate_rows(self):
        """N2: benefit_names 복수 → 각각 별도 CanonicalBenefit"""
        benefit = {
            "benefit_names": ["암진단보험금", "고액암진단보험금"],
            "trigger": "암으로 진단",
            "amounts": [{"amount": "1,000만원", "condition": "", "reduction_note": ""}],
        }
        data = {"contracts": [_make_raw_contract(benefits=[benefit])]}
        result = normalize_summary_data(data, "A", "B")
        assert len(result) == 2
        names = {r.benefit_name for r in result}
        assert "암진단보험금" in names
        assert "고액암진단보험금" in names

    def test_N3_amount_condition_preserved(self):
        """N3: amount + condition 분리 보존"""
        benefit = {
            "benefit_names": ["암진단보험금"],
            "trigger": "암 진단",
            "amounts": [
                {"amount": "특약보험가입금액의 100%", "condition": "(단, 1년 이전이면 50%)", "reduction_note": ""}
            ],
        }
        data = {"contracts": [_make_raw_contract(benefits=[benefit])]}
        result = normalize_summary_data(data, "A", "B")
        assert result[0].amounts[0].amount == "특약보험가입금액의 100%"
        assert "(단, 1년 이전이면 50%)" in result[0].amounts[0].condition

    def test_N4_riders_merged(self):
        """N4: riders 중첩 구조도 contracts와 합산"""
        raw_rider = _make_raw_contract(name="선택특약A")
        data = {
            "contracts": [_make_raw_contract(name="특약B")],
            "riders": [{"contracts": [raw_rider]}],
        }
        result = normalize_summary_data(data, "A", "B")
        assert len(result) == 2
        names = {r.contract_name for r in result}
        assert "특약B" in names
        assert "선택특약A" in names

    def test_N5_empty_benefit_names_yields_one_row(self):
        """N5: benefit_names 비어있으면 빈 이름 1건"""
        benefit = {
            "benefit_names": [],
            "trigger": "사망",
            "amounts": [{"amount": "1,000만원", "condition": "", "reduction_note": ""}],
        }
        data = {"contracts": [_make_raw_contract(benefits=[benefit])]}
        result = normalize_summary_data(data, "A", "B")
        assert len(result) == 1
        assert result[0].benefit_name == ""


# ---------------------------------------------------------------------------
# C1~C4: classify_benefits (Stage 3)
# ---------------------------------------------------------------------------

class TestClassifier:
    def test_C1_detect_cancer_insurance_type(self):
        """C1: 상품명에 '암' 포함 → 암보험"""
        assert detect_insurance_type("무배당 뉴스타트플러스암보험") == "암보험"

    def test_C2_detect_dementia_insurance_type(self):
        """C2: '치매' 포함 → 치매보험"""
        assert detect_insurance_type("라이나치매보험") == "치매보험"

    def test_C3_classify_diagnosis_category(self):
        """C3: '진단확정' 트리거 → diagnosis"""
        cat, cat_ko = classify_benefit_category("암진단보험금", "암으로 진단확정 되었을 때", "암보험")
        assert cat == "diagnosis"
        assert cat_ko == "진단"

    def test_C4_classify_treatment_over_diagnosis(self):
        """C4: '직접치료' 트리거 → treatment (diagnosis보다 우선)"""
        cat, cat_ko = classify_benefit_category("암직접치료급여금", "암의 직접적인 치료", "암보험")
        assert cat == "treatment"
        assert cat_ko == "치료"

    def test_C5_classify_unknown_falls_to_other(self):
        """C5: 키워드 매칭 없으면 other"""
        cat, cat_ko = classify_benefit_category("알수없는급부", "알수없는사유", "암보험")
        assert cat == "other"

    def test_C6_classify_benefits_fills_category_in_place(self):
        """C6: classify_benefits()가 리스트 내 benefit_category 채움"""
        b = CanonicalBenefit(
            company_name="A", product_name="암보험X",
            benefit_name="암진단보험금", trigger="암으로 진단확정",
        )
        result = classify_benefits([b])
        assert result[0].benefit_category == "diagnosis"
        assert result[0].insurance_type == "암보험"


# ---------------------------------------------------------------------------
# E1~E3: export_to_summary_rows (Stage 4)
# ---------------------------------------------------------------------------

class TestExportToSummaryRows:
    def test_E1_single_amount_produces_one_row(self):
        """E1: amount 1건 → SummaryRow 1건"""
        b = CanonicalBenefit(
            company_name="라이나생명",
            product_name="암보험",
            contract_name="테스트특약",
            benefit_name="진단보험금",
            trigger="암 진단",
            amounts=[AmountEntry(amount="3,000만원", condition="", reduction_note="")],
            benefit_category="diagnosis",
            benefit_category_ko="진단",
        )
        rows = export_to_summary_row(b)
        assert len(rows) == 1
        assert rows[0].amount == "3,000만원"
        assert rows[0].insurer == "라이나생명"

    def test_E2_multiple_amounts_produce_multiple_rows(self):
        """E2: amount 복수 → SummaryRow 복수 (각 condition별)"""
        b = CanonicalBenefit(
            company_name="A", product_name="B",
            benefit_name="진단보험금",
            trigger="암 진단",
            amounts=[
                AmountEntry(amount="100%", condition="2년 이후"),
                AmountEntry(amount="50%", condition="1년 이전"),
            ],
        )
        rows = export_to_summary_rows([b])
        assert len(rows) == 2
        amounts = {r.amount for r in rows}
        assert "100%" in amounts
        assert "50%" in amounts

    def test_E3_no_amounts_produces_one_empty_row(self):
        """E3: amounts 없으면 빈 SummaryRow 1건"""
        b = CanonicalBenefit(
            company_name="A", product_name="B",
            benefit_name="사망보험금", trigger="사망",
        )
        rows = export_to_summary_rows([b])
        assert len(rows) == 1
        assert rows[0].amount == ""

    def test_E4_amount_detail_json_when_multiple(self):
        """E4: amount 2건이상 → amount_detail에 JSON 저장"""
        b = CanonicalBenefit(
            company_name="A", product_name="B",
            benefit_name="수술급부",
            amounts=[
                AmountEntry(amount="200만원", condition="1회"),
                AmountEntry(amount="100만원", condition="추가"),
            ],
        )
        rows = export_to_summary_rows([b])
        for row in rows:
            detail = json.loads(row.amount_detail)
            assert len(detail) == 2

    def test_E5_renewal_type_extracted_from_contract_name(self):
        """E5: 계약명에 '갱신형' 포함 → renewal_type 자동 추출"""
        b = CanonicalBenefit(
            company_name="A", product_name="B",
            contract_name="무배당 암진단특약(갱신형)",
            benefit_name="진단보험금",
        )
        rows = export_to_summary_rows([b])
        assert rows[0].renewal_type == "갱신형"

    def test_E6_notes_joined_with_pipe(self):
        """E6: notes 복수 → notes_summary에 | 구분자로 연결"""
        b = CanonicalBenefit(
            company_name="A", product_name="B",
            benefit_name="X",
            notes=["주1", "주2", "주3"],
        )
        rows = export_to_summary_rows([b])
        assert rows[0].notes_summary == "주1 | 주2 | 주3"


# ---------------------------------------------------------------------------
# I1~I3: 파이프라인 통합 (실제 PDF 있을 때만)
# ---------------------------------------------------------------------------

LINA_PDF = Path("linalife/암보험/상품요약서/무배당첫날부터암보험(갱신형)/B00355005_5_S.pdf")
HANWHA_PDF = Path("hanwhalife/암보험/상품요약서/한화생명 Need AI 암보험 무배당/한화생명 Need AI 암보험 무배당_상품요약서_20260101.pdf")


@pytest.mark.skipif(not LINA_PDF.exists(), reason="라이나 샘플 PDF 없음")
class TestLinaPipeline:
    def test_I1_lina_pipeline_produces_canonical_benefits(self):
        """I1: 라이나 요약서 → canonical_benefits 1건 이상"""
        from insurance_parser.summary_pipeline import run_pipeline
        bundle = DocumentBundle(
            company_name="라이나생명",
            product_name="무배당 첫날부터암보험(갱신형)",
            summary_pdf=str(LINA_PDF),
        )
        result = run_pipeline(bundle)
        assert len(result.canonical_benefits) > 0
        assert len(result.summary_rows) > 0

    def test_I2_lina_benefits_have_category(self):
        """I2: 라이나 급부가 모두 benefit_category 있음"""
        from insurance_parser.summary_pipeline import run_pipeline
        bundle = DocumentBundle(
            company_name="라이나생명",
            product_name="무배당 첫날부터암보험(갱신형)",
            summary_pdf=str(LINA_PDF),
        )
        result = run_pipeline(bundle)
        for b in result.canonical_benefits:
            assert b.benefit_category, f"benefit_category 없음: {b.benefit_name}"

    def test_I3_lina_insurance_type_is_cancer(self):
        """I3: 라이나 암보험 → insurance_type = 암보험"""
        from insurance_parser.summary_pipeline import run_pipeline
        bundle = DocumentBundle(
            company_name="라이나생명",
            product_name="무배당 첫날부터암보험(갱신형)",
            summary_pdf=str(LINA_PDF),
        )
        result = run_pipeline(bundle)
        types = {b.insurance_type for b in result.canonical_benefits}
        assert "암보험" in types


@pytest.mark.skipif(not HANWHA_PDF.exists(), reason="한화 샘플 PDF 없음")
class TestHanwhaPipeline:
    def test_I4_hanwha_pipeline_produces_summary_rows(self):
        """I4: 한화 요약서 → summary_rows 1건 이상"""
        from insurance_parser.summary_pipeline import run_pipeline
        bundle = DocumentBundle(
            company_name="한화생명",
            product_name="한화생명 Need AI 암보험 무배당",
            summary_pdf=str(HANWHA_PDF),
        )
        result = run_pipeline(bundle)
        assert len(result.summary_rows) > 0

    def test_I5_require_complete_raises_for_summary_only(self):
        """I5: terms_pdf 없을 때 require_complete=True → ValueError"""
        from insurance_parser.summary_pipeline import run_pipeline
        bundle = DocumentBundle(
            company_name="한화생명",
            product_name="한화생명 Need AI 암보험 무배당",
            summary_pdf=str(HANWHA_PDF),
        )
        with pytest.raises(ValueError, match="BOTH|require_complete"):
            run_pipeline(bundle, require_complete=True)

    def test_I6_graceful_degradation_no_require_complete(self):
        """I6: require_complete=False(기본값)이면 SUMMARY_ONLY도 파싱 성공"""
        from insurance_parser.summary_pipeline import run_pipeline
        bundle = DocumentBundle(
            company_name="한화생명",
            product_name="한화생명 Need AI 암보험 무배당",
            summary_pdf=str(HANWHA_PDF),
        )
        result = run_pipeline(bundle)  # require_complete 기본값 False
        assert len(result.summary_rows) > 0
        assert result.status == BundleStatus.SUMMARY_ONLY

    def test_I7_summary_rows_have_bundle_status(self):
        """I7: SummaryRow에 bundle_status 필드 채워짐"""
        from insurance_parser.summary_pipeline import run_pipeline
        bundle = DocumentBundle(
            company_name="한화생명",
            product_name="한화생명 Need AI 암보험 무배당",
            summary_pdf=str(HANWHA_PDF),
        )
        result = run_pipeline(bundle)
        for row in result.summary_rows:
            assert row.bundle_status == "SUMMARY_ONLY"
            assert row.partial is True

    def test_I8_terms_only_produces_warning_not_error(self):
        """I8: TERMS_ONLY 상태는 error가 아니라 warning — 앱이 죽지 않음"""
        from insurance_parser.summary_pipeline import run_pipeline
        bundle = DocumentBundle(
            company_name="한화생명",
            product_name="한화생명 Need AI 암보험 무배당",
            terms_pdf=str(HANWHA_PDF),   # terms 슬롯에만
            summary_pdf=None,
        )
        result = run_pipeline(bundle)
        assert result.status == BundleStatus.TERMS_ONLY
        # TERMS_ONLY는 오류가 아님 — 파이프라인이 계속 실행됨
        terms_warnings = [w for w in result.warnings if "TERMS_ONLY" in w or "제한적" in w or "stub" in w]
        assert len(terms_warnings) >= 1, "TERMS_ONLY 상태 경고가 없음"


# ---------------------------------------------------------------------------
# D1~D6: DocType 판별기
# ---------------------------------------------------------------------------

class TestDocDetector:
    def test_D1_summary_by_filename(self):
        """D1: 파일명에 '요약서' 포함 → SUMMARY"""
        from insurance_parser.summary_pipeline import detect_doc_type
        assert detect_doc_type("path/to/상품요약서_S.pdf") == DocType.SUMMARY

    def test_D2_terms_by_filename(self):
        """D2: 파일명에 '약관' 포함 → TERMS"""
        from insurance_parser.summary_pipeline import detect_doc_type
        assert detect_doc_type("path/to/보통약관.pdf") == DocType.TERMS

    def test_D3_summary_S_suffix(self):
        """D3: _S.pdf 패턴 → SUMMARY"""
        from insurance_parser.summary_pipeline import detect_doc_type
        assert detect_doc_type("B00279008_6_S.pdf") == DocType.SUMMARY

    def test_D4_classify_upload_returns_dict(self, tmp_path):
        """D4: classify_upload() → doc_type, doc_type_ko 포함 dict 반환"""
        from insurance_parser.summary_pipeline import classify_upload
        f = tmp_path / "보통약관.pdf"
        f.write_text("x")
        result = classify_upload(str(f))
        assert "doc_type" in result
        assert "doc_type_ko" in result
        assert result["doc_type"] == "terms"

    def test_D5_unknown_file_returns_unknown(self, tmp_path):
        """D5: 파일명 패턴 없으면 UNKNOWN (routing hint)"""
        from insurance_parser.summary_pipeline import detect_doc_type
        f = tmp_path / "document_xyz_123.pdf"
        f.write_text("x")
        # 파일명으로 판별 불가 → UNKNOWN
        assert detect_doc_type(str(f)) == DocType.UNKNOWN

    def test_D6_classify_upload_is_hint_only_for_unknown(self, tmp_path):
        """D6: unknown 문서는 is_hint_only=True"""
        from insurance_parser.summary_pipeline import classify_upload
        f = tmp_path / "nopattern123.pdf"
        f.write_text("x")
        result = classify_upload(str(f))
        if result["doc_type"] == "unknown":
            assert result.get("is_hint_only") is True


# ---------------------------------------------------------------------------
# S1~S3: ArtifactStore
# ---------------------------------------------------------------------------

class TestArtifactStore:
    def test_S1_save_and_load_upload(self, tmp_path):
        """S1: save_upload → load_uploads로 복원 가능"""
        from insurance_parser.summary_pipeline.store import ArtifactStore
        store = ArtifactStore(base_dir=str(tmp_path))
        rows = [{"insurer": "테스트", "benefit_name": "진단보험금", "amount": "1,000만원"}]
        store.save_upload("테스트생명", rows, product_name="암보험", doc_type="summary")
        loaded = store.load_uploads()
        assert len(loaded) == 1
        assert loaded[0]["insurer"] == "테스트"

    def test_S2_list_companies_from_all(self, tmp_path):
        """S2: list_companies() → 회사명 목록 반환"""
        from insurance_parser.summary_pipeline.store import ArtifactStore
        store = ArtifactStore(base_dir=str(tmp_path))
        store.save_upload("A생명", [{"insurer": "A생명"}])
        store.save_upload("B생명", [{"insurer": "B생명"}])
        companies = store.list_companies()
        assert "A생명" in companies
        assert "B생명" in companies

    def test_S3_load_prebuilt_returns_empty_when_missing(self, tmp_path):
        """S3: prebuilt 파일 없으면 빈 리스트 반환"""
        from insurance_parser.summary_pipeline.store import ArtifactStore
        store = ArtifactStore(base_dir=str(tmp_path))
        assert store.load_prebuilt() == []

    def test_S4_upload_meta_contains_required_fields(self, tmp_path):
        """S4: 저장된 artifact에 필수 메타 필드 포함"""
        from insurance_parser.summary_pipeline.store import ArtifactStore, ARTIFACT_VERSION
        store = ArtifactStore(base_dir=str(tmp_path))
        rows = [{"insurer": "A생명"}]
        store.save_upload("A생명", rows, product_name="암보험", doc_type="summary")
        metas = store.list_upload_metas()
        assert len(metas) == 1
        m = metas[0]
        for key in ["company_name", "product_name", "doc_type", "uploaded_at", "file_hash", "artifact_version", "row_count"]:
            assert key in m, f"메타 필드 누락: {key}"
        assert m["artifact_version"] == ARTIFACT_VERSION
        assert m["row_count"] == 1

    def test_S5_old_format_list_still_loads(self, tmp_path):
        """S5: v1.0 포맷(배열) JSON도 로드 가능 (하위 호환)"""
        import json
        from insurance_parser.summary_pipeline.store import ArtifactStore, _UPLOAD_PREFIX
        store = ArtifactStore(base_dir=str(tmp_path))
        old_rows = [{"insurer": "구버전생명", "benefit_name": "X"}]
        (tmp_path / f"{_UPLOAD_PREFIX}old_999.json").write_text(
            json.dumps(old_rows, ensure_ascii=False), encoding="utf-8"
        )
        loaded = store.load_uploads()
        assert len(loaded) == 1
        assert loaded[0]["insurer"] == "구버전생명"


# ---------------------------------------------------------------------------
# DEDUP1~DEDUP6: 중복 방지 재현 테스트
# ---------------------------------------------------------------------------

class TestDedupe:
    """파이프라인/저장 단계 중복 재현 및 방지 테스트."""

    def test_DEDUP1_export_same_benefit_twice_deduped(self):
        """DEDUP1: 동일 CanonicalBenefit 2번 export → dedupe 후 1건만 남음."""
        b = CanonicalBenefit(
            company_name="라이나생명", product_name="암보험A",
            contract_name="암진단특약", benefit_name="진단보험금",
            trigger="암으로 진단확정",
            amounts=[AmountEntry(amount="3,000만원", condition="", reduction_note="")],
            source_pdf="test.pdf",
        )
        rows = export_to_summary_rows([b, b])   # 동일 benefit 2회
        assert len(rows) == 1, f"중복 제거 실패: {len(rows)}건"

    def test_DEDUP2_different_amounts_not_deduped(self):
        """DEDUP2: amount만 다른 행은 dedupe 대상이 아님 (정상 2건)."""
        b = CanonicalBenefit(
            company_name="A", product_name="B",
            contract_name="특약", benefit_name="진단보험금",
            trigger="암 진단",
            amounts=[
                AmountEntry(amount="100%", condition="2년 이후"),
                AmountEntry(amount="50%",  condition="1년 이전"),
            ],
            source_pdf="x.pdf",
        )
        rows = export_to_summary_rows([b])
        assert len(rows) == 2, "amount별 분리가 dedupe로 사라지면 안 됨"

    def test_DEDUP3_duplicate_contract_in_contracts_and_riders(self):
        """DEDUP3: contracts + riders에 동일 contract 중복 → normalize 후 1건만."""
        contract = _make_raw_contract(name="중복특약", benefits=[
            {"benefit_names": ["진단보험금"], "trigger": "암 진단",
             "amounts": [{"amount": "1,000만원", "condition": "", "reduction_note": ""}]}
        ])
        data = {
            "contracts": [contract],
            "riders": [contract],
        }
        result = normalize_summary_data(data, "A", "암보험")
        assert len(result) == 1, f"contract 중복 방지 실패: {len(result)}건"

    def test_DEDUP4_dedupe_key_generated_in_each_row(self):
        """DEDUP4: export된 각 SummaryRow에 dedupe_key가 빈 문자열이 아님."""
        b = CanonicalBenefit(
            company_name="A", product_name="B",
            benefit_name="급부X", trigger="사유",
            amounts=[AmountEntry(amount="100만원")],
        )
        rows = export_to_summary_rows([b])
        assert all(row.dedupe_key for row in rows), "dedupe_key 없는 행 존재"

    def test_DEDUP5_store_duplicate_file_hash_not_saved_twice(self, tmp_path):
        """DEDUP5: 동일 file_hash artifact 재저장 시 파일 중복 생성 안 됨."""
        import json
        from insurance_parser.summary_pipeline.store import ArtifactStore, _UPLOAD_PREFIX

        src = tmp_path / "source.pdf"
        src.write_bytes(b"dummy pdf content")

        store = ArtifactStore(base_dir=str(tmp_path))
        rows = [{"insurer": "테스트생명", "benefit_name": "진단보험금", "amount": "1,000만원", "dedupe_key": "abc"}]

        store.save_upload("테스트생명", rows, product_name="암보험",
                          doc_type="summary", source_path=str(src))
        store.save_upload("테스트생명", rows, product_name="암보험",
                          doc_type="summary", source_path=str(src))   # 동일 파일 재저장

        upload_files = list(tmp_path.glob(f"{_UPLOAD_PREFIX}*.json"))
        assert len(upload_files) == 1, f"중복 artifact 파일 생성됨: {[f.name for f in upload_files]}"

    def test_DEDUP6_load_all_dedupes_prebuilt_and_upload_overlap(self, tmp_path):
        """DEDUP6: prebuilt + upload에 동일 dedupe_key 행 있으면 load_all 후 1건만."""
        import json
        from insurance_parser.summary_pipeline.store import ArtifactStore, _PREBUILT_FILENAME, _UPLOAD_PREFIX

        store = ArtifactStore(base_dir=str(tmp_path))

        shared_row = {
            "insurer": "라이나생명", "product_name": "암보험A",
            "contract_name": "암진단특약", "benefit_name": "진단보험금",
            "amount": "3,000만원", "amount_condition": "",
            "trigger": "암으로 진단확정", "source_pdf": "test.pdf",
            "dedupe_key": "deadbeef1234abcd",
        }

        # prebuilt에 저장
        prebuilt_path = tmp_path / _PREBUILT_FILENAME
        prebuilt_path.write_text(
            json.dumps([shared_row], ensure_ascii=False), encoding="utf-8"
        )

        # upload에도 동일 행 저장
        upload_path = tmp_path / f"{_UPLOAD_PREFIX}test_123.json"
        upload_path.write_text(
            json.dumps({"_meta": {"file_hash": "unique_hash_xyz", "artifact_version": "1.1",
                                   "company_name": "라이나생명", "product_name": "암보험A",
                                   "doc_type": "summary", "uploaded_at": "2026-01-01T00:00:00Z",
                                   "row_count": 1}, "rows": [shared_row]},
                       ensure_ascii=False), encoding="utf-8"
        )

        all_rows = store.load_all()
        keys = [r.get("dedupe_key") for r in all_rows]
        assert keys.count("deadbeef1234abcd") == 1, (
            f"prebuilt+upload 중복 제거 실패: dedupe_key 'deadbeef1234abcd'가 {keys.count('deadbeef1234abcd')}번 등장"
        )


# ---------------------------------------------------------------------------
# COMPARE1~COMPARE3: to_comparison_rows 집약 테스트
# ---------------------------------------------------------------------------

class TestComparisonRows:
    """detail_rows → comparison_rows 집약 테스트."""

    def test_COMPARE1_disease_group_rows_collapsed_to_one(self):
        """COMPARE1: 동일 benefit_name, 다른 trigger(질병군)인 N행 → comparison 1행."""
        from insurance_parser.summary_pipeline.normalizer import to_comparison_rows

        detail = [
            {"insurer": "한화생명", "product_name": "암보험A",
             "contract_name": "암보장특약", "benefit_name": "소액질병진단자금",
             "trigger": "기타피부암으로 진단", "amount": "100만원",
             "amount_condition": "1년미만", "dedupe_key": "k1"},
            {"insurer": "한화생명", "product_name": "암보험A",
             "contract_name": "암보장특약", "benefit_name": "소액질병진단자금",
             "trigger": "기타피부암으로 진단", "amount": "200만원",
             "amount_condition": "1년이상", "dedupe_key": "k2"},
            {"insurer": "한화생명", "product_name": "암보험A",
             "contract_name": "암보장특약", "benefit_name": "소액질병진단자금",
             "trigger": "갑상선암으로 진단", "amount": "100만원",
             "amount_condition": "1년미만", "dedupe_key": "k3"},
            {"insurer": "한화생명", "product_name": "암보험A",
             "contract_name": "암보장특약", "benefit_name": "소액질병진단자금",
             "trigger": "갑상선암으로 진단", "amount": "200만원",
             "amount_condition": "1년이상", "dedupe_key": "k4"},
        ]
        comp = to_comparison_rows(detail)
        benefit_comp = [r for r in comp if r["benefit_name"] == "소액질병진단자금"]
        assert len(benefit_comp) == 1, f"질병군 집약 실패: {len(benefit_comp)}행"
        assert benefit_comp[0]["detail_row_count"] == 4

    def test_COMPARE2_trigger_variants_json_when_multiple_diseases(self):
        """COMPARE2: 질병군 trigger가 2개 이상이면 trigger_variants에 JSON 저장."""
        import json as _json
        from insurance_parser.summary_pipeline.normalizer import to_comparison_rows

        detail = [
            {"insurer": "A", "product_name": "B", "contract_name": "C",
             "benefit_name": "소액질병", "trigger": "기타피부암 진단",
             "amount": "100만원", "amount_condition": "1년미만", "dedupe_key": "a1"},
            {"insurer": "A", "product_name": "B", "contract_name": "C",
             "benefit_name": "소액질병", "trigger": "갑상선암 진단",
             "amount": "100만원", "amount_condition": "1년미만", "dedupe_key": "a2"},
        ]
        comp = to_comparison_rows(detail)
        assert len(comp) == 1
        variants = comp[0].get("trigger_variants", "")
        assert variants, "trigger_variants 없음"
        parsed = _json.loads(variants)
        assert len(parsed) == 2

    def test_COMPARE3_different_benefit_names_not_collapsed(self):
        """COMPARE3: benefit_name이 다른 행은 각각 별도 comparison_row."""
        from insurance_parser.summary_pipeline.normalizer import to_comparison_rows

        detail = [
            {"insurer": "A", "product_name": "B", "contract_name": "C",
             "benefit_name": "암진단자금", "trigger": "암 진단",
             "amount": "1000만원", "amount_condition": "", "dedupe_key": "b1"},
            {"insurer": "A", "product_name": "B", "contract_name": "C",
             "benefit_name": "소액질병진단자금", "trigger": "소액질병 진단",
             "amount": "100만원", "amount_condition": "", "dedupe_key": "b2"},
        ]
        comp = to_comparison_rows(detail)
        assert len(comp) == 2, f"다른 benefit이 합쳐짐: {len(comp)}행"

    def test_COMPARE4_empty_input_returns_empty(self):
        """COMPARE4: 빈 입력 → 빈 리스트."""
        from insurance_parser.summary_pipeline.normalizer import to_comparison_rows
        assert to_comparison_rows([]) == []

    def test_COMPARE5_hanwha_disease_group_table_produces_correct_detail_rows(self):
        """COMPARE5: 한화 소액질병 테이블(7질병×2조건) → _parse_benefit_table 7개 benefit."""
        from insurance_parser.parse.hanwha_summary_parser import _parse_benefit_table
        import fitz

        class _FakeTab:
            """16행 소액질병 테이블을 흉내냄."""
            def extract(self):
                return [
                    ["급부명칭", "지급사유", "지급금액\n경과기간", ""],
                    ["", "", "경과기간", ""],
                    ["소액질병진단자금", "기타피부암으로 진단", "1년미만", "100만원"],
                    ["", "", "1년이상", "200만원"],
                    ["", "갑상선암으로 진단", "1년미만", "100만원"],
                    ["", "", "1년이상", "200만원"],
                    ["", "제자리암으로 진단", "1년미만", "100만원"],
                    ["", "", "1년이상", "200만원"],
                ]

        benefits = _parse_benefit_table(_FakeTab())
        assert len(benefits) == 3, f"질병군 분리 실패: {len(benefits)}개"
        for b in benefits:
            assert b["benefit_names"] == ["소액질병진단자금"]
            assert len(b["amounts"]) == 2, f"amounts 2건 기대: {b['amounts']}"
