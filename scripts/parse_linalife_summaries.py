"""
라이나 전체 암보험 상품요약서 파싱 스크립트.

각 암보험 상품별로:
  1. 암보험/상품요약서/{상품명}/... PDF 파싱 (주계약 + 의무부가특약)
  2. 상품 구성의 선택특약 이름으로 암특약/상품요약서 자동 탐색 + 파싱
  3. JSON 출력: output/linalife/{상품명}_summary.json

실행:
  python scripts/parse_linalife_summaries.py [--product 상품폴더명]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from insurance_parser.parse.lina_summary_parser import LinaProductSummaryParser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="라이나 암보험 상품요약서 파싱")
    parser.add_argument(
        "--product",
        help="특정 상품 폴더명 (예: 무배당라이나초간편암보험(갱신형)). 없으면 전체 파싱.",
    )
    parser.add_argument(
        "--base-dir",
        default=str(ROOT / "linalife"),
        help="linalife 루트 경로",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "output" / "linalife"),
        help="JSON 출력 경로",
    )
    args = parser.parse_args()

    base_dir = Path(args.base_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cancer_summary_dir = base_dir / "암보험" / "상품요약서"
    rider_summary_dir = base_dir / "암특약" / "상품요약서"

    if not cancer_summary_dir.exists():
        logger.error("암보험 상품요약서 폴더 없음: %s", cancer_summary_dir)
        sys.exit(1)

    # 파싱 대상 상품 목록
    if args.product:
        product_dirs = [cancer_summary_dir / args.product]
    else:
        product_dirs = sorted([d for d in cancer_summary_dir.iterdir() if d.is_dir()])

    lina_parser = LinaProductSummaryParser()
    results = []
    errors = []

    for product_dir in product_dirs:
        pdfs = sorted(product_dir.glob("*.pdf"))
        if not pdfs:
            logger.warning("PDF 없음: %s", product_dir)
            continue

        # 상품요약서 PDF (보통 1개)
        main_pdf = pdfs[0]
        logger.info("파싱 시작: %s", product_dir.name)

        try:
            result = lina_parser.parse_product_auto(
                main_summary_pdf=main_pdf,
                rider_summary_base_dir=rider_summary_dir,
            )

            output_path = output_dir / f"{product_dir.name}_summary.json"
            lina_parser.save_json(result, output_path)

            contract_summary = [
                f"{c['type']}: {c['name']} (급부 {len(c['benefits'])}개)"
                for c in result["contracts"]
            ]
            logger.info(
                "완료: %s → %d개 계약",
                result["product_name"],
                len(result["contracts"]),
            )
            for s in contract_summary:
                logger.info("  - %s", s)

            results.append({
                "product": result["product_name"],
                "output": str(output_path),
                "contracts": len(result["contracts"]),
                "status": "ok",
            })

        except Exception as e:
            logger.error("파싱 실패: %s: %s", product_dir.name, e, exc_info=True)
            errors.append({"product": product_dir.name, "error": str(e)})

    # 요약 출력
    print("\n" + "=" * 60)
    print(f"파싱 완료: {len(results)}개 성공, {len(errors)}개 실패")
    for r in results:
        print(f"  ✓ {r['product']} → {r['output']}")
    for e in errors:
        print(f"  ✗ {e['product']}: {e['error']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
