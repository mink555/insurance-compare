"""workbench.py — 보험 특약 비교 AI 솔루션 단계형 UX.

단계형 흐름:
  STEP 1 (setup)   — 상품 선택 + 미리보기 + "비교 시작" 버튼
  STEP 2 (compare) — 카테고리별 비교표 카드 + 핵심 차이 칩 + "리포트 생성" 버튼
  STEP 3 (report)  — 리포트 전용 페이지 (리포트 문서 스타일)

비교 단위: comparison_rows (detail_rows는 expander 내부에서만 노출)
"""
from __future__ import annotations

import base64
import json
import re
import tempfile
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

import sys
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from insurance_parser.summary_pipeline import (
    DocumentBundle,
    run_pipeline,
)
from insurance_parser.summary_pipeline.store import ArtifactStore
from insurance_parser.summary_pipeline.normalizer import to_comparison_rows
from insurance_parser.summary_pipeline.detector import _SUMMARY_NAME_RE, _TERMS_NAME_RE
from insurance_parser.report.generator import SummaryReportBuilder
from insurance_parser.comparison.engine import build_comparison, ComparisonResult, rebuild_amount_table
from insurance_parser.comparison.enrich import enrich_rows, resolve_mixed_pairs

_store = ArtifactStore()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 상수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_OUR_COMPANY = "라이나생명"

_STATUS_BADGE = {
    "BOTH":         ("badge-both",    "요약서+약관"),
    "SUMMARY_ONLY": ("badge-summary", "요약서"),
    "TERMS_ONLY":   ("badge-terms",   "약관"),
    "UNKNOWN":      ("badge-unknown", "알수없음"),
}

