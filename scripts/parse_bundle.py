"""
상품 번들 단위 파싱 스크립트.

목적:
  "신상품 PDF가 추가돼도 그대로 동작하는 범용 파서" 실행 진입점.

입력 단위: 상품 번들
  - company_name  : 회사명 (예: 라이나생명)
  - product_name  : 상품명 (예: 무배당 라이나 암보험(갱신형))
  - summary_pdf   : 상품요약서 PDF 경로
  - terms_pdf     : 약관 PDF 경로 (없으면 INCOMPLETE)

상태 판정:
  - COMPLETE              : 상품요약서 + 약관 모두 있음
  - INCOMPLETE_SUMMARY_ONLY : 상품요약서만 있음
  - INCOMPLETE_TERMS_ONLY   : 약관만 있음 (파싱 미지원)

실행 예:
  # 1. 단일 번들 파싱
  python scripts/parse_bundle.py \\
      --company "라이나생명" \\
      --product "무배당 라이나 암보험" \\
      --summary path/to/summary.pdf \\
      --terms   path/to/terms.pdf

  # 2. JSON 입력 파일로 여러 번들 일괄 파싱
  python scripts/parse_bundle.py --input bundles.json

  # 3. COMPLETE 번들만 파싱 (--require-complete)
  python scripts/parse_bundle.py --input bundles.json --require-complete

  # bundles.json 형식:
  [
    {
      "company_name": "라이나생명",
      "product_name": "무배당 라이나 암보험",
      "summary_pdf": "path/to/summary.pdf",
      "terms_pdf": "path/to/terms.pdf"
    },
    ...
  ]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from insurance_parser.models import BundleStatus
from insurance_parser.parse.product_bundle_parser import (
    ProductBundleParser,
    make_bundle,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


STATUS_ICON = {
    BundleStatus.COMPLETE: "✓",
    BundleStatus.INCOMPLETE_SUMMARY_ONLY: "△",
    BundleStatus.INCOMPLETE_TERMS_ONLY: "✗",
}


def _save_result(result, output_dir: Path) -> Path | None:
    if result.summary_data is None:
        return None
    company = result.bundle.company_name.replace(" ", "_")
    product = result.bundle.product_name.replace(" ", "_").replace("/", "-")
    filename = f"{company}_{product}_bundle.json"
    output_path = output_dir / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "company_name": result.bundle.company_name,
                "product_name": result.bundle.product_name,
                "status": result.status.value,
                "summary_pdf": str(result.bundle.summary_pdf or ""),
                "terms_pdf": str(result.bundle.terms_pdf or ""),
                "summary_data": result.summary_data,
                "warnings": result.warnings,
                "errors": result.errors,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return output_path


def _print_summary(results: list, output_dir: Path) -> None:
    print("\n" + "=" * 70)
    ok = sum(1 for r in results if not r.errors)
    fail = sum(1 for r in results if r.errors)
    print(f"파싱 완료: {ok}개 성공, {fail}개 실패")
    print("-" * 70)

    for r in results:
        icon = STATUS_ICON.get(r.status, "?")
        label = r.status.value
        name = f"{r.bundle.company_name} / {r.bundle.product_name}"
        if r.errors:
            print(f"  ✗ [{label}] {name}")
            for e in r.errors:
                print(f"      오류: {e}")
        else:
            out_path = _save_result(r, output_dir)
            path_str = f"→ {out_path}" if out_path else "(저장 없음)"
            print(f"  {icon} [{label}] {name} {path_str}")
            for w in r.warnings:
                print(f"      경고: {w}")

    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="상품 번들(요약서+약관) 단위 파싱",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # 단일 번들 인수
    parser.add_argument("--company", help="회사명 (예: 라이나생명)")
    parser.add_argument("--product", help="상품명")
    parser.add_argument("--summary", help="상품요약서 PDF 경로")
    parser.add_argument("--terms",   help="약관 PDF 경로")

    # 배치 인수
    parser.add_argument(
        "--input", "-i",
        help="번들 목록 JSON 파일 경로",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        default=False,
        help="COMPLETE 상태의 번들만 파싱 (약관 없으면 스킵)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=str(ROOT / "output" / "bundles"),
        help="JSON 출력 디렉토리 (기본: output/bundles/)",
    )

    args = parser.parse_args()
    output_dir = Path(args.output_dir)

    # 번들 목록 구성
    bundles = []

    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            logger.error("입력 파일 없음: %s", input_path)
            sys.exit(1)
        with open(input_path, encoding="utf-8") as f:
            raw = json.load(f)
        for item in raw:
            bundles.append(make_bundle(
                company_name=item.get("company_name", ""),
                product_name=item.get("product_name", ""),
                summary_pdf=item.get("summary_pdf"),
                terms_pdf=item.get("terms_pdf"),
            ))

    elif args.company and args.product:
        bundles.append(make_bundle(
            company_name=args.company,
            product_name=args.product,
            summary_pdf=args.summary,
            terms_pdf=args.terms,
        ))

    else:
        parser.print_help()
        sys.exit(0)

    if not bundles:
        logger.warning("처리할 번들이 없습니다.")
        sys.exit(0)

    # 상태 미리 표시
    print(f"\n총 {len(bundles)}개 번들 처리 시작")
    for b in bundles:
        icon = STATUS_ICON.get(b.status, "?")
        print(f"  {icon} {b.status.value:30s}  {b.company_name} / {b.product_name}")

    # 파싱 실행
    bundle_parser = ProductBundleParser()
    results = bundle_parser.parse_bundles(
        bundles,
        require_complete=args.require_complete,
    )

    _print_summary(results, output_dir)


if __name__ == "__main__":
    main()
