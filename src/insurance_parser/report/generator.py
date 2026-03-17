"""약관 Evidence 기반 암 특약 비교 리포트 빌더.

비교 화면의 공식 단위: comparison_rows (detail_rows는 Evidence 수집용)

리포트 목차 (고정 4섹션 + Evidence 부록):
  1. 전략적 요약        — 한줄평 + Key Point
  2. 핵심 비교내용      — 4개 서브섹션 (Evidence ID 포함)
      2-1. 보장 질병 범위 및 정의
      2-2. 보험금 지급 구조
      2-3. 지급 한도 및 제약 조건
      2-4. 부가 보장
  3. 차별점 심층 분석    — 구조적 차이 + Evidence 근거
  4. 특약 상품 개발 제언  — 경쟁력 평가·개선 포인트
  부록. Evidence 목록  — [E1], [E2], ... ID별 약관 원문 청크
"""
from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from insurance_parser.comparison.normalize import action_from_row, canonical_key

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Evidence 시스템
# ─────────────────────────────────────────────────────────

@dataclass
class Evidence:
    """약관 원문 근거 1건."""
    id: str           # "E1", "E2", ...
    side: str         # "당사" | "타사"
    field: str        # trigger | amount | reduction_note | waiting_period | ...
    benefit: str      # 급부명
    contract: str     # 특약명
    text: str         # 약관 원문 텍스트


class EvidenceCollector:
    """순차 Evidence ID를 부여하며 수집."""

    def __init__(self) -> None:
        self._items: list[Evidence] = []
        self._counter = 0

    def add(
        self,
        side: str,
        field: str,
        benefit: str,
        contract: str,
        text: str,
    ) -> str:
        """Evidence를 추가하고 ID 문자열 (예: "[E1]")을 반환."""
        if not text or not text.strip():
            return ""
        self._counter += 1
        eid = f"E{self._counter}"
        self._items.append(Evidence(
            id=eid, side=side, field=field,
            benefit=benefit, contract=contract, text=text.strip(),
        ))
        return f"[{eid}]"

    @property
    def items(self) -> list[Evidence]:
        return list(self._items)

    @property
    def count(self) -> int:
        return len(self._items)


# ─────────────────────────────────────────────────────────
# 암 분류 상수 + 헬퍼
# ─────────────────────────────────────────────────────────

_CANCER_HIGH = frozenset([
    "백혈병", "뇌암", "뇌종양", "뇌의 악성", "췌장암", "췌장의 악성",
    "골수암", "골수이식", "림프종", "림프암", "다발성골수종",
    "식도암", "담낭암", "담도암",
])
_CANCER_SMALL = frozenset([
    "유방암", "전립선암", "방광암", "자궁경부암",
    "기타피부암", "갑상선암", "대장점막내암",
])
_CANCER_SIMILAR = frozenset([
    "제자리암", "상피내암", "경계성종양", "경계성신생물",
    "양성종양", "비침습성", "점막내암",
])


def _classify_cancer(benefit_name: str) -> str:
    name = benefit_name or ""
    for kw in _CANCER_HIGH:
        if kw in name:
            return "고액암"
    for kw in _CANCER_SMALL:
        if kw in name:
            return "소액암"
    for kw in _CANCER_SIMILAR:
        if kw in name:
            return "유사암"
    if "암" in name or "악성신생물" in name or "악성종양" in name:
        return "일반암"
    return ""


# ─────────────────────────────────────────────────────────
# 보장 질병 범위 정의 테이블 (정적 참조 데이터)
# ─────────────────────────────────────────────────────────

_DISEASE_SCOPE_DEFS = [
    ("암 정의",    "한국표준질병·사인분류(KCD)에서 정한 악성신생물(C00-C97)에 해당하는 질병"),
    ("기타피부암",  "피부의 악성신생물(C44)"),
    ("갑상선암",   "갑상선의 악성신생물(C73)"),
    ("제자리암",   "상피내의 신생물(D00-D09)"),
    ("대장점막내암", "대장의 점막층에 국한된 상피내 신생물(D01)"),
]


# ─────────────────────────────────────────────────────────
# ComparisonReport (Evidence 기반)
# ─────────────────────────────────────────────────────────