# 암 분류 정의 테이블 데이터 (expander용, KCD9 기준 참고 정보)
_CANCER_CLASS_DEFS = [
    {
        "cls":    "일반암",
        "kcd":    "C00-C97 (소액암 제외)\n+ D45/D46/D47일부",
        "examples": "폐암, 위암, 간암, 대장암 등 대부분의 악성신생물",
        "note":   "진단금 100% 기준",
    },
    {
        "cls":    "고액암",
        "kcd":    "보험사 약관 정의\n(KCD 표준 없음)",
        "examples": "백혈병, 뇌암, 췌장암, 담도암, 림프종, 다발성골수종",
        "note":   "진단금 200~300% 추가 지급 (생보사 주로 사용)",
    },
    {
        "cls":    "소액암",
        "kcd":    "C44, C61, C73\nD01.0-D01.2",
        "examples": "기타피부암, 전립선암, 갑상선암, 대장점막내암\n(+관행: 유방암, 방광암, 자궁경부암)",
        "note":   "진단금 ~20% (치료비 적고 예후 좋음)",
    },
    {
        "cls":    "유사암",
        "kcd":    "D00-D09\nD37-D48 (일부 제외)",
        "examples": "제자리암(상피내암), 경계성종양",
        "note":   "진단금 ~20% (암에 가까운 성격의 질병)",
    },
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HTML 유틸
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _badge(cls: str, text: str) -> str:
    return f'<span class="badge {cls}">{text}</span>'


def _status_html(status: str) -> str:
    mapping = {
        "양사동일": '<span class="status-both-same">양사동일</span>',
        "동일":    '<span class="status-both-same">동일</span>',
        "금액상이": '<span class="status-both-diff">금액상이</span>',
        "조건상이": '<span class="status-both-diff">조건상이</span>',
        "당사우위": '<span class="status-only-our">당사우위</span>',
        "타사우위": '<span class="status-only-comp">타사우위</span>',
        "당사단독": '<span class="status-only-our">당사단독</span>',
        "타사단독": '<span class="status-only-comp">타사단독</span>',
        "비교불가": '<span class="status-unknown">비교불가</span>',
        "표시":    "",  # display_only 불일치: 배지 없음, 값만 나란히 표시
        "상이":    "",  # 하위 호환: 표시와 동일하게 배지 없음
    }
    return mapping.get(status, f'<span class="status-unknown">{status}</span>')


def _section_label(title: str) -> None:
    st.markdown(
        f'<div class="section-label">{title}</div>', unsafe_allow_html=True
    )


def _divider() -> None:
    st.markdown('<hr class="divider">', unsafe_allow_html=True)


def _card_stat(label: str, value: str, accent: str = "var(--g9)", sub: str = "") -> str:
    return (
        f'<div class="stat-card">'
        f'<div class="detail-meta">{label}</div>'
        f'<div class="detail-value" style="color:{accent};">{value}</div>'
        f'{"<div class=detail-meta>" + sub + "</div>" if sub else ""}'
        f'</div>'
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 세션 / 데이터
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _init() -> None:
    if st.session_state.get("wb_df") is None:
        with st.spinner("데이터 로딩 중..."):
            rows = _store.load_all()
            st.session_state["wb_df"] = pd.DataFrame(rows) if rows else pd.DataFrame()


def _df() -> pd.DataFrame:
    v = st.session_state.get("wb_df")
    if v is None or (isinstance(v, pd.DataFrame) and v.empty):
        return pd.DataFrame()
    return v


def _companies() -> list[str]:
    df = _df()
    return sorted(df["insurer"].dropna().unique().tolist()) if not df.empty else []


def _products_of(co: str) -> list[str]:
    df = _df()
    if df.empty:
        return []
    return sorted(df[df["insurer"] == co]["product_name"].dropna().unique().tolist())


def _riders_of(co: str, prod: str) -> list[str]:
    df = _df()
    if df.empty:
        return []
    return sorted(
        df[(df["insurer"] == co) & (df["product_name"] == prod)]["contract_name"]
        .dropna().unique().tolist()
    )


def _apply_filter_mask(ctx: dict, side: str) -> pd.DataFrame:
    """ctx에서 회사/상품/특약 조건으로 마스킹된 원본 DataFrame 반환."""
    df = _df()
    if df.empty:
        return pd.DataFrame()
    co    = ctx.get(f"{side}_co", "")
    prod  = ctx.get(f"{side}_prod", "")
    rider = ctx.get(f"{side}_rider", "(전체)")
    if not co:
        return pd.DataFrame()
    mask = df["insurer"] == co
    if prod:
        mask &= df["product_name"] == prod
    if rider and rider != "(전체)":
        mask &= df["contract_name"] == rider
    return df[mask]


def _filter(ctx: dict, side: str) -> pd.DataFrame:
    """comparison_rows 집약 DataFrame 반환."""
    detail = _apply_filter_mask(ctx, side)
    if detail.empty:
        return pd.DataFrame()
    rows = to_comparison_rows(detail.to_dict("records"))
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _filter_detail(ctx: dict, side: str) -> pd.DataFrame:
    """원본 detail_rows DataFrame 반환."""
    return _apply_filter_mask(ctx, side).copy()


def _product_status(co: str, prod: str) -> str:
    df = _df()
    if df.empty:
        return "UNKNOWN"
    sub = df[(df["insurer"] == co) & (df["product_name"] == prod)]
    if sub.empty:
        return "UNKNOWN"
    return sub["bundle_status"].iloc[0] if "bundle_status" in sub.columns else "SUMMARY_ONLY"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 업로드 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _run_upload(company: str, product: str, files: list) -> dict:
    result: dict = {
        "success": False, "rows": [],
        "warnings": [], "errors": [], "bundle_status": "UNKNOWN",
    }
    summary_path = terms_path = None
    try:
        for f in files:
            suffix = Path(f.name).suffix or ".pdf"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(f.read()); tmp.flush(); tmp.close()
            if _SUMMARY_NAME_RE.search(f.name) and not summary_path:
                summary_path = tmp.name
            elif _TERMS_NAME_RE.search(f.name) and not terms_path:
                terms_path = tmp.name
            elif not summary_path:
                summary_path = tmp.name

        bundle = DocumentBundle(
            company_name=company,
            product_name=product or company,
            summary_pdf=summary_path,
            terms_pdf=terms_path,
        )
        pr = run_pipeline(bundle)
        result.update({
            "success": len(pr.summary_rows) > 0,
            "rows": [r.model_dump() for r in pr.summary_rows],
            "warnings": pr.warnings,
            "errors": pr.errors,
            "bundle_status": bundle.status.value,
        })
        if result["rows"]:
            _store.save_upload(
                company, result["rows"],
                product_name=product,
                doc_type="summary",
                source_path=summary_path or terms_path,
            )
            new_df = pd.DataFrame(result["rows"])
            existing = _df()
            if existing.empty:
                st.session_state["wb_df"] = new_df
            else:
                if "dedupe_key" in existing.columns and "dedupe_key" in new_df.columns:
                    existing = existing[~existing["dedupe_key"].isin(new_df["dedupe_key"])]
                st.session_state["wb_df"] = pd.concat([existing, new_df], ignore_index=True)
    except Exception as exc:
        result["errors"].append(str(exc))
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 상단 단계 표시 바
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_step_indicator(current: str) -> None:
    steps = [
        ("setup",   "1", "상품 선택",  "기준 상품 선택"),
        ("compare", "2", "핵심 비교",  "특약 비교 분석"),
        ("report",  "3", "리포트",    "분석 결과 조회"),
    ]
    step_order = ["setup", "compare", "report"]
    cur_idx = step_order.index(current) if current in step_order else 0

    parts = []
    for i, (sid, num, label, sub) in enumerate(steps):
        s_idx = step_order.index(sid)
        if s_idx < cur_idx:
            cls = "step-item done"
            disp = "✓"
        elif s_idx == cur_idx:
            cls = "step-item active"
            disp = num
        else:
            cls = "step-item"
            disp = num
        parts.append(
            f'<div class="{cls}">'
            f'<div class="step-num">{disp}</div>'
            f'<div class="step-label">{label}</div>'
            f'<div class="step-sub">{sub}</div>'
            f'</div>'
        )
        if i < len(steps) - 1:
            line_cls = "step-line done" if s_idx < cur_idx else "step-line"
            parts.append(f'<div class="{line_cls}"></div>')

    st.markdown(
        f'<div class="step-indicator">{"".join(parts)}</div>',
        unsafe_allow_html=True,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 컨텍스트 바
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_context_bar(ctx: dict, our_n: int, comp_n: int) -> None:
    our_co    = ctx.get("our_co",    "—")
    our_prod  = ctx.get("our_prod",  "—")
    our_rider = ctx.get("our_rider", "전체")
    comp_co   = ctx.get("comp_co",   "—")
    comp_prod = ctx.get("comp_prod", "—")
    comp_rider= ctx.get("comp_rider","전체")

    our_status  = _product_status(our_co,  our_prod)
    comp_status = _product_status(comp_co, comp_prod)
    our_s_cls, our_s_lbl   = _STATUS_BADGE.get(our_status,  ("badge-unknown", our_status))
    comp_s_cls, comp_s_lbl = _STATUS_BADGE.get(comp_status, ("badge-unknown", comp_status))

    st.markdown(
        f'<div class="ctx-bar">'
        f'<div class="ctx-side ctx-side-our">'
        f'<div class="ctx-label ctx-label-our">당사</div>'
        f'<div class="ctx-company">{our_co}</div>'
        f'<div class="ctx-product">{our_prod}</div>'
        f'<div class="ctx-meta">특약: <b>{our_rider}</b> &nbsp;|&nbsp; '
        f'급부 <b class="text-our">{our_n}</b>건'
        f'&nbsp;<span class="badge {our_s_cls}">{our_s_lbl}</span></div></div>'
        f'<div class="ctx-vs">VS</div>'
        f'<div class="ctx-side ctx-side-comp">'
        f'<div class="ctx-label ctx-label-comp">타사</div>'
        f'<div class="ctx-company">{comp_co}</div>'
        f'<div class="ctx-product">{comp_prod}</div>'
        f'<div class="ctx-meta">특약: <b>{comp_rider}</b> &nbsp;|&nbsp; '
        f'급부 <b class="text-comp">{comp_n}</b>건'
        f'&nbsp;<span class="badge {comp_s_cls}">{comp_s_lbl}</span></div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 사이드바
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_sidebar() -> dict:
    ctx: dict = {}
    companies = _companies()

    with st.sidebar:
        logo_path = Path(__file__).resolve().parent.parent / "image.png"
        logo_b64 = ""
        if logo_path.exists():
            logo_b64 = base64.b64encode(logo_path.read_bytes()).decode()
        logo_img = (
            f'<img src="data:image/png;base64,{logo_b64}">'
            if logo_b64
            else '<div class="sidebar-brand-icon">LI</div>'
        )
        st.markdown(
            f'<div class="sidebar-brand">'
            f'{logo_img}'
            f'<div class="sidebar-brand-text">'
            f'<span class="sidebar-brand-name">라이나생명</span>'
            f'<span class="sidebar-brand-sub">인사이트 플랫폼</span>'
            f'</div></div>'
            f'<hr class="sidebar-teal-line">',
            unsafe_allow_html=True,
        )

        cur_step = st.session_state.get("wb_step", "setup")
        nav_items = [
            ("setup",   "대시보드"),
            ("compare", "상품 비교"),
            ("report",  "결과 조회"),
        ]
        nav_container = st.container()
        with nav_container:
            st.markdown('<div class="sidebar-nav-wrap">', unsafe_allow_html=True)
            for sid, label in nav_items:
                btn_type = "primary" if sid == cur_step else "secondary"
                if st.button(
                    label,
                    key=f"nav_{sid}",
                    type=btn_type,
                    use_container_width=True,
                ):
                    if sid != cur_step:
                        st.session_state["wb_step"] = sid
                        st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        _divider()

        st.markdown(
            '<div class="sidebar-nav-label">당사 상품</div>',
            unsafe_allow_html=True,
        )
        our_cos = [c for c in companies if c == _OUR_COMPANY] or companies[:1]
        our_co = st.selectbox("당사 회사", our_cos, key="ctx_our_co",
                              label_visibility="collapsed")
        our_prods = _products_of(our_co)
        our_prod = st.selectbox("당사 상품", our_prods, key="ctx_our_prod",
                                label_visibility="collapsed") if our_prods else ""
        our_riders = _riders_of(our_co, our_prod)
        our_rider = st.selectbox(
            "당사 특약", ["(전체)"] + our_riders,
            key="ctx_our_rider", label_visibility="collapsed",
        ) if our_riders else "(전체)"
        ctx.update({"our_co": our_co, "our_prod": our_prod, "our_rider": our_rider})

        _divider()

        st.markdown(
            '<div class="sidebar-nav-label">타사 상품</div>',
            unsafe_allow_html=True,
        )
        comp_cos = [c for c in companies if c != _OUR_COMPANY] or companies
        comp_co = st.selectbox("타사 회사", comp_cos, key="ctx_comp_co",
                               label_visibility="collapsed") if comp_cos else ""
        comp_prods = _products_of(comp_co) if comp_co else []
        comp_prod = st.selectbox("타사 상품", comp_prods, key="ctx_comp_prod",
                                 label_visibility="collapsed") if comp_prods else ""
        comp_riders = _riders_of(comp_co, comp_prod) if comp_prod else []
        comp_rider = st.selectbox(
            "타사 특약", ["(전체)"] + comp_riders,
            key="ctx_comp_rider", label_visibility="collapsed",
        ) if comp_riders else "(전체)"
        ctx.update({"comp_co": comp_co, "comp_prod": comp_prod, "comp_rider": comp_rider})

        _divider()

        # 새 회사 추가
        with st.expander("➕ 새 회사 추가", expanded=False):
            new_co   = st.text_input("회사명", placeholder="삼성생명", key="up_co")
            new_prod = st.text_input("상품명", placeholder="무배당 삼성암보험", key="up_prod")
            uploaded = st.file_uploader(
                "PDF 업로드", type=["pdf"],
                accept_multiple_files=True, key="up_files",
            )
            if uploaded:
                for f in uploaded:
                    typ = "● 요약서" if _SUMMARY_NAME_RE.search(f.name) else \
                          "○ 약관" if _TERMS_NAME_RE.search(f.name) else "◌ 불명"
                    st.caption(f"↗ {f.name}  {typ}")

            if st.button("파싱 시작", type="primary",
                         disabled=not (new_co and uploaded), key="up_btn"):
                with st.spinner(f"'{new_co}' 파싱 중..."):
                    res = _run_upload(new_co, new_prod, uploaded)
                st.session_state["wb_upload_log"].append(res)
                if res["success"]:
                    st.success(f"✓ {len(res['rows'])}건 추출")
                    st.rerun()
                else:
                    st.error("파싱 실패")
                for e in res["errors"]:
                    st.caption(f"오류: {e}")

    return ctx


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 1: 비교 설정 화면
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_setup_step(ctx: dict) -> None:
    """STEP 1: 설정 화면 — 대시보드 스타일 + 상품 미리보기 + 비교 시작."""

    our_co   = ctx.get("our_co", "")
    our_prod = ctx.get("our_prod", "")
    comp_co  = ctx.get("comp_co", "")
    comp_prod= ctx.get("comp_prod", "")

    df = _df()
    n_co   = df["insurer"].nunique() if not df.empty else 0
    n_prod = df["product_name"].nunique() if not df.empty else 0
    n_rows = len(df)

    st.markdown(
        f'<div class="dash-stats">'
        f'<div class="dash-stat-card">'
        f'<div><div class="dash-stat-label">등록 상품</div>'
        f'<div><span class="dash-stat-value">{n_prod}</span><span class="dash-stat-unit">개</span></div></div>'
        f'<div class="dash-stat-icon dash-stat-icon-teal"><span class="material-symbols-outlined">description</span></div></div>'
        f'<div class="dash-stat-card">'
        f'<div><div class="dash-stat-label">비교 분석</div>'
        f'<div><span class="dash-stat-value">{n_co}</span><span class="dash-stat-unit">건</span></div></div>'
        f'<div class="dash-stat-icon dash-stat-icon-green"><span class="material-symbols-outlined">compare_arrows</span></div></div>'
        f'<div class="dash-stat-card">'
        f'<div><div class="dash-stat-label">급부 데이터</div>'
        f'<div><span class="dash-stat-value">{n_rows}</span><span class="dash-stat-unit">건</span></div></div>'
        f'<div class="dash-stat-icon dash-stat-icon-amber"><span class="material-symbols-outlined">bar_chart</span></div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    has_both = bool(our_co and our_prod and comp_co and comp_prod)

    flow_steps = [
        "기준 상품 선택",
        "기준 상품 문서 업로드",
        "비교 상품 문서 업로드",
        "비교 항목 입력 및 AI 분석",
        "검토 후 RDB 저장",
    ]
    steps_html = "".join(
        f'<li><span class="step-dot step-dot-teal">{i+1}.</span> {s}</li>'
        for i, s in enumerate(flow_steps)
    )
    st.markdown(
        f'<div class="flow-card flow-card-our">'
        f'<div class="flow-card-hdr">'
        f'<div class="flow-card-icon flow-card-icon-teal"><span class="material-symbols-outlined">balance</span></div>'
        f'<div class="flow-card-title">상품 비교</div></div>'
        f'<div class="flow-card-desc">당사 상품 vs 타사 상품 비교 분석</div>'
        f'<ul class="flow-card-steps">{steps_html}</ul>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="setup-btn-wrap">', unsafe_allow_html=True)
    _, btn_col, _ = st.columns([3, 2, 3])
    with btn_col:
        if st.button(
            "비교 분석 시작 →",
            type="primary",
            disabled=not has_both,
            use_container_width=True,
            key="btn_start_compare",
        ):
            st.session_state["wb_step"] = "compare"
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    if not has_both:
        st.markdown(
            '<div class="setup-hint">사이드바에서 당사·타사 상품을 모두 선택해야 비교를 시작할 수 있습니다.</div>',
            unsafe_allow_html=True,
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 2 — 비교 테이블 유틸
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━



def _render_benefit_expander(row: dict, detail_df: Optional[pd.DataFrame], badge_cls: str, badge_lbl: str) -> None:
    """comparison_row 1건 상세 expander."""
    meta_cols = st.columns(4)
    fields = [
        ("대표 금액",  row.get("amount", "—"),              "var(--our-700)"),
        ("지급조건",   row.get("amount_condition", "—"),    "var(--gray-700)"),
        ("특약명",     row.get("contract_name", "—"),       "var(--gray-700)"),
        ("카테고리",   row.get("benefit_category_ko", "—"), "var(--gray-700)"),
    ]
    for col, (label, value, color) in zip(meta_cols, fields):
        with col:
            st.markdown(
                f'<div class="detail-meta">{label}</div>'
                f'<div class="detail-value">{value or "—"}</div>',
                unsafe_allow_html=True,
            )

    _divider()

    # 질병군 목록
    tv_raw = row.get("trigger_variants", "")
    if tv_raw:
        try:
            triggers = json.loads(tv_raw)
        except Exception:
            triggers = [tv_raw]
        st.markdown('<div class="section-label">질병군 목록</div>', unsafe_allow_html=True)
        items = "".join(f'<div class="gap-card-item">• {t}</div>' for t in triggers)
        st.markdown(items, unsafe_allow_html=True)
    elif row.get("trigger"):
        st.markdown(
            f'<div class="section-label">지급사유</div>'
            f'<div class="detail-meta">{row.get("trigger") or ""}</div>',
            unsafe_allow_html=True,
        )

    # 조건별 금액
    ad_raw = row.get("amount_detail", "")
    if ad_raw:
        try:
            amounts = json.loads(ad_raw)
        except Exception:
            amounts = []
        if amounts:
            st.markdown('<div class="section-label">조건별 금액 전체</div>', unsafe_allow_html=True)
            amt_df = pd.DataFrame(amounts)
            col_map = {
                "amount": "지급금액", "condition": "지급조건",
                "reduction_note": "감액조건", "trigger": "질병군",
            }
            amt_df = amt_df.rename(columns={k: v for k, v in col_map.items() if k in amt_df.columns})
            amt_df = amt_df.loc[:, (amt_df != "").any(axis=0)]
            st.dataframe(amt_df, use_container_width=True, hide_index=True)

    # 세부 행 원본
    if detail_df is not None and not detail_df.empty:
        bname   = row.get("benefit_name", "")
        insurer = row.get("insurer", "")
        mask = (detail_df["benefit_name"] == bname) & (detail_df["insurer"] == insurer)
        sub = detail_df[mask]
        if not sub.empty:
            st.markdown(f'<div class="section-label">세부 행 원본 ({len(sub)}건)</div>', unsafe_allow_html=True)
            show_cols = [c for c in [
                "amount", "amount_condition", "reduction_note",
                "trigger", "waiting_period", "coverage_limit", "notes_summary",
            ] if c in sub.columns]
            ko = {
                "amount": "지급금액", "amount_condition": "지급조건",
                "reduction_note": "감액조건", "trigger": "원문지급사유",
                "waiting_period": "대기기간", "coverage_limit": "지급한도",
                "notes_summary": "주석",
            }
            st.dataframe(
                sub[show_cols].rename(columns={k: v for k, v in ko.items() if k in show_cols}),
                use_container_width=True, hide_index=True,
            )



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 2 — 3-Table 비교 워크벤치
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _apply_slots_cache(rows: list[dict]) -> list[dict]:
    """session_state에 캐시된 슬롯을 rows에 적용."""
    cache: dict = st.session_state.get("wb_slots_cache", {})
    for row in rows:
        if row.get("slots") is None:
            key = f"{row.get('insurer', '')}|{row.get('benefit_name', '')}"
            cached = cache.get(key)
            if cached:
                row["slots"] = cached
    return rows


def _save_slots_cache(rows: list[dict]) -> None:
    """enriched rows의 슬롯을 session_state 캐시에 저장."""
    cache: dict = st.session_state.setdefault("wb_slots_cache", {})
    for row in rows:
        slots = row.get("slots")
        if slots:
            key = f"{row.get('insurer', '')}|{row.get('benefit_name', '')}"
            cache[key] = slots


def _gen_report(ctx, our_df, comp_df, our_detail, comp_detail):
    builder = SummaryReportBuilder()
    our_label_full = (
        f"{ctx.get('our_co','')} · {ctx.get('our_prod','')} · "
        f"{ctx.get('our_rider','전체')}"
    )
    comp_label_full = (
        f"{ctx.get('comp_co','')} · {ctx.get('comp_prod','')} · "
        f"{ctx.get('comp_rider','전체')}"
    )
    return builder.build(
        base_df=our_df,
        comp_df=comp_df,
        base_label=our_label_full,
        comp_label=comp_label_full,
        detail_base_df=our_detail if not our_detail.empty else None,
        detail_comp_df=comp_detail if not comp_detail.empty else None,
    )


def _render_compare_step(
    ctx: dict,
    our_df: pd.DataFrame,
    comp_df: pd.DataFrame,
    our_detail: pd.DataFrame,
    comp_detail: pd.DataFrame,
) -> None:
    our_label  = ctx.get("our_co",  "당사")
    comp_label = ctx.get("comp_co", "타사")

    our_rows = our_df.to_dict("records") if not our_df.empty else []
    comp_rows = comp_df.to_dict("records") if not comp_df.empty else []

    # ── 캐시된 슬롯 적용 ──
    our_rows = _apply_slots_cache(our_rows)
    comp_rows = _apply_slots_cache(comp_rows)

    # ── 스마트 enrichment: 매칭된 쌍만 LLM 슬롯 추출 ──
    from insurance_parser.comparison.normalize import match_benefits as _quick_match

    quick_match = _quick_match(our_rows, comp_rows)
    to_enrich: list[dict] = []
    seen_ids: set[int] = set()
    for pair in quick_match.pairs:
        if pair.match_type != "matched":
            continue
        for row in (pair.our_row, pair.comp_row):
            if row and id(row) not in seen_ids and row.get("slots") is None:
                seen_ids.add(id(row))
                to_enrich.append(row)

    if to_enrich:
        enrich_bar = st.progress(
            0, text=f"약관 문장 → 비교 슬롯 변환 중... ({len(to_enrich)}건)"
        )

        def _enrich_progress(done: int, total: int) -> None:
            enrich_bar.progress(
                done / total,
                text=f"슬롯 추출: {done}/{total}건",
            )

        try:
            enrich_rows(to_enrich, progress_callback=_enrich_progress)
            _save_slots_cache(to_enrich)
        except Exception:
            pass
        enrich_bar.empty()

    # ── 3-Layer 비교 엔진 호출 ──
    result: ComparisonResult = build_comparison(our_rows, comp_rows)

    # ── 조건상이 항목 LLM 분석 ──
    mixed = [cp for cp in result.pairs if cp.overall_advantage == "조건상이"]
    if mixed:
        resolve_mixed_pairs(mixed)
        rebuild_amount_table(result)
        s = result.summary
        s["diff"] = sum(
            1 for cp in result.pairs
            if cp.overall_advantage in ("당사우위", "타사우위", "금액상이", "조건상이")
        )

    st.session_state["wb_comparison_result"] = result

    s = result.summary

    # ── Sticky summary bar ──
    st.markdown(
        f'<div class="sticky-bar">'
        f'<div class="sticky-bar-item"><span class="sticky-bar-num" style="color:var(--teal-700)">{s["matched"]}</span><span class="sticky-bar-label">매칭 급부</span></div>'
        f'<div class="sticky-bar-sep"></div>'
        f'<div class="sticky-bar-item"><span class="sticky-bar-num" style="color:var(--teal-600)">{s["our_only"]}</span><span class="sticky-bar-label">당사 단독</span></div>'
        f'<div class="sticky-bar-sep"></div>'
        f'<div class="sticky-bar-item"><span class="sticky-bar-num" style="color:var(--comp-600)">{s["comp_only"]}</span><span class="sticky-bar-label">타사 단독</span></div>'
        f'<div class="sticky-bar-sep"></div>'
        f'<div class="sticky-bar-item"><span class="sticky-bar-num" style="color:var(--amber-600)">{s["diff"]}</span><span class="sticky-bar-label">조건 상이</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ─────────────────────────────────────────────
    #  Card 1: 보장 범위 비교 (coverage_summary 기반)
    # ─────────────────────────────────────────────
    st.markdown(
        '<div class="cat-card">'
        '<div class="cat-card-header">'
        '<span class="cat-card-label" style="background:var(--teal-50);color:var(--teal-700)">① 보장 범위 비교</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    thead1 = f'<tr><th>질병 분류</th><th class="col-our">{our_label}</th><th class="col-comp">{comp_label}</th><th class="col-status">상태</th></tr>'
    rows1 = []
    coverage = result.coverage_summary
    display_order = ["일반암", "고액암", "기타피부암", "갑상선암", "제자리암", "경계성종양",
                     "전이암", "유방암", "전립선암", "소액질병"]
    for disease in display_order:
        counts = coverage.get(disease)
        if not counts:
            continue
        our_n, comp_n = counts["our"], counts["comp"]
        b_brief = f'{our_n}건' if our_n else '<span class="text-muted">미보장</span>'
        c_brief = f'{comp_n}건' if comp_n else '<span class="text-muted">미보장</span>'
        if our_n and comp_n:
            badge = f'<span class="status-both-same">매칭 {counts["matched"]}건</span>'
        elif our_n:
            badge = '<span class="status-only-our">당사단독</span>'
        elif comp_n:
            badge = '<span class="status-only-comp">타사단독</span>'
        else:
            continue
        rows1.append(
            f'<tr><td class="row-label">{disease}</td>'
            f'<td class="col-our-cell">{b_brief}</td>'
            f'<td class="col-comp-cell">{c_brief}</td>'
            f'<td class="col-status">{badge}</td></tr>'
        )
    remaining = {d: c for d, c in coverage.items() if d not in display_order and d != "기타"}
    for disease, counts in sorted(remaining.items()):
        our_n, comp_n = counts["our"], counts["comp"]
        if not our_n and not comp_n:
            continue
        b_brief = f'{our_n}건' if our_n else '<span class="text-muted">—</span>'
        c_brief = f'{comp_n}건' if comp_n else '<span class="text-muted">—</span>'
        if our_n and comp_n:
            badge = f'<span class="status-both-same">매칭 {counts["matched"]}건</span>'
        elif our_n:
            badge = '<span class="status-only-our">당사단독</span>'
        else:
            badge = '<span class="status-only-comp">타사단독</span>'
        rows1.append(
            f'<tr><td class="row-label">{disease}</td>'
            f'<td class="col-our-cell">{b_brief}</td>'
            f'<td class="col-comp-cell">{c_brief}</td>'
            f'<td class="col-status">{badge}</td></tr>'
        )
    st.markdown(
        f'<table class="tbl"><colgroup><col style="width:35%"><col style="width:22%"><col style="width:22%"><col style="width:90px"></colgroup><thead>{thead1}</thead><tbody>{"".join(rows1)}</tbody></table>',
        unsafe_allow_html=True,
    )
    with st.expander("▸ 암 분류 기준 (KCD9 + 표준약관)", expanded=False):
        def_rows = "".join(
            f'<tr><td class="row-label">{d["cls"]}</td><td>{d["kcd"]}</td>'
            f'<td>{d["examples"]}</td><td class="text-muted">{d["note"]}</td></tr>'
            for d in _CANCER_CLASS_DEFS
        )
        st.markdown(
            '<table class="tbl"><colgroup><col style="width:20%"><col style="width:25%"><col style="width:30%"><col style="width:25%"></colgroup><thead><tr><th>분류</th><th>KCD</th>'
            f'<th>예시</th><th>특징</th></tr></thead><tbody>{def_rows}</tbody></table>',
            unsafe_allow_html=True,
        )
    st.markdown('</div>', unsafe_allow_html=True)

    # ─────────────────────────────────────────────
    #  Card 2: 지급 조건 비교 (슬롯 기반)
    # ─────────────────────────────────────────────
    st.markdown(
        '<div class="cat-card">'
        '<div class="cat-card-header">'
        '<span class="cat-card-label" style="background:var(--teal-50);color:var(--teal-700)">② 지급 조건 비교</span>'
        f'<span class="cat-card-count">{len(result.pairs)}건 매칭</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    if result.slot_table:
        seen_dims: set[str] = set()
        dim_labels: list[tuple[str, str]] = []
        for row in result.slot_table:
            dim = row["dimension"]
            if dim not in seen_dims:
                seen_dims.add(dim)
                dim_labels.append((dim, row["label"]))

        for dim, label in dim_labels:
            dim_rows = [r for r in result.slot_table if r["dimension"] == dim]
            if not dim_rows:
                continue
            st.markdown(f'<div class="rpt-sub">{label}</div>', unsafe_allow_html=True)
            thead2 = f'<tr><th>급부</th><th class="col-our">{our_label}</th><th class="col-comp">{comp_label}</th><th class="col-status">판정</th></tr>'
            rows2 = []
            for r in dim_rows:
                o_val = r["our_value"] or "—"
                c_val = r["comp_value"] or "—"
                adv = r["advantage"]
                diff_cls = ' row-diff' if adv in ("당사우위", "타사우위") else ''
                rows2.append(
                    f'<tr><td class="row-label cell-clamp">{r["benefit"]}</td>'
                    f'<td class="col-our-cell{diff_cls}"><div class="cell-clamp">{o_val}</div></td>'
                    f'<td class="col-comp-cell{diff_cls}"><div class="cell-clamp">{c_val}</div></td>'
                    f'<td class="col-status">{_status_html(adv)}</td></tr>'
                )
            st.markdown(
                f'<table class="tbl"><colgroup><col style="width:35%"><col style="width:22%"><col style="width:22%"><col style="width:90px"></colgroup><thead>{thead2}</thead><tbody>{"".join(rows2)}</tbody></table>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown('<div class="card-empty">지급 조건 데이터가 없습니다.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # ─────────────────────────────────────────────
    #  Card 3: 급부별 금액 비교 (canonical key 매칭)
    # ─────────────────────────────────────────────
    st.markdown(
        '<div class="cat-card">'
        '<div class="cat-card-header">'
        '<span class="cat-card-label" style="background:var(--teal-50);color:var(--teal-700)">③ 급부별 금액 비교</span>'
        f'<span class="cat-card-count">{len(result.amount_table)}건</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    thead3 = (
        f'<tr><th>당사 급부명</th><th>타사 급부명</th>'
        f'<th class="col-our">{our_label}</th><th class="col-comp">{comp_label}</th>'
        f'<th class="col-status">상태</th></tr>'
    )
    rows3 = []
    for row in result.amount_table:
        diff_cls = ' row-diff' if row["status"] in ("당사우위", "타사우위", "금액상이", "조건상이") else ''
        rat = row.get("rationale", "") if row.get("status") != "조건상이" else ""
        rat_html = f'<div class="rationale">{rat}</div>' if rat else ''
        our_amt = (row["our_amount"] or "—").replace("\n", "<br>")
        comp_amt = (row["comp_amount"] or "—").replace("\n", "<br>")
        rows3.append(
            f'<tr><td class="row-label cell-clamp">{row["our_name"]}</td>'
            f'<td class="row-label cell-clamp">{row["comp_name"]}</td>'
            f'<td class="text-our col-our-cell{diff_cls}" style="white-space:normal;line-height:1.6;font-size:12px">{our_amt}</td>'
            f'<td class="text-comp col-comp-cell{diff_cls}" style="white-space:normal;line-height:1.6;font-size:12px">{comp_amt}</td>'
            f'<td class="col-status">{_status_html(row["status"])}{rat_html}</td></tr>'
        )
    st.markdown(
        f'<table class="tbl"><colgroup><col style="width:20%"><col style="width:20%"><col style="width:17%"><col style="width:17%"><col style="width:26%"></colgroup><thead>{thead3}</thead><tbody>{"".join(rows3)}</tbody></table>',
        unsafe_allow_html=True,
    )
    st.markdown('</div>', unsafe_allow_html=True)

    # ── 세부 데이터 (접힘) ──
    with st.expander(f"▸ 세부 데이터 보기 ({len(result.amount_table)}건)", expanded=False):
        for cp in result.pairs:
            for row_src, detail_src, lbl in [
                (cp.our_row if hasattr(cp, 'our_row') else None, our_detail, our_label),
                (cp.comp_row if hasattr(cp, 'comp_row') else None, comp_detail, comp_label),
            ]:
                if row_src is None:
                    continue
                ad_raw = row_src.get("amount_detail", "")
                dcnt = int(row_src.get("detail_row_count", 1))
                if ad_raw or dcnt > 1:
                    bname = row_src.get("benefit_name", "")
                    st.markdown(
                        f'<div class="card-sub">{lbl} · {bname}</div>',
                        unsafe_allow_html=True,
                    )
                    _render_benefit_expander(row_src, detail_src, "", lbl)

    # ── Action bar ──
    st.markdown('<div class="spacer-3"></div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="action-bar">'
        '<div class="action-bar-text">'
        '비교 분석이 완료되었습니다. <b>리포트를 생성</b>하여 상세 보고서를 확인하실 수 있습니다.'
        '</div></div>',
        unsafe_allow_html=True,
    )
    act_c1, act_c2, _ = st.columns([2, 2, 5])
    with act_c1:
        if st.button("리포트 생성 →", type="primary", key="btn_gen_report",
                      use_container_width=True):
            with st.spinner("리포트 생성 중..."):
                report = _gen_report(ctx, our_df, comp_df, our_detail, comp_detail)
                st.session_state["wb_report"] = report
            st.session_state["wb_step"] = "report"
            st.rerun()
    with act_c2:
        if st.button("← 설정으로", key="btn_back_setup"):
            st.session_state["wb_step"] = "setup"
            st.rerun()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 3: 리포트 화면
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_report_step(ctx: dict) -> None:
    """STEP 3: 리포트 전용 페이지."""

    # 상단 내비 — 버튼 4개를 한 행에 모두 배치 (내비 2 + 다운로드 2 + 여백)
    b1, b2, b3, b4, _ = st.columns([2, 2, 2, 2, 4])
    with b1:
        if st.button("← 비교 화면으로", key="btn_back_compare"):
            st.session_state["wb_step"] = "compare"
            st.rerun()
    with b2:
        if st.button("↺ 리포트 재생성", key="btn_regen_report"):
            st.session_state["wb_report"] = None
            st.session_state["wb_step"] = "compare"
            st.rerun()

    report = st.session_state.get("wb_report")
    if not report:
        st.info("리포트가 생성되지 않았습니다. 비교 화면에서 '리포트 생성' 버튼을 클릭해 주십시오.")
        return

    with b3:
        st.download_button(
            "⬇ Markdown",
            report.full_markdown.encode("utf-8"),
            "comparison_report.md", "text/markdown",
            key="dl_report_md",
        )
    with b4:
        st.download_button(
            "⬇ CSV",
            report.csv.encode("utf-8-sig"),
            "comparison_report.csv", "text/csv",
            key="dl_report_csv",
        )

    st.markdown('<div class="spacer-4"></div>', unsafe_allow_html=True)

    _render_report_body(report, ctx)


def _render_report_body(report, ctx: dict) -> None:
    """ComparisonReport를 M3 카드 기반 SaaS 스타일로 렌더링."""
    md = report.full_markdown
    if not md:
        st.warning("리포트 내용이 생성되지 않았습니다.")
        return

    _build_tooltip_map(report)
    ev_map = _build_ev_map(report)
    _render_rpt_hero(report, ctx)
    _render_rpt_comparison(report, ev_map)
    _render_rpt_analysis(report, ev_map)
    _render_rpt_evidence(report)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Report — M3 유틸 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_EV_TOOLTIP_MAP: dict[str, str] = {}


def _build_tooltip_map(report) -> None:
    """Evidence ID → tooltip text 맵을 빌드."""
    global _EV_TOOLTIP_MAP
    _EV_TOOLTIP_MAP = {}
    for ev in _evidence_from_report(report):
        label = f"{ev.side} · {ev.benefit}"
        text = ev.text.replace('"', '&quot;').replace("'", "&#39;").replace("\n", " ")
        if len(text) > 250:
            text = text[:250] + "…"
        _EV_TOOLTIP_MAP[ev.id] = f"{label}: {text}"


def _eid_html(eid_str: str) -> str:
    if not eid_str:
        return ""
    eid = eid_str.strip("[]")
    tooltip = _EV_TOOLTIP_MAP.get(eid, "")
    if tooltip:
        return f'<span class="eid" data-tooltip="{tooltip}">{eid_str}</span>'
    return f'<span class="eid">{eid_str}</span>'


def _cell_v(value: str, eid: str = "") -> str:
    v = value.strip() if value else "—"
    return f'<div class="cell-clamp">{v}{_eid_html(eid)}</div>'


def _evidence_from_report(report) -> list:
    return getattr(report, "evidences", []) or []


def _build_ev_map(report) -> dict[str, str]:
    ev_map: dict[str, str] = {}
    for ev in _evidence_from_report(report):
        key = f"{ev.side}|{ev.field}|{ev.benefit}"
        ev_map[key] = f"[{ev.id}]"
    return ev_map


def _find_eid(ev_map: dict, side: str, field: str, benefit: str) -> str:
    return ev_map.get(f"{side}|{field}|{benefit}", "")


def _rpt_table(thead: str, rows: list[str], colgroup: str = "") -> str:
    return (
        f'<table class="tbl">{colgroup}'
        f'<thead>{thead}</thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §1 전략적 요약 — Hero Card
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_rpt_hero(report, ctx: dict) -> None:
    cr: ComparisonResult | None = st.session_state.get("wb_comparison_result")
    if cr:
        s = cr.summary
        kps = [f"매칭 급부 {s['matched']}건, 전체 {s['total']}건"]
        if s["our_only"]:
            our_names = [cp.our_name for cp in cr.only_our[:3]]
            kps.append(f"당사 단독 보장 {s['our_only']}건: {', '.join(our_names)}")
        if s["comp_only"]:
            comp_names = [cp.comp_name for cp in cr.only_comp[:3]]
            kps.append(f"타사 단독 보장 {s['comp_only']}건: {', '.join(comp_names)}")
        if s["diff"]:
            kps.append(f"조건/금액 상이 {s['diff']}건")
    else:
        kps = [f"당사 {len(report.base_rows)}건 vs 타사 {len(report.comp_rows)}건"]

    kp_items = "".join(f'<div class="rpt-hero-kp-item">{kp}</div>' for kp in kps)

    st.markdown(
        f'<div class="rpt-hero">'
        f'<div class="rpt-hero-label">[Executive Summary] 전략적 요약</div>'
        f'<div class="rpt-hero-headline">'
        f'<b>한 줄 평</b>: 본 특약은 암보장개시일 이후 암으로 진단확정된 경우 보험금을 지급하는 '
        f'정액형 암보장 구조를 기반으로 합니다.'
        f'</div>'
        f'<div class="rpt-hero-sub">'
        f'<b>Key Selling Point</b>: 보험금 지급 여부는 약관에서 정의한 암 범위(KCD 기준)와 진단확정 시점, '
        f'보장개시일 이후 여부에 따라 결정됩니다.'
        f'</div>'
        f'<div class="rpt-hero-kp">'
        f'<div class="rpt-hero-kp-title">Key Points</div>'
        f'{kp_items}'
        f'</div></div>',
        unsafe_allow_html=True,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §2 핵심 비교 (3개 서브테이블을 하나의 카드로)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_rpt_comparison(report, ev_map: dict) -> None:
    our_l = report.base_label.split(" · ")[0] if report.base_label else "당사"
    comp_l = report.comp_label.split(" · ")[0] if report.comp_label else "타사"

    cr: ComparisonResult | None = st.session_state.get("wb_comparison_result")

    st.markdown(
        '<div class="card">'
        '<div class="card-hdr">'
        '<div class="card-num">2</div>'
        '<div class="card-title">[Comparison Matrix] 핵심 비교내용</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    if cr:
        # ── 보장 범위 (coverage_summary 기반) ──
        st.markdown('<div class="rpt-sub">보장 범위</div>', unsafe_allow_html=True)
        c_thead = f'<tr><th>질병 분류</th><th class="col-our">{our_l}</th><th class="col-comp">{comp_l}</th><th class="col-status">상태</th></tr>'
        c_rows = []
        for disease, counts in sorted(cr.coverage_summary.items()):
            if disease == "기타" or (not counts["our"] and not counts["comp"]):
                continue
            b_s = f'{counts["our"]}건' if counts["our"] else '<span class="text-muted">—</span>'
            c_s = f'{counts["comp"]}건' if counts["comp"] else '<span class="text-muted">—</span>'
            if counts["our"] and counts["comp"]:
                badge = f'<span class="status-both-same">매칭 {counts["matched"]}건</span>'
            elif counts["our"]:
                badge = '<span class="status-only-our">당사단독</span>'
            else:
                badge = '<span class="status-only-comp">타사단독</span>'
            c_rows.append(f'<tr><td class="row-label">{disease}</td><td class="col-our-cell">{b_s}</td><td class="col-comp-cell">{c_s}</td><td class="col-status">{badge}</td></tr>')
        cg4 = '<colgroup><col style="width:35%"><col style="width:22%"><col style="width:22%"><col style="width:90px"></colgroup>'
        st.markdown(_rpt_table(c_thead, c_rows, cg4), unsafe_allow_html=True)

        # ── 급부별 금액 (amount_table 기반) ──
        st.markdown('<div class="rpt-sub">급부별 금액</div>', unsafe_allow_html=True)
        a_thead = f'<tr><th>당사 급부</th><th>타사 급부</th><th class="col-our">{our_l}</th><th class="col-comp">{comp_l}</th><th class="col-status">상태</th></tr>'
        a_rows = []
        for row in cr.amount_table:
            diff_cls = ' row-diff' if row["status"] in ("당사우위", "타사우위", "금액상이", "조건상이") else ''
            rat = row.get("rationale", "") if row.get("status") != "조건상이" else ""
            rat_html = f'<div class="rationale">{rat}</div>' if rat else ''
            our_eid = _find_eid(ev_map, "당사", "amount", row["our_name"])
            comp_eid = _find_eid(ev_map, "타사", "amount", row["comp_name"])
            our_amt = (row["our_amount"] or "").replace("\n", "<br>")
            comp_amt = (row["comp_amount"] or "").replace("\n", "<br>")
            our_amt_html = _cell_v(our_amt, our_eid)
            comp_amt_html = _cell_v(comp_amt, comp_eid)
            a_rows.append(
                f'<tr><td class="row-label cell-clamp">{row["our_name"]}</td>'
                f'<td class="row-label cell-clamp">{row["comp_name"]}</td>'
                f'<td class="text-our col-our-cell{diff_cls}" style="white-space:normal;line-height:1.6;font-size:12px">{our_amt_html}</td>'
                f'<td class="text-comp col-comp-cell{diff_cls}" style="white-space:normal;line-height:1.6;font-size:12px">{comp_amt_html}</td>'
                f'<td class="col-status">{_status_html(row["status"])}{rat_html}</td></tr>'
            )
        cg5 = '<colgroup><col style="width:20%"><col style="width:20%"><col style="width:17%"><col style="width:17%"><col style="width:26%"></colgroup>'
        st.markdown(_rpt_table(a_thead, a_rows, cg5), unsafe_allow_html=True)
    else:
        st.markdown('<div class="card-empty">비교 데이터가 없습니다.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# §3 분석 및 제언 (합체)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_deep_dive_facts(cr: ComparisonResult, our_l: str, comp_l: str) -> list[dict]:
    """ComparisonResult에서 팩트 기반 서브섹션을 구성합니다.
    Returns list of { title, items: [str] } dicts."""
    sections: list[dict] = []

    # --- 1) 보장 범위 차이 ---
    coverage_facts: list[str] = []
    our_only_cats = [k for k, v in cr.coverage_summary.items() if k != "기타" and v.get("our") and not v.get("comp")]
    comp_only_cats = [k for k, v in cr.coverage_summary.items() if k != "기타" and v.get("comp") and not v.get("our")]
    both_cats = [k for k, v in cr.coverage_summary.items() if k != "기타" and v.get("our") and v.get("comp")]
    total_our = sum(v.get("our", 0) for v in cr.coverage_summary.values())
    total_comp = sum(v.get("comp", 0) for v in cr.coverage_summary.values())

    if total_our or total_comp:
        coverage_facts.append(
            f"전체 보장 급부 수: <b>{our_l} {total_our}건</b> vs <b>{comp_l} {total_comp}건</b>"
            f" (차이 <b>{abs(total_our - total_comp)}건</b>)"
        )
    if both_cats:
        matched_total = sum(cr.coverage_summary[k].get("matched", 0) for k in both_cats)
        coverage_facts.append(
            f"양사 공통 분류 <b>{len(both_cats)}개</b> 중 매칭 급부 <b>{matched_total}건</b>"
        )
    if our_only_cats:
        coverage_facts.append(
            f"당사 단독 보장 질병 분류: <b>{', '.join(our_only_cats)}</b>"
        )
    if comp_only_cats:
        coverage_facts.append(
            f"타사 단독 보장 질병 분류: <b>{', '.join(comp_only_cats)}</b>"
        )
    if coverage_facts:
        sections.append({"title": "보장 범위 차이 분석", "items": coverage_facts})

    # --- 2) 당사/타사 단독 보장 ---
    exclusive_facts: list[str] = []
    if cr.only_our:
        names = [cp.our_name for cp in cr.only_our[:5]]
        exclusive_facts.append(
            f"당사 단독 보장 <b>{len(cr.only_our)}건</b>: {', '.join(f'<b>{n}</b>' for n in names)}"
            + (f" 외 {len(cr.only_our) - 5}건" if len(cr.only_our) > 5 else "")
        )
    if cr.only_comp:
        names = [cp.comp_name for cp in cr.only_comp[:5]]
        exclusive_facts.append(
            f"타사 단독 보장 <b>{len(cr.only_comp)}건</b>: {', '.join(f'<b>{n}</b>' for n in names)}"
            + (f" 외 {len(cr.only_comp) - 5}건" if len(cr.only_comp) > 5 else "")
        )
    if exclusive_facts:
        sections.append({"title": "단독 보장 현황", "items": exclusive_facts})

    # --- 3) 조건/금액 차이 ---
    diff_pairs = [cp for cp in cr.pairs if cp.overall_advantage in ("당사우위", "타사우위", "금액상이", "조건상이")]
    our_adv = [cp for cp in diff_pairs if cp.overall_advantage == "당사우위"]
    comp_adv = [cp for cp in diff_pairs if cp.overall_advantage == "타사우위"]
    amount_diff = [cp for cp in diff_pairs if cp.overall_advantage == "금액상이"]
    cond_diff = [cp for cp in diff_pairs if cp.overall_advantage == "조건상이"]
    condition_facts: list[str] = []
    if diff_pairs:
        parts = [f"당사우위 <b>{len(our_adv)}건</b>", f"타사우위 <b>{len(comp_adv)}건</b>"]
        if amount_diff:
            parts.append(f"금액상이 <b>{len(amount_diff)}건</b>")
        if cond_diff:
            parts.append(f"조건상이 <b>{len(cond_diff)}건</b>")
        condition_facts.append(
            f"조건/금액 차이 항목 총 <b>{len(diff_pairs)}건</b> — {', '.join(parts)}"
        )
    for cp in our_adv[:3]:
        condition_facts.append(
            f"<b>{cp.our_name}</b>: {our_l} {cp.our_amount or '—'} vs {comp_l} {cp.comp_amount or '—'} → <b>당사우위</b>"
        )
    for cp in comp_adv[:3]:
        condition_facts.append(
            f"<b>{cp.our_name}</b>: {our_l} {cp.our_amount or '—'} vs {comp_l} {cp.comp_amount or '—'} → <b>타사우위</b>"
        )
    if condition_facts:
        sections.append({"title": "조건 및 금액 비교", "items": condition_facts})

    # --- 4) 슬롯 기반 세부 조건 차이 ---
    slot_facts: list[str] = []
    for row in cr.slot_table:
        if row.get("status") in ("당사우위", "타사우위"):
            slot_facts.append(
                f"<b>{row.get('benefit', '')} · {row.get('label', '')}</b>: "
                f"{our_l} \"{row.get('our', '—')}\" vs {comp_l} \"{row.get('comp', '—')}\" → <b>{row['status']}</b>"
            )
        if len(slot_facts) >= 6:
            break
    if slot_facts:
        sections.append({"title": "세부 지급 조건 차이", "items": slot_facts})

    return sections


def _render_rpt_analysis(report, ev_map: dict) -> None:
    our_l = report.base_label.split(" · ")[0] if report.base_label else "당사"
    comp_l = report.comp_label.split(" · ")[0] if report.comp_label else "타사"
    cr: ComparisonResult | None = st.session_state.get("wb_comparison_result")

    st.markdown(
        '<div class="card">'
        '<div class="card-hdr">'
        '<div class="card-num">3</div>'
        '<div class="card-title">[Deep Dive] 차별점 심층 분석</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    if cr:
        sections = _build_deep_dive_facts(cr, our_l, comp_l)
        if sections:
            for sec in sections:
                items_html = "".join(f'<li>{item}</li>' for item in sec["items"])
                st.markdown(
                    f'<div class="deep-section">'
                    f'<div class="deep-section-title">{sec["title"]}</div>'
                    f'<ul class="insight-list">{items_html}</ul>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.markdown('<div class="card-empty">양사 급부 및 조건이 모두 동일합니다. 차이 항목이 없습니다.</div>', unsafe_allow_html=True)

        diff_pairs = [cp for cp in cr.pairs if cp.overall_advantage in ("당사우위", "타사우위", "금액상이", "조건상이")]
        if diff_pairs:
            st.markdown('<div class="rpt-sub">조건/금액 상이 상세</div>', unsafe_allow_html=True)
            d_thead = f'<tr><th>급부</th><th class="col-our">{our_l}</th><th class="col-comp">{comp_l}</th><th class="col-status">판정</th></tr>'
            d_rows = []
            for cp in diff_pairs[:10]:
                rat = (cp.rationale or "") if cp.overall_advantage != "조건상이" else ""
                rat_html = f'<div class="rationale">{rat}</div>' if rat else ''
                our_eid = _find_eid(ev_map, "당사", "amount", cp.our_name)
                comp_eid = _find_eid(ev_map, "타사", "amount", cp.comp_name)
                d_rows.append(
                    f'<tr><td class="row-label cell-clamp">{cp.our_name}</td>'
                    f'<td class="text-our">{_cell_v(cp.our_amount or "", our_eid)}</td>'
                    f'<td class="text-comp">{_cell_v(cp.comp_amount or "", comp_eid)}</td>'
                    f'<td class="col-status">{_status_html(cp.overall_advantage)}{rat_html}</td></tr>'
                )
            cg4d = '<colgroup><col style="width:30%"><col style="width:20%"><col style="width:20%"><col style="width:30%"></colgroup>'
            st.markdown(_rpt_table(d_thead, d_rows, cg4d), unsafe_allow_html=True)
    else:
        st.markdown('<div class="card-empty">비교 데이터가 없습니다.</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Evidence 부록 — 클릭 시 원문 펼쳐지는 구조
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_rpt_evidence(report) -> None:
    evidences = _evidence_from_report(report)
    if not evidences:
        return

    st.markdown(
        f'<div class="card">'
        f'<div class="card-hdr">'
        f'<div class="card-num">4</div>'
        f'<div class="card-title">Evidence 부록</div>'
        f'<div class="card-badge">{len(evidences)}건</div>'
        f'</div>'
        f'<div class="detail-meta">'
        f'리포트 내 <span class="eid">[E1]</span> 형태의 태그에 마우스를 올리시면 약관 원문을 미리 볼 수 있습니다.'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    our_evs = [ev for ev in evidences if ev.side == "당사"]
    comp_evs = [ev for ev in evidences if ev.side != "당사"]

    def _ev_group(title: str, evs: list, side_cls: str):
        if not evs:
            return
        with st.expander(f"{title} ({len(evs)}건)", expanded=False):
            cards_html = []
            for ev in evs:
                side_style = (
                    "background:#f0fdfa;color:#0f766e;border:1px solid #99f6e4"
                    if side_cls == "ev-card-side-our"
                    else "background:#fef2f2;color:#b91c1c;border:1px solid #fecaca"
                )
                contract_html = (
                    f'<span style="font-size:11px;color:#9ca3af;margin-left:auto;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px">{ev.contract}</span>'
                    if ev.contract else ''
                )
                safe_text = ev.text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                cards_html.append(
                    f'<div style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:12px 14px;margin-bottom:10px">'
                    # 헤더
                    f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;flex-wrap:wrap">'
                    f'<span style="font-size:11px;font-weight:700;color:#0f766e;background:#f0fdfa;border:1px solid #99f6e4;padding:2px 8px;border-radius:20px">[{ev.id}]</span>'
                    f'<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;{side_style}">{ev.side}</span>'
                    f'<span style="font-size:13px;font-weight:600;color:#1f2937">{ev.benefit}</span>'
                    f'{contract_html}'
                    f'</div>'
                    # 필드 태그
                    f'<span style="display:inline-block;font-size:11px;color:#6b7280;background:#f3f4f6;padding:1px 8px;border-radius:20px;margin-bottom:8px">{ev.field}</span>'
                    # 원문
                    f'<div style="font-size:12px;color:#374151;line-height:1.75;white-space:pre-wrap;word-break:keep-all;background:#ffffff;border:1px solid #e5e7eb;border-radius:6px;padding:10px 12px">{safe_text}</div>'
                    f'</div>'
                )
            st.markdown("".join(cards_html), unsafe_allow_html=True)

    _ev_group("당사 Evidence", our_evs, "ev-card-side-our")
    _ev_group("타사 Evidence", comp_evs, "ev-card-side-comp")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 메인 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def render() -> None:
    _init()
    ctx = _render_sidebar()

    step = st.session_state.get("wb_step", "setup")

    step_labels = {"setup": "대시보드", "compare": "상품 비교", "report": "리포트"}
    step_descs  = {"setup": "라이나생명 보험 상품 분석 현황", "compare": "당사 상품 vs 타사 상품 비교 분석", "report": "특약 비교 분석 리포트"}
    st.markdown(
        f'<div class="app-title-bar"><div class="app-title">'
        f'<span class="app-title-main">{step_labels.get(step, "보험 특약 비교")}</span>'
        f'<span class="app-title-sub">{step_descs.get(step, "라이나생명 인사이트")}</span>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    _render_step_indicator(step)
    st.markdown('<div class="spacer-3"></div>', unsafe_allow_html=True)

    if step == "setup":
        _render_setup_step(ctx)
        return

    # STEP 2 / 3 공통: 데이터 로드
    our_df     = _filter(ctx, "our")
    comp_df    = _filter(ctx, "comp")
    our_detail = _filter_detail(ctx, "our")
    comp_detail= _filter_detail(ctx, "comp")

    # 컨텍스트 바
    _render_context_bar(ctx, len(our_df), len(comp_df))

    if step == "compare":
        if our_df.empty and comp_df.empty:
            st.info("선택한 상품의 데이터가 존재하지 않습니다. 대시보드에서 상품을 다시 설정해 주십시오.")
            if st.button("← 설정으로", key="btn_back_setup_empty"):
                st.session_state["wb_step"] = "setup"
                st.rerun()
        else:
            _render_compare_step(ctx, our_df, comp_df, our_detail, comp_detail)

    elif step == "report":
        _render_report_step(ctx)
