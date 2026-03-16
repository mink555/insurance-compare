"""테스트 공용 설정."""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))


def find_pdfs(company_dir: str, doc_type: str) -> list[Path]:
    d = BASE_DIR / company_dir
    if not d.exists():
        return []
    return sorted(p for p in d.rglob("*.pdf") if doc_type in str(p))
