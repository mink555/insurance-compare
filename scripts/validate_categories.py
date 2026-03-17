"""카테고리 분류 검증 스크립트.

사용법:
    python scripts/validate_categories.py                          # artifacts/prebuilt_riders.json 검증
    python scripts/validate_categories.py path/to/rows.json        # 특정 파일 검증
    python scripts/validate_categories.py --threshold 0.03         # 임계값 3%로 변경

종료 코드:
    0: other 비율이 임계값 이하 (정상)
    1: other 비율이 임계값 초과 (사전 보완 필요)

새 상품 추가 후 이 스크립트를 실행해 other 비율을 확인하세요.
other 비율이 높으면 benefit_category_keywords.json에 키워드를 추가하세요.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "artifacts" / "prebuilt_riders.json"
DEFAULT_THRESHOLD = 0.05  # 5%


def load_rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "rows" in data:
        return data["rows"]
    if isinstance(data, list):
        return data
    raise ValueError(f"지원하지 않는 JSON 구조: {path}")


def validate(rows: list[dict], threshold: float) -> int:
    """분류 결과 검증. 반환값: 종료 코드 (0=정상, 1=경고)."""
    total = len(rows)
    if total == 0:
        print("데이터 없음.")
        return 0

    cat_counts = Counter(r.get("benefit_category", "(없음)") for r in rows)
    other_count = cat_counts.get("other", 0)
    ratio = other_count / total

    # ── 전체 카테고리 분포 출력 ──
    print(f"\n{'='*50}")
    print(f"  카테고리 분류 검증 결과 (총 {total}건)")
    print(f"{'='*50}")
    for cat, cnt in cat_counts.most_common():
        bar = "█" * int(cnt / total * 30)
        flag = " ← 검토 필요" if cat == "other" and ratio > threshold else ""
        print(f"  {cat:20s} {cnt:4d}건  {bar}{flag}")

    print(f"\n  other 비율: {ratio*100:.1f}%  (임계값: {threshold*100:.0f}%)")

    if ratio > threshold:
        # ── other 급부명 목록 출력 ──
        other_rows = [r for r in rows if r.get("benefit_category") == "other"]
        name_counts = Counter(r.get("benefit_name", "") for r in other_rows)

        print(f"\n{'─'*50}")
        print(f"  ⚠️  other {other_count}건 상세 (benefit_category_keywords.json 키워드 추가 필요)")
        print(f"{'─'*50}")
        for name, cnt in name_counts.most_common():
            print(f"  {cnt:3d}건  {name}")

        print(f"\n{'─'*50}")
        print("  조치 방법:")
        print("    1. 위 급부명을 보고 어느 카테고리인지 판단")
        print("    2. insurance_info/benefit_category_keywords.json 해당 섹션에 키워드 추가")
        print("    3. 이 스크립트를 다시 실행해 other 비율 재확인")
        print(f"{'─'*50}\n")
        return 1

    print(f"\n  ✅ 정상 (other 비율 {ratio*100:.1f}% ≤ {threshold*100:.0f}%)\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="카테고리 분류 검증")
    parser.add_argument(
        "data_path",
        nargs="?",
        default=str(DEFAULT_DATA_PATH),
        help=f"검증할 JSON 파일 경로 (기본값: {DEFAULT_DATA_PATH})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"other 비율 경고 임계값 (기본값: {DEFAULT_THRESHOLD})",
    )
    args = parser.parse_args()

    data_path = Path(args.data_path)
    if not data_path.is_file():
        print(f"파일 없음: {data_path}", file=sys.stderr)
        sys.exit(1)

    print(f"  파일: {data_path}")
    rows = load_rows(data_path)
    exit_code = validate(rows, args.threshold)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
