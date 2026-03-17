"""신규 보험사 급부 갭 탐지 스크립트.

사용법:
  python -m tools.check_gaps                    # 전체 prebuilt 데이터 분석
  python -m tools.check_gaps --insurer 삼성생명  # 특정 보험사만
  python -m tools.check_gaps --json             # JSON 출력 (파이프 활용)

출력:
  1. canonical_key가 raw 급부명 그대로인 항목 (synonyms에 매핑 안 된 것)
  2. benefit_category_ko = '기타'인 항목 (keywords.json 미매칭)
  3. 타사와 매칭 안 된 단독 급부 요약

신규 회사 추가 워크플로우:
  1. PDF 파싱 → prebuilt 저장
  2. python -m tools.check_gaps --insurer <회사명>
  3. 출력된 갭 목록 보고 config/synonyms_<회사명>.json 에 variants 추가
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from insurance_parser.comparison.normalize import canonical_key, normalize_text, invalidate_synonyms_cache, action_from_row
from insurance_parser.summary_pipeline.store import ArtifactStore


def _is_raw_key(benefit_name: str, ck: str) -> bool:
    """canonical_key가 정규화된 원문 그대로인 경우 → synonyms 미매칭."""
    norm = normalize_text(benefit_name)
    return ck == norm and "|" not in ck


def analyze(insurer_filter: str | None = None) -> dict:
    store = ArtifactStore()
    rows = store.load_all()

    if insurer_filter:
        rows = [r for r in rows if r.get("insurer", "") == insurer_filter]

    if not rows:
        return {"error": f"데이터 없음 (insurer={insurer_filter!r})"}

    # ── 1. synonyms 미매칭 (raw key) ──
    raw_gaps: list[dict] = []
    for r in rows:
        name = r.get("benefit_name", "")
        ck = canonical_key(name)
        if _is_raw_key(name, ck):
            raw_gaps.append({
                "insurer":      r.get("insurer", ""),
                "product":      r.get("product_name", ""),
                "benefit_name": name,
                "canonical_key": ck,
            })

    # ── 2. action 슬롯 미추출 (분류 불가) ──
    cat_gaps: list[dict] = []
    for r in rows:
        act = action_from_row(r)
        if not act:
            cat_gaps.append({
                "insurer":      r.get("insurer", ""),
                "benefit_name": r.get("benefit_name", ""),
                "canonical_key": canonical_key(r.get("benefit_name", "")),
            })

    # ── 3. 보험사별 단독 급부 수 (타사 매칭 없는 것) ──
    from collections import defaultdict
    key_by_insurer: dict[str, set] = defaultdict(set)
    for r in rows:
        insurer = r.get("insurer", "")
        ck = canonical_key(r.get("benefit_name", ""))
        key_by_insurer[insurer].add(ck)

    insurers = list(key_by_insurer)
    solo: dict[str, list[str]] = {}
    for ins in insurers:
        others = set()
        for other_ins, keys in key_by_insurer.items():
            if other_ins != ins:
                others |= keys
        solo[ins] = sorted(key_by_insurer[ins] - others)

    # ── unique 집계 ──
    raw_unique = list({r["benefit_name"]: r for r in raw_gaps}.values())
    cat_unique = list({r["benefit_name"]: r for r in cat_gaps}.values())

    return {
        "target_insurer": insurer_filter or "전체",
        "total_rows": len(rows),
        "synonyms_gap": {
            "count": len(raw_unique),
            "items": raw_unique,
        },
        "category_gap": {
            "count": len(cat_unique),
            "items": cat_unique,
        },
        "solo_keys": {ins: {"count": len(keys), "sample": keys[:5]} for ins, keys in solo.items()},
    }


def _print_report(result: dict) -> None:
    print(f"\n{'='*60}")
    print(f"  갭 분석 — {result['target_insurer']} ({result['total_rows']}건)")
    print(f"{'='*60}")

    # synonyms 갭
    sg = result["synonyms_gap"]
    print(f"\n[1] synonyms 미매칭 (canonical_key = 원문 그대로): {sg['count']}건")
    if sg["items"]:
        print("  → config/synonyms_<보험사명>.json 에 추가 필요")
        for item in sg["items"]:
            print(f"  [{item['insurer']}] {item['benefit_name']!r}")
            print(f"         canonical_key={item['canonical_key']!r}")
    else:
        print("  ✓ 없음")

    # action 갭
    cg = result["category_gap"]
    print(f"\n[2] action 슬롯 미추출 (synonyms 미매칭): {cg['count']}건")
    if cg["items"]:
        print("  → insurance_info/benefit_category_keywords.json 키워드 추가 필요")
        for item in cg["items"][:10]:
            print(f"  [{item['insurer']}] {item['benefit_name']!r}  →  {item['canonical_key']!r}")
        if len(cg["items"]) > 10:
            print(f"  ... 외 {len(cg['items'])-10}건")
    else:
        print("  ✓ 없음")

    # 단독 급부
    print(f"\n[3] 보험사별 단독 보장 키 (타사 미매칭)")
    for ins, info in result["solo_keys"].items():
        sample = ", ".join(info["sample"])
        print(f"  {ins}: {info['count']}개 — 예) {sample}")

    print(f"\n{'='*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="보험 급부 synonyms/category 갭 탐지")
    parser.add_argument("--insurer", "-i", default=None, help="보험사명 필터 (예: 삼성생명)")
    parser.add_argument("--json",    "-j", action="store_true", help="JSON 형식 출력")
    args = parser.parse_args()

    invalidate_synonyms_cache()   # 최신 파일 반영
    result = analyze(args.insurer)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if "error" in result:
            print(f"오류: {result['error']}", file=sys.stderr)
            sys.exit(1)
        _print_report(result)


if __name__ == "__main__":
    main()
