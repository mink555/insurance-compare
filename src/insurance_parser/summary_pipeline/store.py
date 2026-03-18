"""artifact store — 파싱 결과 JSON 저장/로드.

HF Spaces / Vercel 배포 고려:
  - 파일 경로 하드코딩 금지
  - 환경변수 ARTIFACT_DIR 또는 기본값 사용
  - SummaryRow dict 리스트 ↔ JSON 파일 직렬화

저장 포맷:
  {
    "_meta": {
      "company_name": str,
      "product_name": str,
      "doc_type": "summary" | "terms" | "unknown",
      "uploaded_at": ISO 8601 string,
      "file_hash": sha256 hex (첫 32 bytes),
      "artifact_version": str,
      "row_count": int
    },
    "rows": [ ...SummaryRow dicts... ]
  }
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ARTIFACT_VERSION = "1.1"

_DEFAULT_ARTIFACT_DIR = Path(os.environ.get(
    "ARTIFACT_DIR",
    str(Path(__file__).resolve().parent.parent.parent.parent / "artifacts"),
))

_PREBUILT_FILENAME = "prebuilt_riders.json"
_UPLOAD_PREFIX = "upload_"


def _file_hash(path: str) -> str:
    """파일 SHA-256 앞 16바이트 hex (빠른 식별용)."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            h.update(f.read(65536))   # 첫 64 KB만 해시
        return h.hexdigest()[:32]
    except Exception:
        return ""


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_meta(
    company_name: str,
    product_name: str,
    doc_type: str,
    rows: list[dict],
    source_path: Optional[str] = None,
) -> dict:
    return {
        "company_name": company_name,
        "product_name": product_name,
        "doc_type": doc_type,
        "uploaded_at": _now_iso(),
        "file_hash": _file_hash(source_path) if source_path else "",
        "artifact_version": ARTIFACT_VERSION,
        "row_count": len(rows),
    }


class ArtifactStore:
    """파싱 결과 JSON artifact 저장/로드."""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir) if base_dir else _DEFAULT_ARTIFACT_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 내부: 포맷 읽기/쓰기
    # ------------------------------------------------------------------

    def _write(self, path: Path, meta: dict, rows: list[dict]) -> None:
        payload = {"_meta": meta, "rows": rows}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_rows(self, path: Path) -> list[dict]:
        """구/신 포맷 모두 지원."""
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw           # v1.0 호환: 배열 직렬화
        return raw.get("rows", [])

    def _read_meta(self, path: Path) -> Optional[dict]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw.get("_meta")
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # 사전 파싱 결과 (prebuilt)
    # ------------------------------------------------------------------

    def load_prebuilt(self) -> list[dict]:
        path = self.base_dir / _PREBUILT_FILENAME
        if not path.exists():
            logger.warning("prebuilt artifact 없음: %s", path)
            return []
        try:
            return self._read_rows(path)
        except Exception as e:
            logger.error("prebuilt 로드 실패: %s", e)
            return []

    def save_prebuilt(self, rows: list[dict]) -> Path:
        path = self.base_dir / _PREBUILT_FILENAME
        meta = _make_meta("(prebuilt)", "(all)", "summary", rows)
        self._write(path, meta, rows)
        logger.info("prebuilt 저장: %s (%d행)", path, len(rows))
        return path

    # ------------------------------------------------------------------
    # 업로드 파싱 결과 (per-company)
    # ------------------------------------------------------------------

    def save_upload(
        self,
        company_name: str,
        rows: list[dict],
        product_name: str = "",
        doc_type: str = "summary",
        source_path: Optional[str] = None,
    ) -> Path:
        """업로드 파싱 결과를 메타데이터 포함 JSON으로 저장.

        동일 file_hash artifact가 이미 존재하면 저장을 건너뛰고 기존 파일 경로를 반환합니다.
        """
        new_hash = _file_hash(source_path) if source_path else ""

        # 동일 file_hash 중복 저장 방지
        if new_hash:
            for path in sorted(self.base_dir.glob(f"{_UPLOAD_PREFIX}*.json")):
                m = self._read_meta(path)
                if m and m.get("file_hash") == new_hash:
                    logger.warning(
                        "[ArtifactStore/save_upload] 동일 file_hash 이미 존재 — 저장 생략: %s (hash=%s)",
                        path.name, new_hash,
                    )
                    return path

        safe_name = company_name.replace("/", "_").replace(" ", "_")
        filename = f"{_UPLOAD_PREFIX}{safe_name}_{int(time.time())}.json"
        path = self.base_dir / filename
        meta = _make_meta(company_name, product_name, doc_type, rows, source_path)
        self._write(path, meta, rows)
        logger.info("업로드 저장: %s (%d행, doc_type=%s)", path, len(rows), doc_type)
        return path

    def load_uploads(self) -> list[dict]:
        all_rows: list[dict] = []
        seen_file_hashes: set[str] = set()
        for path in sorted(self.base_dir.glob(f"{_UPLOAD_PREFIX}*.json")):
            try:
                meta = self._read_meta(path)
                fh = (meta or {}).get("file_hash", "")
                if fh and fh in seen_file_hashes:
                    logger.warning(
                        "[ArtifactStore] 동일 file_hash 중복 artifact 건너뜀: %s (hash=%s)",
                        path.name, fh,
                    )
                    continue
                if fh:
                    seen_file_hashes.add(fh)
                all_rows.extend(self._read_rows(path))
            except Exception as e:
                logger.warning("업로드 파일 로드 실패 %s: %s", path, e)
        return all_rows

    def list_companies(self) -> list[str]:
        """저장된 모든 artifact에서 회사명 목록을 반환한다."""
        companies: list[str] = []
        seen: set[str] = set()
        for row in self.load_all():
            name = row.get("insurer", "")
            if name and name not in seen:
                seen.add(name)
                companies.append(name)
        return companies

    def list_upload_metas(self) -> list[dict]:
        """업로드 artifact 파일들의 메타 정보 목록을 반환한다."""
        metas: list[dict] = []
        for path in sorted(self.base_dir.glob(f"{_UPLOAD_PREFIX}*.json")):
            meta = self._read_meta(path)
            if meta:
                metas.append(meta)
        return metas

    def load_all(self) -> list[dict]:
        """prebuilt + uploads 전체 로드. dedupe_key 기준 최종 중복 행 제거."""
        prebuilt = self.load_prebuilt()
        uploads = self.load_uploads()
        combined = prebuilt + uploads

        logger.info(
            "[ArtifactStore/load_all] prebuilt=%d + uploads=%d = combined=%d",
            len(prebuilt), len(uploads), len(combined),
        )

        # dedupe_key가 없는 구 포맷 row는 normalizer.make_dedupe_key로 즉시 생성
        from .normalizer import make_dedupe_key
        seen: set[str] = set()
        deduped: list[dict] = []
        for row in combined:
            key = row.get("dedupe_key") or make_dedupe_key(row)
            if not row.get("dedupe_key"):
                row = {**row, "dedupe_key": key}
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)

        after = len(deduped)
        if after != len(combined):
            logger.warning(
                "[ArtifactStore/load_all] 최종 dedupe: %d → %d행 (%d건 제거)",
                len(combined), after, len(combined) - after,
            )
        else:
            logger.info("[ArtifactStore/load_all] 최종 DataFrame: %d행 (중복 없음)", after)

        return deduped