@dataclass
class ComparisonReport:
    """약관 Evidence 기반 비교 리포트."""
    base_label: str
    comp_label: str
    base_rows: list[dict]
    comp_rows: list[dict]
    detail_base: list[dict] = field(default_factory=list)
    detail_comp: list[dict] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    insight: dict = field(default_factory=dict)
    _cached_markdown: str = field(default="", repr=False, compare=False)

    def _our_label(self) -> str:
        return self.base_label.split(" · ")[0] if self.base_label else "당사"

    def _comp_label(self) -> str:
        return self.comp_label.split(" · ")[0] if self.comp_label else "타사"

    # ── Evidence 수집 ────────────────────────────────────

    def _collect_evidence(self, collector: EvidenceCollector, row: dict, side: str) -> dict[str, str]:
        """한 행의 주요 필드에서 Evidence를 수집, {field_name: "[E1]"} 형태의 ID 반환."""
        bname = row.get("benefit_name", "")
        cname = row.get("contract_name", "")
        refs: dict[str, str] = {}

        for fld, txt_key in [
            ("trigger",         "trigger"),
            ("amount",          "amount"),
            ("amount_condition", "amount_condition"),
            ("reduction_note",  "reduction_note"),
            ("waiting_period",  "waiting_period"),
            ("coverage_limit",  "coverage_limit"),
            ("notes_summary",   "notes_summary"),
        ]:
            txt = row.get(txt_key, "") or ""
            if txt.strip():
                eid = collector.add(side, fld, bname, cname, txt)
                refs[fld] = eid
        return refs

    # ── 셀 값 포맷 (값 + Evidence ID) ────────────────────

    @staticmethod
    def _cell(value: str, eid: str = "") -> str:
        v = value.strip() if value else "—"
        if eid:
            return f"{v} {eid}"
        return v

    # ── §1 전략적 요약 ──────────────────────────────────

    def _section1_lines(self) -> list[str]:
        lines: list[str] = []
        our_l, comp_l = self._our_label(), self._comp_label()

        lines.append("# 암 특약 비교 리포트 (약관 Evidence 기반)\n")
        lines.append(f"**당사**: {self.base_label}  ")
        lines.append(f"**타사**: {self.comp_label}\n")
        lines.append("---\n")
        lines.append("## 1. 전략적 요약\n")

        ins = self.insight
        if ins:
            position  = ins.get("position", "")
            headline  = ins.get("headline", "")
            key_points = ins.get("key_points", [])
            top_gaps   = ins.get("top_gaps", [])

            # 포지션 레이블
            pos_map = {"우위": "▲ 당사 우위", "열위": "▼ 당사 열위", "혼재": "◎ 혼재", "단독보장중심": "— 단독 위주"}
            pos_label = pos_map.get(position, position)

            lines.append(f"**한줄평**: {pos_label} — {headline}\n")

            if top_gaps:
                best = top_gaps[0]
                side_l = our_l if best["side"] == "당사우위" else comp_l
                lines.append(
                    f"> 최대 격차: **{best['name']}** — {our_l} {best['our_amt']} vs "
                    f"{comp_l} {best['comp_amt']} ({best['gap_pct']:+d}% {side_l})\n"
                )

            lines.append("**Key Selling Points**\n")
            type_icon = {"strength": "✓", "weakness": "✗", "gap": "⊙", "condition": "ℹ"}
            for kp in key_points:
                icon = type_icon.get(kp.get("type", ""), "·")
                lines.append(f"- {icon} **{kp['label']}**: {kp['desc']}")
            lines.append("")
        else:
            # fallback: insight 없을 때
            base_map = {r.get("benefit_name", ""): r for r in self.base_rows}
            comp_map = {r.get("benefit_name", ""): r for r in self.comp_rows}
            only_our  = [n for n in base_map if n not in comp_map]
            only_comp = [n for n in comp_map if n not in base_map]
            lines.append(f"- {our_l} {len(self.base_rows)}건 vs {comp_l} {len(self.comp_rows)}건 급부")
            if only_our:
                lines.append(f"- 당사 단독 보장 {len(only_our)}건: {', '.join(only_our[:3])}")
            if only_comp:
                lines.append(f"- 타사 단독 보장 {len(only_comp)}건: {', '.join(only_comp[:3])}")
            lines.append("")
        return lines

    # ── §2 핵심 비교내용 ────────────────────────────────

    def _section2_lines(self, collector: EvidenceCollector) -> list[str]:
        lines: list[str] = []
        our_l, comp_l = self._our_label(), self._comp_label()

        base_map = {r.get("benefit_name", ""): r for r in self.base_rows}
        comp_map = {r.get("benefit_name", ""): r for r in self.comp_rows}

        # Evidence 수집 for all rows
        base_refs: dict[str, dict[str, str]] = {}
        comp_refs: dict[str, dict[str, str]] = {}
        for bname, row in base_map.items():
            base_refs[bname] = self._collect_evidence(collector, row, "당사")
        for bname, row in comp_map.items():
            comp_refs[bname] = self._collect_evidence(collector, row, "타사")

        lines.append("## 2. 핵심 비교내용\n")

        # ── (1) 보장 질병 범위 및 정의 ──
        lines.append("### (1) 보장 질병 범위 및 정의\n")
        lines.append(f"| 항목 | {our_l} | {comp_l} |")
        lines.append("|------|------|------|")

        diag_base = {n: r for n, r in base_map.items()
                     if action_from_row(r) == "진단"}
        diag_comp = {n: r for n, r in comp_map.items()
                     if action_from_row(r) == "진단"}

        for item_label, kcd_desc in _DISEASE_SCOPE_DEFS:
            b_matches = [n for n in diag_base if item_label.replace("암 ", "") in n or item_label in n]
            c_matches = [n for n in diag_comp if item_label.replace("암 ", "") in n or item_label in n]

            if b_matches:
                br = diag_base[b_matches[0]]
                b_text = br.get("trigger", "") or kcd_desc
                b_eid = base_refs.get(b_matches[0], {}).get("trigger", "")
                b_cell = self._cell(b_text[:60], b_eid)
            else:
                cancer_base = [n for n in diag_base if _classify_cancer(n) and item_label.replace("암 정의", "일반암") == _classify_cancer(n)]
                if cancer_base:
                    b_cell = self._cell(kcd_desc, base_refs.get(cancer_base[0], {}).get("trigger", ""))
                else:
                    b_cell = self._cell(kcd_desc)

            if c_matches:
                cr = diag_comp[c_matches[0]]
                c_text = cr.get("trigger", "") or kcd_desc
                c_eid = comp_refs.get(c_matches[0], {}).get("trigger", "")
                c_cell = self._cell(c_text[:60], c_eid)
            else:
                cancer_comp = [n for n in diag_comp if _classify_cancer(n) and item_label.replace("암 정의", "일반암") == _classify_cancer(n)]
                if cancer_comp:
                    c_cell = self._cell(kcd_desc, comp_refs.get(cancer_comp[0], {}).get("trigger", ""))
                else:
                    c_cell = self._cell(kcd_desc)

            lines.append(f"| {item_label} | {b_cell} | {c_cell} |")

        # 암 분류별 보장 현황
        lines.append("")
        lines.append("**암 분류별 보장 현황**\n")
        lines.append(f"| 암 분류 | {our_l} | {comp_l} | 비고 |")
        lines.append("|---------|------|------|------|")
        for cls in ["일반암", "고액암", "소액암", "유사암"]:
            b_items = [n for n in base_map if _classify_cancer(n) == cls]
            c_items = [n for n in comp_map if _classify_cancer(n) == cls]
            b_str = ", ".join(b_items[:2]) + ("…" if len(b_items) > 2 else "") if b_items else "—"
            c_str = ", ".join(c_items[:2]) + ("…" if len(c_items) > 2 else "") if c_items else "—"
            if b_items and c_items:
                note = "양사 보장"
            elif b_items:
                note = "**당사 단독**"
            elif c_items:
                note = "**타사 단독**"
            else:
                note = "미보장"
            lines.append(f"| {cls} | {b_str[:30]} | {c_str[:30]} | {note} |")
        lines.append("")

        # ── (2) 보험금 지급 구조 ──
        lines.append("### (2) 보험금 지급 구조\n")
        lines.append(f"| 항목 | {our_l} | {comp_l} |")
        lines.append("|------|------|------|")

        all_names = list(dict.fromkeys(
            list(base_map.keys()) + list(comp_map.keys())
        ))

        struct_rows = [
            ("보험금 지급 사유", "trigger"),
            ("지급 방식", "amount"),
            ("지급 조건", "amount_condition"),
            ("지급 횟수", "coverage_limit"),
        ]

        for row_label, fld in struct_rows:
            b_vals = list({r.get(fld, "") for r in self.base_rows if r.get(fld, "")} - {""})
            c_vals = list({r.get(fld, "") for r in self.comp_rows if r.get(fld, "")} - {""})

            b_text = b_vals[0][:55] if b_vals else "—"
            c_text = c_vals[0][:55] if c_vals else "—"

            b_eid_candidates = [base_refs.get(n, {}).get(fld, "") for n in base_map if base_refs.get(n, {}).get(fld)]
            c_eid_candidates = [comp_refs.get(n, {}).get(fld, "") for n in comp_map if comp_refs.get(n, {}).get(fld)]
            b_eid = b_eid_candidates[0] if b_eid_candidates else ""
            c_eid = c_eid_candidates[0] if c_eid_candidates else ""

            lines.append(f"| {row_label} | {self._cell(b_text, b_eid)} | {self._cell(c_text, c_eid)} |")

        # 치료/수술 급부별 상세
        treat_names = [n for n in all_names
                       if action_from_row(base_map.get(n) or comp_map.get(n) or {})
                       in ("치료", "수술", "항암약물", "표적항암약물", "항암방사선",
                           "특정면역항암약물", "카티항암약물", "특정항암호르몬약물",
                           "항암세기조절방사선", "항암양성자방사선", "항암중입자방사선",
                           "항암복합", "로봇수술", "관혈수술", "내시경수술",
                           "복강경흉강경수술", "재건수술", "절제수술", "적출수술",
                           "피부재건수술", "림프부종수술")]
        if treat_names:
            lines.append("")
            lines.append("**치료·수술 급부별 상세**\n")
            lines.append(f"| 항목 | {our_l} | {comp_l} |")
            lines.append("|------|------|------|")
            for bname in treat_names:
                br = base_map.get(bname)
                cr = comp_map.get(bname)
                b_amt = (br.get("amount", "") or "—") if br else "없음"
                c_amt = (cr.get("amount", "") or "—") if cr else "없음"
                b_eid = base_refs.get(bname, {}).get("amount", "")
                c_eid = comp_refs.get(bname, {}).get("amount", "")
                lines.append(f"| {bname[:28]} | {self._cell(b_amt[:20], b_eid)} | {self._cell(c_amt[:20], c_eid)} |")
        lines.append("")

        # ── (3) 지급 한도 및 제약조건 ──
        lines.append("### (3) 지급 한도 및 제약조건\n")
        lines.append(f"| 항목 | {our_l} | {comp_l} |")
        lines.append("|------|------|------|")

        limit_fields = [
            ("보장개시일", "waiting_period"),
            ("감액기간", "reduction_note"),
            ("지급 한도", "coverage_limit"),
        ]

        for row_label, fld in limit_fields:
            b_vals = list({r.get(fld, "") for r in self.base_rows if r.get(fld, "")} - {""})
            c_vals = list({r.get(fld, "") for r in self.comp_rows if r.get(fld, "")} - {""})

            b_text = b_vals[0][:50] if b_vals else "—"
            c_text = c_vals[0][:50] if c_vals else "—"

            b_eid_candidates = [base_refs.get(n, {}).get(fld, "") for n in base_map if base_refs.get(n, {}).get(fld)]
            c_eid_candidates = [comp_refs.get(n, {}).get(fld, "") for n in comp_map if comp_refs.get(n, {}).get(fld)]
            b_eid = b_eid_candidates[0] if b_eid_candidates else ""
            c_eid = c_eid_candidates[0] if c_eid_candidates else ""

            lines.append(f"| {row_label} | {self._cell(b_text, b_eid)} | {self._cell(c_text, c_eid)} |")
        lines.append("")

        # ── (4) 부가보장 ──
        lines.append("### (4) 부가보장\n")
        extra_actions = {"입원", "통원", "요양병원입원", "생활자금", "사망", "장해", "재진단",
                         "중환자실", "재활치료", "다학제진료", "영양치료", "통증완화", "항구토제",
                         "검사", "주요검사", "기타검사", "PET검사", "PSMAPET검사",
                         "NGS유전자패널검사", "MRI촬영검사", "바늘생검"}
        extra_names = [n for n in all_names
                       if action_from_row(base_map.get(n) or comp_map.get(n) or {}) in extra_actions]

        if extra_names:
            lines.append(f"| 항목 | {our_l} | {comp_l} |")
            lines.append("|------|------|------|")
            for bname in extra_names:
                br = base_map.get(bname)
                cr = comp_map.get(bname)
                b_amt = (br.get("amount", "") or "있음") if br else "없음"
                c_amt = (cr.get("amount", "") or "있음") if cr else "없음"
                b_eid = base_refs.get(bname, {}).get("trigger", "") or base_refs.get(bname, {}).get("amount", "")
                c_eid = comp_refs.get(bname, {}).get("trigger", "") or comp_refs.get(bname, {}).get("amount", "")
                lines.append(f"| {bname[:28]} | {self._cell(b_amt[:20], b_eid)} | {self._cell(c_amt[:20], c_eid)} |")
        else:
            lines.append("(부가보장 해당 없음)")
        lines.append("")

        return lines

    # ── §3 차별점 심층분석 ──────────────────────────────

    def _section3_lines(self) -> list[str]:
        lines: list[str] = []
        our_l, comp_l = self._our_label(), self._comp_label()
        base_map = {r.get("benefit_name", ""): r for r in self.base_rows}
        comp_map = {r.get("benefit_name", ""): r for r in self.comp_rows}

        only_our = [r for r in self.base_rows if r.get("benefit_name", "") not in comp_map]
        only_comp = [r for r in self.comp_rows if r.get("benefit_name", "") not in base_map]

        lines.append("## 3. 차별점 심층분석\n")

        lines.append(
            "* 암보험 특약은 대부분 **암보장개시일 이후 진단확정 시 보험금을 지급하고 "
            "최초 1회 지급 구조를 채택**한다."
        )
        lines.append(
            "* 약관 구조는 유사하지만 **암 분류 기준(유사암, 소액암 등)과 "
            "특정 암 보장 범위에서 상품 간 차이가 발생할 수 있다.**"
        )
        lines.append(
            "* 특히 **갑상선암, 제자리암, 대장점막내암 분류 기준**이 "
            "상품 경쟁력에 영향을 미친다."
        )
        lines.append("")

        # 양사 보장 구조 차이
        if only_our:
            lines.append(f"### 당사 단독 보장 ({len(only_our)}건)\n")
            for r in only_our[:8]:
                cancer = _classify_cancer(r.get("benefit_name", ""))
                tag = f" [{cancer}]" if cancer else ""
                act = action_from_row(r)
                lines.append(
                    f"- **{r.get('benefit_name', '')}{tag}** "
                    f"({act}) — {r.get('amount', '')}"
                )
            lines.append("")

        if only_comp:
            lines.append(f"### 타사 단독 보장 ({len(only_comp)}건)\n")
            for r in only_comp[:8]:
                cancer = _classify_cancer(r.get("benefit_name", ""))
                tag = f" [{cancer}]" if cancer else ""
                act = action_from_row(r)
                lines.append(
                    f"- **{r.get('benefit_name', '')}{tag}** "
                    f"({act}) — {r.get('amount', '')}"
                )
            lines.append("")

        # 금액 상이
        diff_pairs = [
            (n, base_map[n], comp_map[n])
            for n in base_map if n in comp_map
            and base_map[n].get("amount", "") != comp_map[n].get("amount", "")
        ]
        if diff_pairs:
            lines.append("### 금액 상이 항목\n")
            for bname, br, cr in diff_pairs[:8]:
                lines.append(
                    f"- **{bname}**: {our_l} {br.get('amount', '—')} "
                    f"vs {comp_l} {cr.get('amount', '—')}"
                )
            lines.append("")

        if not only_our and not only_comp and not diff_pairs:
            lines.append("양사 급부명 및 금액이 모두 동일합니다.\n")

        return lines

    # ── §4 특약 상품 개발 제언 ──────────────────────────

    def _section4_lines(self) -> list[str]:
        lines: list[str] = []
        base_map = {r.get("benefit_name", ""): r for r in self.base_rows}
        comp_map = {r.get("benefit_name", ""): r for r in self.comp_rows}
        only_comp = [r for r in self.comp_rows if r.get("benefit_name", "") not in base_map]

        lines.append("## 4. 특약 상품 개발 제언\n")

        lines.append(
            "* 특정 암 분류 기준을 명확히 하여 **보장 범위와 보험금 지급 구조를 "
            "직관적으로 제시할 필요가 있다.**"
        )
        lines.append(
            "* 치료 단계 중심 보장을 강화하는 **항암치료·재진단 특약 설계**가 "
            "상품 경쟁력 확보에 유효할 수 있다."
        )
        lines.append(
            "* 보장 질병 범위와 지급 조건을 명확히 표현하여 "
            "**상품 이해도와 비교 가능성을 높일 수 있다.**"
        )
        lines.append("")

        if only_comp:
            lines.append("### 타사 대비 미보장 영역 (도입 검토)\n")
            for r in only_comp[:5]:
                cancer = _classify_cancer(r.get("benefit_name", ""))
                tag = f" [{cancer}]" if cancer else ""
                lines.append(
                    f"- {r.get('benefit_name', '')}{tag} — "
                    f"타사 {r.get('amount', '')} 수준"
                )
            lines.append("")

        multi_trigger = [r for r in (self.base_rows + self.comp_rows) if r.get("trigger_variants")]
        if multi_trigger:
            lines.append("### 다중 질병군 분기 구조 활용\n")
            for r in multi_trigger[:4]:
                try:
                    tvs = json.loads(r["trigger_variants"])
                    lines.append(
                        f"- **{r.get('benefit_name', '')}** — {len(tvs)}개 질병군 분기: "
                        f"질병군별 진단금 차등 특약으로 설계 검토 가능"
                    )
                except Exception:
                    pass
            lines.append("")

        return lines

    # ── Evidence 부록 ───────────────────────────────────

    def _evidence_appendix_lines(self) -> list[str]:
        if not self.evidences:
            return []
        lines: list[str] = []
        lines.append("---\n")
        lines.append("## 부록: Evidence 목록\n")
        lines.append(f"총 {len(self.evidences)}건의 약관 근거가 수집되었습니다.\n")
        for ev in self.evidences:
            lines.append(f"**[{ev.id}]** ({ev.side} / {ev.benefit})")
            lines.append(f"> {ev.text[:200]}")
            lines.append("")
        return lines

    # ── full_markdown 속성 ──────────────────────────────

    @property
    def full_markdown(self) -> str:
        if self._cached_markdown:
            return self._cached_markdown

        collector = EvidenceCollector()

        all_lines: list[str] = []
        all_lines.extend(self._section1_lines())
        all_lines.append("---\n")
        all_lines.extend(self._section2_lines(collector))
        all_lines.append("---\n")
        all_lines.extend(self._section3_lines())
        all_lines.append("---\n")
        all_lines.extend(self._section4_lines())

        self.evidences = collector.items

        all_lines.extend(self._evidence_appendix_lines())

        self._cached_markdown = "\n".join(all_lines)
        return self._cached_markdown

    # ── CSV ─────────────────────────────────────────────

    @property
    def csv(self) -> str:
        rows = [
            {**r, "_side": "당사"} for r in self.base_rows
        ] + [
            {**r, "_side": "타사"} for r in self.comp_rows
        ]
        if not rows:
            return ""
        df = pd.DataFrame(rows)
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return buf.getvalue()


