"""InsureCompare — 보험 특약 비교 AI 솔루션."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

_LOGO_PATH = Path(__file__).parent / "image.png"

st.set_page_config(
    page_title="라이나 인사이트 - 보험 상품 분석 솔루션",
    page_icon=str(_LOGO_PATH),
    layout="wide",
    initial_sidebar_state="expanded",
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Design System
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20,300,0,0');

/* ══════════════════════════════════════════════
   A. DESIGN TOKENS  — Lina Insight Style
   ══════════════════════════════════════════════ */
:root {
  --fs-xs: 12px; --fs-sm: 13px; --fs-md: 14px; --fs-lg: 16px; --fs-xl: 20px;
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-5: 24px; --sp-6: 32px;
  --r-sm: 8px; --r-md: 12px; --r-lg: 16px; --r-full: 9999px;
  --shadow-1: 0 1px 3px rgba(0,0,0,.04), 0 1px 2px rgba(0,0,0,.02);
  --shadow-2: 0 4px 12px rgba(0,0,0,.06), 0 1px 3px rgba(0,0,0,.03);
  --shadow-3: 0 8px 24px rgba(0,0,0,.08), 0 2px 6px rgba(0,0,0,.04);

  --teal-700: #0F766E; --teal-600: #0D9488; --teal-500: #14B8A6;
  --teal-400: #2DD4BF; --teal-300: #5EEAD4; --teal-200: #99F6E4;
  --teal-100: #CCFBF1; --teal-50: #F0FDFA;
  --primary-700: #0F766E; --primary-600: #0D9488; --primary-500: #14B8A6;
  --primary-400: #2DD4BF; --primary-100: #CCFBF1; --primary-50: #F0FDFA;

  --our-700: #0F766E; --our-600: #0D9488; --our-200: #99F6E4;
  --our-100: #CCFBF1; --our-50: #F0FDFA;
  --comp-700: #B91C1C; --comp-600: #DC2626; --comp-200: #FECACA;
  --comp-100: #FEE2E2; --comp-50: #FEF2F2;

  --sidebar-bg: #111111; --sidebar-hover: #1a1a1a; --sidebar-active: var(--teal-500);
  --sidebar-text: rgba(255,255,255,.75); --sidebar-text-active: #fff;

  --g9: #111827; --g8: #1F2937; --g7: #374151; --g6: #4B5563;
  --g5: #6B7280; --g4: #9CA3AF; --g3: #D1D5DB; --g2: #E5E7EB;
  --g1: #F3F4F6; --g0: #F9FAFB; --w: #FFFFFF;

  --green-700: #047857; --green-600: #059669; --green-50: #ECFDF5;
  --amber-700: #B45309; --amber-600: #D97706; --amber-50: #FFFBEB;
  --violet-700: #6D28D9; --violet-600: #7C3AED; --violet-50: #EDE9FE;
}

/* ══════════════════════════════════════════════
   B. GLOBAL
   ══════════════════════════════════════════════ */
.stApp { background: var(--g0); font-family: 'Pretendard',-apple-system,BlinkMacSystemFont,sans-serif; -webkit-font-smoothing: antialiased; font-size: 14px; }
.material-symbols-outlined { font-family: 'Material Symbols Outlined'; font-weight: 300; font-style: normal; font-size: 20px; line-height: 1; display: inline-block; vertical-align: middle; -webkit-font-smoothing: antialiased; }
header[data-testid="stHeader"] { background: transparent !important; height: 0 !important; min-height: 0 !important; }
#MainMenu, footer, header [data-testid="stToolbar"] { display: none !important; }
div[data-testid="stDecoration"] { display: none !important; }
.block-container { padding: 1rem 2rem 2rem !important; max-width: 1200px !important; margin: 0 auto; }
h1 { font-size: 20px !important; font-weight: 700; color: var(--g9); letter-spacing: -.02em; margin: 0 !important; }
h2 { font-size: 16px !important; font-weight: 600; color: var(--g9); margin-bottom: var(--sp-1) !important; }
h3 { font-size: 14px !important; font-weight: 600; color: var(--g7); }
p, li, .stMarkdown p { color: var(--g6); line-height: 1.65; font-size: 14px; }

/* ══════════════════════════════════════════════
   C. SIDEBAR — dark Lina style (reference match)
   ══════════════════════════════════════════════ */
section[data-testid="stSidebar"] {
  background: var(--sidebar-bg) !important;
  border-right: none !important;
  min-width: 260px !important; max-width: 260px !important;
  width: 260px !important;
  transform: none !important;
  transition: none !important;
  margin-left: 0 !important;
  left: 0 !important;
  visibility: visible !important;
  display: flex !important;
}
section[data-testid="stSidebar"] > div:first-child {
  padding: 1rem var(--sp-4) var(--sp-5) !important;
  width: 100% !important;
  overflow-x: hidden !important;
}
section[data-testid="stSidebar"] > div:first-child > div:first-child {
  padding-top: 0 !important; margin-top: 0 !important;
}
section[data-testid="stSidebar"] > div:first-child > div:first-child > div:first-child {
  padding-top: 0 !important; margin-top: 0 !important;
}
section[data-testid="stSidebar"] > div:first-child > div:first-child > div:first-child > div:first-child {
  padding-top: 0 !important; margin-top: 0 !important;
}
section[data-testid="stSidebar"] header,
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] {
  display: none !important; height: 0 !important; padding: 0 !important; margin: 0 !important; min-height: 0 !important;
}
section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] { gap: var(--sp-3) !important; }
section[data-testid="stSidebar"] [data-testid="stSidebarNavCollapseButton"],
section[data-testid="stSidebar"] button[kind="headerNoPadding"] {
  display: none !important;
}
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"] {
  display: none !important;
}
section[data-testid="stSidebar"] * {
  color: var(--sidebar-text);
  overflow-wrap: break-word;
  word-break: keep-all;
}
section[data-testid="stSidebar"] .stMarkdown { overflow: visible !important; }
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stMultiSelect label {
  font-size: var(--fs-xs) !important;
  color: rgba(255,255,255,.35) !important;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
section[data-testid="stSidebar"] .stSelectbox > div > div,
section[data-testid="stSidebar"] .stMultiSelect > div > div {
  background: rgba(255,255,255,.08) !important;
  border-color: rgba(255,255,255,.10) !important;
  color: #fff !important;
  font-size: 13px !important;
  min-height: 38px !important;
  border-radius: var(--r-sm) !important;
}
section[data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] span {
  white-space: nowrap !important; overflow: hidden !important; text-overflow: ellipsis !important;
  max-width: 190px !important; display: inline-block !important;
  color: rgba(255,255,255,.9) !important;
}
section[data-testid="stSidebar"] .stSelectbox > div > div:hover,
section[data-testid="stSidebar"] .stMultiSelect > div > div:hover {
  border-color: var(--teal-400) !important;
  background: rgba(255,255,255,.12) !important;
}
section[data-testid="stSidebar"] .stTextInput input {
  background: rgba(255,255,255,.08) !important;
  border-color: rgba(255,255,255,.10) !important;
  color: #fff !important;
  font-size: 13px !important;
  border-radius: var(--r-sm) !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="primary"] {
  background: var(--teal-500) !important;
  color: #fff !important;
  border: none !important;
  font-size: 13px !important;
  border-radius: var(--r-sm) !important;
}
section[data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
  background: var(--teal-400) !important;
}
section[data-testid="stSidebar"] .stButton > button {
  background: rgba(255,255,255,.08) !important;
  border-color: rgba(255,255,255,.10) !important;
  color: rgba(255,255,255,.85) !important;
  font-size: 13px !important;
  border-radius: var(--r-sm) !important;
}
section[data-testid="stSidebar"] .stButton > button:hover {
  background: rgba(255,255,255,.14) !important;
  border-color: rgba(255,255,255,.18) !important;
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] {
  background: rgba(255,255,255,.06) !important;
  border-color: rgba(255,255,255,.08) !important;
  border-radius: var(--r-sm) !important;
}
section[data-testid="stSidebar"] div[data-testid="stExpander"] summary {
  color: rgba(255,255,255,.55) !important;
  font-size: var(--fs-sm) !important;
}
section[data-testid="stSidebar"] .stFileUploader label,
section[data-testid="stSidebar"] .stFileUploader section {
  border-color: rgba(255,255,255,.10) !important;
  background: rgba(255,255,255,.06) !important;
}
section[data-testid="stSidebar"] .stCaption, section[data-testid="stSidebar"] small {
  color: rgba(255,255,255,.35) !important;
  font-size: 10px !important;
}
section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,.06) !important; margin: var(--sp-2) 0 !important; }

/* ── Inputs / Buttons (main area) ── */
.stButton > button[kind="primary"] {
  background: var(--teal-500) !important; color: #fff !important; border: none !important;
  border-radius: var(--r-sm) !important; padding: var(--sp-2) var(--sp-5) !important; font-weight: 600 !important;
  font-size: var(--fs-md) !important; box-shadow: var(--shadow-1); transition: all .15s;
}
.stButton > button[kind="primary"]:hover {
  background: var(--teal-600) !important; box-shadow: var(--shadow-2);
}
.stButton > button {
  border-radius: var(--r-sm) !important; font-size: var(--fs-sm) !important;
  border: 1px solid var(--g2) !important; padding: var(--sp-2) var(--sp-4) !important;
  color: var(--g7) !important; background: var(--w) !important; transition: all .15s;
}
.stButton > button:hover { border-color: var(--g3) !important; background: var(--g0) !important; }

.stSelectbox > div > div, .stMultiSelect > div > div {
  border-radius: var(--r-sm) !important; border-color: var(--g2) !important;
  font-size: var(--fs-sm) !important; background: var(--w) !important;
}

.stDataFrame { font-size: var(--fs-sm); }
.stDataFrame [data-testid="stDataFrameResizable"] { border-radius: var(--r-md); overflow: hidden; border: 1px solid var(--g2) !important; }

/* ══════════════════════════════════════════════
   D. TABS / EXPANDER
   ══════════════════════════════════════════════ */
.stTabs [data-baseweb="tab-list"] { gap: 2px; background: var(--g1); border-radius: var(--r-md); padding: 3px; }
.stTabs [data-baseweb="tab"] { border-radius: var(--r-sm); padding: var(--sp-2) var(--sp-4); font-size: var(--fs-sm); font-weight: 500; color: var(--g5) !important; background: transparent !important; }
.stTabs [aria-selected="true"] { background: var(--w) !important; color: var(--g9) !important; box-shadow: var(--shadow-1) !important; font-weight: 600 !important; }

div[data-testid="stExpander"] { border: 1px solid var(--g2) !important; border-radius: var(--r-sm) !important; background: var(--w); overflow: hidden; margin-bottom: 0 !important; }
div[data-testid="stExpander"] summary { font-size: var(--fs-xs) !important; font-weight: 500; color: var(--g5) !important; padding: var(--sp-2) var(--sp-3) !important; }

div[data-testid="stDownloadButton"] > button { font-size: var(--fs-xs) !important; padding: var(--sp-2) var(--sp-4) !important; border: 1px solid var(--g2) !important; background: var(--w) !important; color: var(--g6) !important; border-radius: var(--r-sm) !important; }

/* ══════════════════════════════════════════════
   E. SPACING
   ══════════════════════════════════════════════ */
div[data-testid="stVerticalBlock"] { gap: var(--sp-2) !important; }
div[data-testid="stHorizontalBlock"] { gap: var(--sp-4) !important; }

/* ══════════════════════════════════════════════
   F. BADGES
   ══════════════════════════════════════════════ */
.badge { display: inline-block; padding: 3px var(--sp-3); border-radius: var(--r-full); font-size: var(--fs-xs); font-weight: 600; }
.badge-our     { background: var(--our-100);  color: var(--our-700); }
.badge-comp    { background: var(--comp-100); color: var(--comp-700); }
.badge-both    { background: #D1FAE5; color: var(--green-700); }
.badge-summary { background: var(--teal-100); color: var(--teal-700); }
.badge-terms   { background: #FEF3C7; color: var(--amber-700); }
.badge-unknown { background: var(--g2); color: var(--g6); }

.status-badge { display: inline-flex; align-items: center; padding: 2px var(--sp-2); border-radius: var(--r-full); font-size: var(--fs-xs); font-weight: 600; }
.status-both-same { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: var(--r-full); font-size: 10px; font-weight: 600; background: var(--g1); color: var(--g5); border: 1px solid var(--g2); white-space: nowrap; }
.status-both-diff { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: var(--r-full); font-size: 10px; font-weight: 600; background: #FEF3C7; color: var(--amber-700); border: 1px solid #FDE68A; white-space: nowrap; }
.status-only-our  { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: var(--r-full); font-size: 10px; font-weight: 600; background: var(--teal-100); color: var(--teal-700); border: 1px solid var(--teal-200); white-space: nowrap; }
.status-only-comp { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: var(--r-full); font-size: 10px; font-weight: 600; background: var(--comp-100); color: var(--comp-700); border: 1px solid var(--comp-200); white-space: nowrap; }

/* ══════════════════════════════════════════════
   G. STEP INDICATOR — horizontal stepper with lines
   ══════════════════════════════════════════════ */
.step-indicator {
  display: flex; align-items: flex-start; justify-content: center;
  background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-lg);
  padding: var(--sp-5) var(--sp-6); box-shadow: var(--shadow-1);
}
.step-item {
  display: flex; flex-direction: column; align-items: center;
  flex: 0 0 auto; min-width: 80px; position: relative;
}
.step-num {
  width: 36px; height: 36px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: var(--fs-md); font-weight: 700;
  background: var(--g1); color: var(--g4);
  border: 2px solid var(--g2);
  position: relative; z-index: 2; transition: all .2s;
}
.step-item.active .step-num {
  background: #fff; color: var(--teal-600);
  border-color: var(--teal-500); box-shadow: 0 0 0 3px rgba(20,184,166,.15);
}
.step-item.done .step-num {
  background: var(--teal-500); color: #fff;
  border-color: var(--teal-500);
}
.step-label {
  font-size: var(--fs-sm); font-weight: 600; color: var(--g4);
  margin-top: var(--sp-2); text-align: center; white-space: nowrap;
}
.step-item.active .step-label { color: var(--teal-700); font-weight: 700; }
.step-item.done .step-label { color: var(--teal-600); }
.step-sub {
  font-size: 11px; color: var(--g4); margin-top: 2px;
  text-align: center; white-space: nowrap;
}
.step-line {
  flex: 1; height: 2px; background: var(--g2);
  margin: 17px var(--sp-2) 0; min-width: 40px;
}
.step-line.done { background: var(--teal-400); }

/* ══════════════════════════════════════════════
   H. UNIFIED TABLE
   ══════════════════════════════════════════════ */
.tbl, .rpt-tbl, .ev-tbl, .cmp-tbl {
  width: 100%; border-collapse: separate; border-spacing: 0;
  font-size: 13px; border-radius: var(--r-md);
  overflow: hidden; border: 1px solid var(--g2); table-layout: fixed;
}
.tbl thead th, .rpt-tbl thead th, .ev-tbl th, .cmp-tbl th {
  padding: 8px 12px; text-align: left;
  font-size: 12px; font-weight: 600; color: var(--g5);
  background: var(--g0); border-bottom: 1px solid var(--g2);
  letter-spacing: .01em; white-space: nowrap;
}
.tbl thead th.col-our, .rpt-tbl thead th.col-our { color: var(--teal-700); background: rgba(204,251,241,.25); }
.tbl thead th.col-comp, .rpt-tbl thead th.col-comp { color: var(--comp-700); background: rgba(254,226,226,.25); }
.tbl tbody td, .rpt-tbl tbody td, .ev-tbl td, .cmp-tbl td {
  padding: 6px 12px; color: var(--g7);
  border-bottom: 1px solid var(--g1); vertical-align: middle;
  line-height: 1.45; font-size: 13px;
}
.tbl tbody tr:last-child td, .rpt-tbl tbody tr:last-child td,
.ev-tbl tr:last-child td, .cmp-tbl tr:last-child td { border-bottom: none; }
.tbl tbody tr:hover, .rpt-tbl tbody tr:hover { background: var(--g0); }
.tbl .row-label, .rpt-tbl .row-label { font-weight: 600; color: var(--g8); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tbl .col-our-cell { background: rgba(204,251,241,.06); }
.tbl .col-comp-cell { background: rgba(254,226,226,.04); }
.tbl .col-status, .rpt-tbl .col-status { min-width: 90px; text-align: center; white-space: normal; vertical-align: middle; }
.tbl td.row-diff { background: rgba(251,191,36,.04); }
.rationale { font-size: 11px; color: var(--g5); font-weight: 400; margin-top: 3px; line-height: 1.4; white-space: normal; word-break: keep-all; }

.cell-clamp { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; line-height: 1.5; }
.cell-clamp-3 { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; text-overflow: ellipsis; max-height: 4.4em; line-height: 1.45; }
.text-muted { color: var(--g4); }
.text-our { color: var(--teal-700); font-weight: 600; }
.text-comp { color: var(--comp-700); font-weight: 600; }

/* ══════════════════════════════════════════════
   I. EVIDENCE TAG
   ══════════════════════════════════════════════ */
.eid { display: inline; font-size: 10px; font-weight: 600; color: var(--teal-600); background: none; border: none; padding: 0 1px; margin-left: 1px; cursor: help; transition: all .15s; vertical-align: super; line-height: 1; position: relative; }
.eid:hover { color: var(--teal-800); text-decoration: underline; }
.eid[data-tooltip]:hover::after { content: attr(data-tooltip); position: absolute; left: 0; top: calc(100% + 6px); z-index: 9999; width: 340px; max-width: 90vw; padding: var(--sp-3) var(--sp-4); background: #111; color: #E0E7FF; font-size: var(--fs-xs); font-weight: 400; line-height: 1.55; border-radius: var(--r-sm); box-shadow: 0 8px 24px rgba(0,0,0,.35); white-space: normal; word-break: keep-all; pointer-events: none; }
.eid[data-tooltip]:hover::before { content: ''; position: absolute; left: 10px; top: calc(100% + 1px); z-index: 9999; border: 5px solid transparent; border-bottom-color: #111; pointer-events: none; }

/* ══════════════════════════════════════════════
   J. CARD SYSTEM
   ══════════════════════════════════════════════ */
.card { background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-lg); padding: var(--sp-5) var(--sp-5) var(--sp-4); margin-bottom: var(--sp-3); box-shadow: var(--shadow-1); }
.card-hdr { display: flex; align-items: center; gap: var(--sp-2); padding-bottom: var(--sp-3); margin-bottom: var(--sp-3); border-bottom: 1px solid var(--g1); }
.card-num { width: 24px; height: 24px; border-radius: 50%; background: var(--teal-500); color: #fff; font-size: 12px; font-weight: 700; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; }
.card-icon { width: 40px; height: 40px; border-radius: var(--r-sm); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.card-icon .material-symbols-outlined { font-size: 22px; }
.card-icon-blue   { background: var(--teal-100); color: var(--teal-700); }
.card-icon-violet { background: #EDE9FE; color: var(--violet-700); }
.card-icon-amber  { background: #FEF3C7; color: var(--amber-700); }
.card-icon-green  { background: #D1FAE5; color: var(--green-700); }
.card-icon-red    { background: var(--comp-100); color: var(--comp-700); }
.card-title { font-size: 15px; font-weight: 700; color: var(--g9); letter-spacing: -.01em; }
.card-badge { margin-left: auto; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: var(--r-full); background: var(--teal-50); color: var(--teal-700); }
.card-sub { font-size: 13px; font-weight: 600; color: var(--teal-700); margin: var(--sp-3) 0 var(--sp-2); padding-left: var(--sp-3); border-left: 3px solid var(--teal-400); }
.card-empty { padding: var(--sp-4); text-align: center; color: var(--g4); font-size: var(--fs-sm); }

.rpt-card { background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-lg); padding: var(--sp-4); margin-bottom: var(--sp-3); box-shadow: var(--shadow-1); }
.rpt-card-hdr { display: flex; align-items: center; gap: var(--sp-2); padding-bottom: var(--sp-3); margin-bottom: var(--sp-3); border-bottom: 1px solid var(--g1); }
.rpt-card-icon { width: 28px; height: 28px; border-radius: var(--r-sm); display: flex; align-items: center; justify-content: center; font-size: 14px; flex-shrink: 0; }
.rpt-card-title { font-size: 15px; font-weight: 700; color: var(--g9); letter-spacing: -.01em; }
.rpt-card-badge { margin-left: auto; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: var(--r-full); background: var(--g1); color: var(--g5); }
.rpt-sub { font-size: 13px; font-weight: 600; color: var(--teal-700); margin: var(--sp-3) 0 var(--sp-2); padding-left: var(--sp-3); border-left: 3px solid var(--teal-400); }
.ev-appendix { background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-lg); padding: var(--sp-5); margin-top: var(--sp-2); box-shadow: var(--shadow-1); }

/* ══════════════════════════════════════════════
   K. HERO (report strategic summary)
   ══════════════════════════════════════════════ */
.rpt-hero { background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-lg); padding: var(--sp-4) var(--sp-5); margin-bottom: var(--sp-3); box-shadow: var(--shadow-1); }
.rpt-hero-label { font-size: 14px; font-weight: 700; letter-spacing: -.01em; color: var(--g9); margin-bottom: var(--sp-2); display: flex; align-items: center; gap: var(--sp-2); }
.rpt-hero-label::before { content: '1'; display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 50%; background: var(--teal-500); color: #fff; font-size: 11px; font-weight: 700; flex-shrink: 0; }
.rpt-hero-headline { font-size: 13px; font-weight: 400; line-height: 1.6; color: var(--g7); margin-bottom: var(--sp-2); }
.rpt-hero-headline b { color: var(--teal-600); font-weight: 600; }
.rpt-hero-sub { font-size: 13px; font-weight: 400; line-height: 1.6; color: var(--g7); margin-bottom: var(--sp-3); }
.rpt-hero-sub b { color: var(--teal-600); font-weight: 600; }
.rpt-hero-kp { border-top: 1px solid var(--g1); padding-top: var(--sp-3); }
.rpt-hero-kp-title { font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--teal-600); margin-bottom: var(--sp-1); }
.rpt-hero-kp-item { font-size: 13px; color: var(--g7); padding: 3px 0 3px var(--sp-3); border-left: 2px solid var(--teal-400); margin-bottom: var(--sp-1); line-height: 1.5; }

/* ══════════════════════════════════════════════
   L. INSIGHT / GAP / DIFF
   ══════════════════════════════════════════════ */
.deep-section { margin-bottom: var(--sp-3); }
.deep-section-title {
  font-size: 13px; font-weight: 700; color: var(--teal-700);
  padding: 6px 12px; margin-bottom: var(--sp-1);
  background: var(--teal-50); border-left: 3px solid var(--teal-500);
  border-radius: 0 var(--r-sm) var(--r-sm) 0;
  display: flex; align-items: center; gap: 6px;
}
.insight-list { list-style: none; padding: 0; margin: var(--sp-1) 0 var(--sp-2); }
.insight-list li { font-size: 13px; color: var(--g7); line-height: 1.6; padding: 4px 0 4px var(--sp-4); margin-bottom: 2px; position: relative; }
.insight-list li::before { content: '·'; position: absolute; left: 8px; top: 4px; color: var(--teal-500); font-weight: 700; font-size: 16px; }
.insight-list li b { color: var(--teal-700); font-weight: 600; }
.gap-card { padding: var(--sp-3) var(--sp-4); border-radius: var(--r-md); margin-bottom: var(--sp-2); }
.gap-card-our  { background: var(--teal-50); border: 1px solid var(--teal-200); }
.gap-card-comp { background: var(--comp-50); border: 1px solid var(--comp-200); }
.gap-card-title { font-size: 12px; font-weight: 600; margin-bottom: var(--sp-2); letter-spacing: .02em; }
.gap-card-title-our  { color: var(--teal-700); }
.gap-card-title-comp { color: var(--comp-700); }
.gap-card-item { font-size: 13px; color: var(--g7); padding: 2px 0 2px var(--sp-3); border-left: 2px solid var(--g3); margin-bottom: var(--sp-1); line-height: 1.5; }
.gap-card-item b { color: var(--g9); font-weight: 600; }

/* ══════════════════════════════════════════════
   M. CONTEXT BAR
   ══════════════════════════════════════════════ */
.ctx-bar { display: flex; align-items: stretch; gap: var(--sp-3); background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-lg); padding: var(--sp-3) var(--sp-4); margin-bottom: var(--sp-3); box-shadow: var(--shadow-1); }
.ctx-side { flex: 1; background: var(--w); border-radius: var(--r-md); padding: var(--sp-3) var(--sp-4); }
.ctx-side-our  { border: 1.5px solid var(--teal-200); border-top: 3px solid var(--teal-500); }
.ctx-side-comp { border: 1.5px solid var(--comp-200); border-top: 3px solid var(--comp-600); }
.ctx-label { font-size: var(--fs-xs); font-weight: 700; letter-spacing: .1em; text-transform: uppercase; margin-bottom: var(--sp-1); }
.ctx-label-our  { color: var(--teal-600); }
.ctx-label-comp { color: var(--comp-600); }
.ctx-company { font-size: 16px; font-weight: 700; color: var(--g9); letter-spacing: -.01em; }
.ctx-product { font-size: var(--fs-sm); color: var(--g5); margin: 2px 0 var(--sp-2); }
.ctx-meta { font-size: var(--fs-sm); color: var(--g5); display: flex; align-items: center; gap: var(--sp-2); }
.ctx-meta b { color: var(--g7); }
.ctx-vs { flex: 0 0 44px; display: flex; align-items: center; justify-content: center; font-size: var(--fs-sm); font-weight: 700; color: var(--g3); }

/* ══════════════════════════════════════════════
   N. COMPARE STEP (category cards / chips)
   ══════════════════════════════════════════════ */
.section-label { font-size: var(--fs-sm); font-weight: 700; color: var(--g4); letter-spacing: .08em; text-transform: uppercase; margin: var(--sp-5) 0 var(--sp-3); padding-bottom: var(--sp-2); border-bottom: 1px solid var(--g1); }
.detail-meta { font-size: var(--fs-sm); color: var(--g5); margin-bottom: 2px; }
.detail-value { font-size: var(--fs-lg); font-weight: 700; color: var(--g9); }
.stat-card { background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-md); padding: var(--sp-4); text-align: center; box-shadow: var(--shadow-1); }
.kp-chip-advantage { background: var(--green-50); border: 1px solid #A7F3D0; border-radius: var(--r-md); padding: var(--sp-4); }
.kp-chip-gap     { background: var(--comp-50); border: 1px solid var(--comp-200); border-radius: var(--r-md); padding: var(--sp-4); }
.kp-chip-neutral { background: var(--g0); border: 1px solid var(--g2); border-radius: var(--r-md); padding: var(--sp-4); }
.kp-chip-title { font-size: var(--fs-xs); font-weight: 700; letter-spacing: .06em; text-transform: uppercase; margin-bottom: var(--sp-2); }
.kp-chip-item  { font-size: var(--fs-xs); color: var(--g7); padding: 2px 0; line-height: 1.5; }
.cat-card { background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-lg); padding: var(--sp-4) var(--sp-5); margin-bottom: var(--sp-3); box-shadow: var(--shadow-1); }
.cat-card-header { display: flex; align-items: center; gap: var(--sp-2); margin-bottom: var(--sp-3); padding-bottom: var(--sp-2); border-bottom: 1px solid var(--g1); }
.cat-card-label { font-size: 13px; font-weight: 600; letter-spacing: .01em; padding: 3px 10px; border-radius: var(--r-full); }
.cat-card-count { margin-left: auto; font-size: 11px; font-weight: 600; color: var(--g5); background: var(--g1); padding: 2px 8px; border-radius: var(--r-full); }
.cat-card-summary { font-size: var(--fs-sm); color: var(--g5); margin-bottom: var(--sp-2); line-height: 1.5; }

/* ══════════════════════════════════════════════
   O. SETUP SCREEN
   ══════════════════════════════════════════════ */
.setup-hero { text-align: center; padding: 48px var(--sp-5) 32px; }
.setup-hero-icon { font-size: 48px; margin-bottom: var(--sp-4); width: 80px; height: 80px; background: var(--teal-100); border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; }
.setup-hero-title { font-size: 22px; font-weight: 900; color: var(--g9); letter-spacing: -.04em; margin-bottom: var(--sp-2); }
.setup-hero-sub { font-size: var(--fs-md); color: var(--g4); line-height: 1.7; margin-bottom: var(--sp-5); }

.setup-preview-card { background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-lg); padding: var(--sp-5); text-align: left; box-shadow: var(--shadow-1); transition: all .2s; }
.setup-preview-card:hover { box-shadow: var(--shadow-2); }
.setup-preview-card-our  { border-color: var(--teal-200); border-top: 3px solid var(--teal-500); }
.setup-preview-card-comp { border-color: var(--comp-200); border-top: 3px solid var(--comp-600); }
.setup-preview-label { font-size: 9px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; margin-bottom: var(--sp-2); }
.setup-preview-label-our  { color: var(--teal-600); }
.setup-preview-label-comp { color: var(--comp-600); }
.setup-preview-company { font-size: 18px; font-weight: 900; letter-spacing: -.03em; margin-bottom: var(--sp-1); color: var(--g9); }
.setup-preview-product { font-size: var(--fs-sm); margin-bottom: var(--sp-1); color: var(--g7); }
.setup-preview-meta { margin: var(--sp-2) 0; font-size: var(--fs-xs); color: var(--g5); }
.setup-preview-empty { text-align: center; padding: var(--sp-6); font-size: var(--fs-sm); color: var(--g4); }
.setup-vs { display: flex; align-items: center; justify-content: center; height: 100%; font-size: 18px; font-weight: 900; color: var(--g3); }
.setup-btn-wrap { text-align: center; margin: var(--sp-6) 0 var(--sp-2); }
.setup-hint { text-align: center; font-size: var(--fs-xs); color: var(--g4); margin-top: var(--sp-2); }

/* ══════════════════════════════════════════════
   O2. SETUP FLOW CARDS (Lina-style numbered steps)
   ══════════════════════════════════════════════ */
.flow-card { background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-lg); padding: var(--sp-6) var(--sp-6) var(--sp-5); box-shadow: var(--shadow-1); transition: all .2s; }
.flow-card:hover { box-shadow: var(--shadow-2); }
.flow-card-our  { border-top: 4px solid var(--teal-500); }
.flow-card-hdr { display: flex; align-items: center; gap: var(--sp-3); margin-bottom: var(--sp-4); }
.flow-card-icon { width: 42px; height: 42px; border-radius: var(--r-sm); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.flow-card-icon .material-symbols-outlined { font-size: 24px; }
.flow-card-icon-teal { background: var(--teal-100); color: var(--teal-700); }
.flow-card-title { font-size: 16px; font-weight: 700; color: var(--g9); letter-spacing: -.01em; }
.flow-card-desc { font-size: var(--fs-md); color: var(--g5); margin-bottom: var(--sp-5); }
.flow-card-steps { list-style: none; padding: 0; margin: 0 0 var(--sp-5); background: var(--g0); border-radius: var(--r-md); padding: var(--sp-4) var(--sp-5); }
.flow-card-steps li { font-size: var(--fs-md); color: var(--g7); padding: 8px 0; display: flex; align-items: center; gap: var(--sp-3); border-bottom: 1px solid var(--g1); }
.flow-card-steps li:last-child { border-bottom: none; }
.flow-card-steps li .step-dot { width: 24px; height: 24px; border-radius: 50%; font-size: var(--fs-xs); font-weight: 600; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.flow-card-steps li .step-dot-teal { background: var(--teal-500); color: #fff; }

/* ══════════════════════════════════════════════
   P. APP TITLE BAR (matches Lina header)
   ══════════════════════════════════════════════ */
.app-title-bar { display: flex; align-items: center; justify-content: space-between; padding: var(--sp-4) 0; border-bottom: 1px solid var(--g2); margin-bottom: var(--sp-4); }
.app-title { line-height: 1.4; }
.app-title-main { font-size: 20px; font-weight: 700; color: var(--g9); letter-spacing: -.02em; display: block; }
.app-title-sub  { font-size: 14px; color: var(--g5); display: block; margin-top: 2px; font-weight: 400; }

/* sidebar brand — matches Lina logo area */
.sidebar-brand { padding: var(--sp-4) var(--sp-4) var(--sp-3); display: flex; align-items: center; gap: var(--sp-3); margin-top: 0 !important; }
.sidebar-brand img { width: 40px; height: 40px; border-radius: 8px; object-fit: cover; flex-shrink: 0; }
.sidebar-brand-icon { width: 40px; height: 40px; background: var(--teal-500); border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 14px; font-weight: 900; color: #fff; flex-shrink: 0; }
.sidebar-brand-text { display: flex; flex-direction: column; min-width: 0; }
.sidebar-brand-name { font-size: 16px; font-weight: 700; color: #fff !important; letter-spacing: -.01em; white-space: nowrap; }
.sidebar-brand-sub { font-size: 11px; color: #E5A51B !important; white-space: nowrap; margin-top: 2px; font-weight: 500; letter-spacing: .02em; }
.sidebar-teal-line { height: 1px; background: var(--teal-500); margin: var(--sp-3) 0 var(--sp-4); border: none; opacity: .6; }

.sidebar-nav-label { font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: rgba(255,255,255,.32) !important; padding: var(--sp-3) var(--sp-1) var(--sp-2); margin-top: var(--sp-2); }

.spacer-4 { height: var(--sp-4); }
.spacer-2 { height: var(--sp-2); }
.spacer-3 { height: var(--sp-3); }
.spacer-5 { height: var(--sp-5); }
.divider { border: none; border-top: 1px solid var(--g1); margin: var(--sp-2) 0; }
section[data-testid="stSidebar"] .divider { border-top-color: rgba(255,255,255,.08) !important; margin: var(--sp-2) 0 !important; }

/* Sidebar stat summary */
.sidebar-stats { font-size: 12px; color: rgba(255,255,255,.45) !important; padding: var(--sp-2) var(--sp-1); line-height: 1.7; }

/* Sidebar nav buttons — real clickable Streamlit buttons styled as nav */
.sidebar-nav-wrap { margin: 0; }
.sidebar-nav-wrap .stButton { margin-bottom: 4px !important; }
.sidebar-nav-wrap .stButton > button {
  background: transparent !important;
  border: none !important;
  color: rgba(255,255,255,.55) !important;
  font-size: 14px !important;
  font-weight: 400 !important;
  padding: 10px var(--sp-4) !important;
  border-radius: var(--r-sm) !important;
  text-align: left !important;
  justify-content: flex-start !important;
  gap: 10px !important;
  width: 100% !important;
  transition: all .15s !important;
  letter-spacing: -.01em !important;
}
.sidebar-nav-wrap .stButton > button::before {
  font-family: 'Material Symbols Outlined' !important;
  font-size: 20px !important;
  font-weight: 300 !important;
  line-height: 1 !important;
  -webkit-font-smoothing: antialiased;
}
.sidebar-nav-wrap .stButton:nth-of-type(1) > button::before { content: 'dashboard' !important; }
.sidebar-nav-wrap .stButton:nth-of-type(2) > button::before { content: 'balance' !important; }
.sidebar-nav-wrap .stButton:nth-of-type(3) > button::before { content: 'description' !important; }
.sidebar-nav-wrap .stButton > button:hover {
  background: rgba(255,255,255,.06) !important;
  border: none !important;
  color: rgba(255,255,255,.9) !important;
}
.sidebar-nav-wrap .stButton > button[kind="primary"] {
  background: var(--teal-500) !important;
  color: #fff !important;
  font-weight: 500 !important;
  border: none !important;
  border-radius: var(--r-sm) !important;
  box-shadow: 0 2px 8px rgba(20,184,166,.18) !important;
}
.sidebar-nav-wrap .stButton > button[kind="primary"]:hover {
  background: var(--teal-400) !important;
}

/* ══════════════════════════════════════════════
   Q. STICKY SUMMARY BAR
   ══════════════════════════════════════════════ */
.sticky-bar { position: sticky; top: 0; z-index: 100; background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-md); padding: var(--sp-3) var(--sp-5); margin-bottom: var(--sp-3); box-shadow: var(--shadow-2); display: flex; align-items: center; gap: var(--sp-5); }
.sticky-bar-item { display: flex; flex-direction: column; align-items: center; gap: 1px; min-width: 56px; }
.sticky-bar-num { font-size: 20px; font-weight: 700; line-height: 1.1; letter-spacing: -.02em; }
.sticky-bar-label { font-size: var(--fs-xs); font-weight: 600; color: var(--g5); letter-spacing: .02em; white-space: nowrap; }
.sticky-bar-sep { width: 1px; height: 32px; background: var(--g2); flex-shrink: 0; }
.sticky-bar-action { margin-left: auto; }

/* ══════════════════════════════════════════════
   R. ACTION BAR
   ══════════════════════════════════════════════ */
.action-bar { background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-md); padding: var(--sp-3) var(--sp-5); margin-top: var(--sp-4); box-shadow: var(--shadow-2); display: flex; align-items: center; gap: var(--sp-4); }
.action-bar-text { font-size: var(--fs-md); color: var(--g5); flex: 1; }
.action-bar-text b { color: var(--g7); }

/* ══════════════════════════════════════════════
   S. SECONDARY SECTION EXPANDER OVERRIDE
   ══════════════════════════════════════════════ */
.secondary-section summary { font-size: var(--fs-xs) !important; color: var(--g4) !important; }

/* ══════════════════════════════════════════════
   T. DASHBOARD-STYLE STAT ROW (Lina top cards)
   ══════════════════════════════════════════════ */
.dash-stats { display: flex; gap: var(--sp-4); margin-bottom: var(--sp-3); }
.dash-stat-card { flex: 1; background: var(--w); border: 1px solid var(--g2); border-radius: var(--r-lg); padding: 20px var(--sp-5); display: flex; align-items: center; justify-content: space-between; box-shadow: var(--shadow-1); transition: all .2s; }
.dash-stat-card:hover { box-shadow: var(--shadow-2); }
.dash-stat-label { font-size: 14px; color: var(--g5); margin-bottom: 4px; font-weight: 400; }
.dash-stat-value { font-size: 28px; font-weight: 700; color: var(--g9); letter-spacing: -.03em; line-height: 1; }
.dash-stat-unit  { font-size: 14px; font-weight: 600; color: var(--g5); margin-left: 2px; }
.dash-stat-icon { width: 44px; height: 44px; border-radius: var(--r-md); display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.dash-stat-icon .material-symbols-outlined { font-size: 24px; }
.dash-stat-icon-teal  { background: var(--teal-100); color: var(--teal-600); }
.dash-stat-icon-amber { background: #FEF3C7; color: var(--amber-600); }
.dash-stat-icon-green { background: #D1FAE5; color: var(--green-600); }
.dash-stat-icon-red   { background: var(--comp-100); color: var(--comp-600); }
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Session Init
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
for _k, _v in [
    ("wb_df", None),
    ("wb_report", None),
    ("wb_upload_log", []),
    ("wb_step", "setup"),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Routing — workbench only
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from views.workbench import render  # noqa: E402
render()