# ─────────────────────────────────────────────────────────
# SummaryReportBuilder
# ─────────────────────────────────────────────────────────

class SummaryReportBuilder:
    """comparison_rows DataFrame을 받아 ComparisonReport를 생성."""

    def build(
        self,
        base_df: pd.DataFrame,
        comp_df: pd.DataFrame,
        base_label: str = "당사",
        comp_label: str = "타사",
        detail_base_df: Optional[pd.DataFrame] = None,
        detail_comp_df: Optional[pd.DataFrame] = None,
    ) -> ComparisonReport:
        base_rows = base_df.to_dict("records") if not base_df.empty else []
        comp_rows = comp_df.to_dict("records") if not comp_df.empty else []
        detail_base = detail_base_df.to_dict("records") if detail_base_df is not None and not detail_base_df.empty else []
        detail_comp = detail_comp_df.to_dict("records") if detail_comp_df is not None and not detail_comp_df.empty else []

        log.info(
            "[SummaryReportBuilder] base=%d comparison_rows (%d detail), "
            "comp=%d comparison_rows (%d detail)",
            len(base_rows), len(detail_base), len(comp_rows), len(detail_comp),
        )
        return ComparisonReport(
            base_label=base_label,
            comp_label=comp_label,
            base_rows=base_rows,
            comp_rows=comp_rows,
            detail_base=detail_base,
            detail_comp=detail_comp,
        )
