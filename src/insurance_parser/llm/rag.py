"""ChromaDB 기반 RAG 헬퍼.

insurance_info/ 디렉토리의 도메인 참조 텍스트(txt, json, xls)를
의미 단위로 청킹하여 ChromaDB에 저장하고 유사 청크를 검색한다.

청킹 전략:
  - 표준약관 txt  → 조항(제N조) 단위
  - 리서치 txt   → 섹션(번호/구분선) 단위
  - KCD txt      → 대분류(■ 섹션) 내 소분류 단위
  - KCD json     → 보험 분류 카테고리별 요약 + 코드 그룹
  - 카테고리 json → 보험 종류별 키워드 사전 → 통째로 1청크
  - XLS          → 상품별 특약 행 그룹 → 요약 텍스트
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .openrouter import embed, is_rag_enabled

log = logging.getLogger(__name__)

_INSURANCE_INFO_DIR = Path(__file__).resolve().parents[3] / "insurance_info"
_CHROMA_DIR = _INSURANCE_INFO_DIR / ".chroma_db"

_collection = None
_initialized = False

_MAX_CHUNK_CHARS = 1500


# ═══════════════════════════════════════════════════════════
# ChromaDB 초기화
# ═══════════════════════════════════════════════════════════

def _get_collection():
    """ChromaDB 컬렉션을 반환한다. 최초 호출 시 초기화."""
    global _collection, _initialized
    if _initialized:
        return _collection

    try:
        import chromadb
    except ImportError:
        log.warning("chromadb 미설치 — pip install chromadb")
        _initialized = True
        return None

    try:
        client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        _collection = client.get_or_create_collection(
            name="insurance_domain",
            metadata={"hnsw:space": "cosine"},
        )
        _initialized = True
        log.info("ChromaDB 초기화: %s (문서 %d건)", _CHROMA_DIR, _collection.count())
    except Exception as e:
        log.error("ChromaDB 초기화 실패: %s", e)
        _initialized = True
        _collection = None

    return _collection


# ═══════════════════════════════════════════════════════════
# 청킹: 표준약관 (조항 단위)
# ═══════════════════════════════════════════════════════════

_RE_ARTICLE = re.compile(r"^(제\d+(?:조의?\d*)?[조관])")
_RE_APPENDIX = re.compile(r"^<부표\s*[\d\-]+>|^■\s|^[가-힣]\.\s")


def _chunk_standard_terms(path: Path) -> list[dict]:
    """2026년_생명보험표준약관.txt → 관/조 단위 청크. 부표도 분할."""
    text = path.read_text(encoding="utf-8")
    source = path.name
    chunks: list[dict] = []
    current_section = ""
    buf: list[str] = []
    in_appendix = False

    for line in text.split("\n"):
        stripped = line.strip()
        is_heading = _RE_ARTICLE.match(stripped)
        is_appendix_heading = _RE_APPENDIX.match(stripped)

        should_flush = False
        if is_heading and buf:
            should_flush = True
        elif in_appendix and is_appendix_heading and buf and len("\n".join(buf)) > 300:
            should_flush = True
        elif buf and len("\n".join(buf)) > _MAX_CHUNK_CHARS:
            should_flush = True

        if should_flush:
            chunk_text = "\n".join(buf).strip()
            if chunk_text:
                chunks.append({
                    "id": f"{source}::art::{len(chunks)}",
                    "text": chunk_text,
                    "source": source,
                    "doc_type": "standard_terms",
                    "topic": current_section or "서문",
                })
            buf = []

        if is_heading:
            current_section = is_heading.group(1)
        elif is_appendix_heading:
            in_appendix = True
            if stripped.startswith("<부표"):
                current_section = stripped.split(">")[0] + ">"

        buf.append(line)

    if buf:
        chunk_text = "\n".join(buf).strip()
        if chunk_text:
            chunks.append({
                "id": f"{source}::art::{len(chunks)}",
                "text": chunk_text,
                "source": source,
                "doc_type": "standard_terms",
                "topic": current_section or "부표",
            })

    merged = _merge_small_chunks(chunks, _MAX_CHUNK_CHARS)
    log.info("[RAG 청킹] %s → %d개 조항 청크", source, len(merged))
    return merged


# ═══════════════════════════════════════════════════════════
# 청킹: 보험 리서치 (섹션 단위)
# ═══════════════════════════════════════════════════════════

_RE_RESEARCH_SECTION = re.compile(r"^(\d+)\)\s+|^[①②③④⑤⑥⑦⑧⑨]")


def _chunk_research(path: Path) -> list[dict]:
    """보험_리서치.txt → 섹션(번호) 단위 청크."""
    text = path.read_text(encoding="utf-8")
    source = path.name
    chunks: list[dict] = []
    buf: list[str] = []
    current_topic = "암보험 분류"

    for line in text.split("\n"):
        m = _RE_RESEARCH_SECTION.match(line.strip())
        if m and buf and len("\n".join(buf)) > 100:
            chunks.append({
                "id": f"{source}::sec::{len(chunks)}",
                "text": "\n".join(buf).strip(),
                "source": source,
                "doc_type": "research",
                "topic": current_topic,
            })
            buf = []

        if m:
            current_topic = line.strip()[:50]

        buf.append(line)

    if buf:
        chunks.append({
            "id": f"{source}::sec::{len(chunks)}",
            "text": "\n".join(buf).strip(),
            "source": source,
            "doc_type": "research",
            "topic": current_topic,
        })

    log.info("[RAG 청킹] %s → %d개 섹션 청크", source, len(chunks))
    return chunks


# ═══════════════════════════════════════════════════════════
# 청킹: KCD9 JSON (보험 분류별 요약 + 코드 목록)
# ═══════════════════════════════════════════════════════════

def _chunk_kcd_json(path: Path) -> list[dict]:
    """kcd9_cancer_codes.json → 보험 분류별 요약 청크."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    source = path.name
    chunks: list[dict] = []

    cats = raw.get("insurance_categories", {})
    for cat_key, cat_val in cats.items():
        label = cat_val.get("label_ko", cat_key)
        code_ranges = cat_val.get("code_ranges", [])
        note = cat_val.get("note", "")
        exclude = cat_val.get("exclude", [])

        sub_cats = cat_val.get("subcategories", {})
        sub_text = ""
        if sub_cats:
            parts = []
            for sk, sv in sub_cats.items():
                parts.append(f"  - {sv.get('label_ko', sk)}: {', '.join(sv.get('codes', []))}")
            sub_text = "\n".join(parts)

        text = (
            f"[보험 분류] {label}\n"
            f"KCD 코드 범위: {', '.join(code_ranges)}\n"
            + (f"제외 코드: {', '.join(exclude)}\n" if exclude else "")
            + (f"비고: {note}\n" if note else "")
            + (f"세부 분류:\n{sub_text}\n" if sub_text else "")
        )
        chunks.append({
            "id": f"{source}::cat::{cat_key}",
            "text": text.strip(),
            "source": source,
            "doc_type": "kcd_classification",
            "topic": label,
        })

    codes = raw.get("codes", {})
    by_category: dict[str, list[str]] = {}
    for code, info in codes.items():
        cat = info.get("category", "unknown")
        name_ko = info.get("name_ko", "")
        name_en = info.get("name_en", "")
        if not name_ko:
            continue
        entry = f"{code} {name_ko}"
        if name_en:
            entry += f" ({name_en})"
        by_category.setdefault(cat, []).append(entry)

    for cat, items in by_category.items():
        for i in range(0, len(items), 25):
            batch = items[i:i + 25]
            label = cats.get(cat, {}).get("label_ko", cat) if cat in cats else cat
            text = f"[KCD9 코드 — {label}]\n" + "\n".join(batch)
            if len(text) > _MAX_CHUNK_CHARS:
                text = text[:_MAX_CHUNK_CHARS]
            chunks.append({
                "id": f"{source}::codes::{cat}::{i}",
                "text": text,
                "source": source,
                "doc_type": "kcd_code_list",
                "topic": label,
            })

    log.info("[RAG 청킹] %s → %d개 KCD JSON 청크", source, len(chunks))
    return chunks


# ═══════════════════════════════════════════════════════════
# 청킹: 카테고리 키워드 JSON (통째로)
# ═══════════════════════════════════════════════════════════

def _chunk_category_keywords(path: Path) -> list[dict]:
    """benefit_category_keywords.json → 보험 종류별 1청크씩."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    source = path.name
    chunks: list[dict] = []

    for ins_type, categories in raw.items():
        if ins_type.startswith("_"):
            continue
        lines = [f"[급부 분류 키워드 — {ins_type}]"]
        if not isinstance(categories, dict):
            continue
        for cat_key, cat_val in categories.items():
            if not isinstance(cat_val, dict):
                continue
            label = cat_val.get("label_ko", cat_key)
            kws = cat_val.get("keywords", [])
            lines.append(f"  {label}: {', '.join(kws)}")
        chunks.append({
            "id": f"{source}::{ins_type}",
            "text": "\n".join(lines),
            "source": source,
            "doc_type": "category_keywords",
            "topic": ins_type,
        })

    log.info("[RAG 청킹] %s → %d개 키워드 청크", source, len(chunks))
    return chunks


# ═══════════════════════════════════════════════════════════
# 청킹: XLS 상품비교표 (상품별 그룹)
# ═══════════════════════════════════════════════════════════

def _chunk_xls(path: Path) -> list[dict]:
    """보장성_상품비교*.xls → 상품별 특약 요약 청크."""
    try:
        import pandas as pd
    except ImportError:
        log.warning("pandas 미설치 — XLS 청킹 건너뜀")
        return []

    try:
        dfs = pd.read_html(str(path))
    except Exception as e:
        log.warning("XLS 파싱 실패: %s", e)
        return []

    if not dfs:
        return []

    df = dfs[0]
    df.columns = [
        "_".join(str(c) for c in col) if isinstance(col, tuple) else str(col)
        for col in df.columns
    ]

    source = path.name
    chunks: list[dict] = []

    col_company = [c for c in df.columns if "보험회사명" in c]
    col_product = [c for c in df.columns if "상품명" in c]
    col_type = [c for c in df.columns if "구분" in c]
    col_benefit = [c for c in df.columns if "급부명칭" in c]
    col_trigger = [c for c in df.columns if "지급사유" in c]
    col_amount = [c for c in df.columns if "지급금액" in c]
    col_premium_m = [c for c in df.columns if "남자" in c and "보험료" in c]
    col_premium_f = [c for c in df.columns if "여자" in c and "보험료" in c]
    col_features = [c for c in df.columns if "특이사항" in c]

    if not (col_company and col_product and col_benefit):
        log.warning("XLS 컬럼 매핑 실패")
        return []

    cc, cp, cb = col_company[0], col_product[0], col_benefit[0]
    ct = col_type[0] if col_type else None
    ctr = col_trigger[0] if col_trigger else None
    ca = col_amount[0] if col_amount else None

    for (company, product), group in df.groupby([cc, cp]):
        lines = [f"[상품비교 — {company} / {product}]"]

        for _, row in group.iterrows():
            typ = str(row.get(ct, "")).strip() if ct else ""
            benefit = str(row.get(cb, "")).strip()
            trigger = str(row.get(ctr, "")).strip()[:80] if ctr else ""
            amount = str(row.get(ca, "")).strip() if ca else ""

            if not benefit or benefit == "nan":
                continue
            entry = f"  [{typ}] {benefit}"
            if amount and amount != "nan":
                entry += f" — {amount}"
            if trigger and trigger != "nan":
                entry += f" ({trigger[:60]})"
            lines.append(entry)

        text = "\n".join(lines)
        if len(text) > _MAX_CHUNK_CHARS:
            text = text[:_MAX_CHUNK_CHARS] + "\n  ..."

        chunks.append({
            "id": f"{source}::product::{len(chunks)}",
            "text": text,
            "source": source,
            "doc_type": "product_comparison",
            "topic": f"{company} {product}",
        })

    log.info("[RAG 청킹] %s → %d개 상품비교 청크", source, len(chunks))
    return chunks


# ═══════════════════════════════════════════════════════════
# 유틸: 작은 청크 병합
# ═══════════════════════════════════════════════════════════

def _merge_small_chunks(chunks: list[dict], max_chars: int) -> list[dict]:
    """연속된 작은 청크를 max_chars 이내로 병합."""
    if not chunks:
        return []
    merged: list[dict] = []
    buf = chunks[0]
    for c in chunks[1:]:
        combined_len = len(buf["text"]) + len(c["text"]) + 2
        same_source = buf.get("doc_type") == c.get("doc_type")
        if combined_len <= max_chars and same_source:
            buf = {
                **buf,
                "text": buf["text"] + "\n\n" + c["text"],
                "topic": buf.get("topic", ""),
            }
        else:
            merged.append(buf)
            buf = c
    merged.append(buf)
    for i, c in enumerate(merged):
        c["id"] = f"{c['source']}::merged::{i}"
    return merged


# ═══════════════════════════════════════════════════════════
# 통합 로더
# ═══════════════════════════════════════════════════════════

def _load_chunks() -> list[dict]:
    """insurance_info/ 의 모든 도메인 파일을 의미 단위 청크로 분할."""
    chunks: list[dict] = []

    for path in sorted(_INSURANCE_INFO_DIR.iterdir()):
        if path.name.startswith("."):
            continue

        try:
            if path.name == "2026년_생명보험표준약관.txt":
                chunks.extend(_chunk_standard_terms(path))
            elif path.name == "보험_리서치.txt":
                chunks.extend(_chunk_research(path))
            elif path.name == "kcd9_cancer_codes.json":
                chunks.extend(_chunk_kcd_json(path))
            elif path.name == "benefit_category_keywords.json":
                chunks.extend(_chunk_category_keywords(path))
            elif path.suffix in (".xls", ".xlsx"):
                chunks.extend(_chunk_xls(path))
            elif path.suffix == ".txt":
                chunks.extend(_chunk_generic_txt(path))
        except Exception as e:
            log.warning("[RAG 청킹] %s 처리 실패: %s", path.name, e)

    log.info("[RAG] 총 %d개 청크 로드 완료", len(chunks))
    return chunks


def _chunk_generic_txt(path: Path) -> list[dict]:
    """인식되지 않는 txt 파일 → 빈 줄 기준 단락 분할."""
    text = path.read_text(encoding="utf-8")
    source = path.name
    chunks: list[dict] = []
    paragraphs = re.split(r"\n\s*\n", text)
    buf: list[str] = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        buf.append(para)
        if len("\n\n".join(buf)) > _MAX_CHUNK_CHARS:
            chunks.append({
                "id": f"{source}::para::{len(chunks)}",
                "text": "\n\n".join(buf),
                "source": source,
                "doc_type": "general",
                "topic": "",
            })
            buf = []

    if buf:
        chunks.append({
            "id": f"{source}::para::{len(chunks)}",
            "text": "\n\n".join(buf),
            "source": source,
            "doc_type": "general",
            "topic": "",
        })

    return chunks


# ═══════════════════════════════════════════════════════════
# 임베딩 + 인덱스 구축
# ═══════════════════════════════════════════════════════════

def _embed_texts(texts: list[str]) -> list[list[float]]:
    """OpenRouter 임베딩 API로 텍스트를 벡터화한다."""
    resp = embed([t[:2000] for t in texts])
    return resp.embeddings


def build_index(*, force: bool = False) -> int:
    """RAG 인덱스를 구축한다. 비활성화 시 0을 반환한다."""
    if not is_rag_enabled():
        return 0

    col = _get_collection()
    if col is None:
        return 0

    if col.count() > 0 and not force:
        log.info("RAG 인덱스 이미 존재: %d 청크", col.count())
        return col.count()

    chunks = _load_chunks()
    if not chunks:
        return 0

    batch_size = 50
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [c["text"] for c in batch]
        ids = [c["id"] for c in batch]
        metadatas = [{
            "source": c["source"],
            "doc_type": c.get("doc_type", ""),
            "topic": c.get("topic", ""),
        } for c in batch]

        try:
            embeddings = _embed_texts(texts)
            col.upsert(
                ids=ids,
                documents=texts,
                embeddings=embeddings,
                metadatas=metadatas,
            )
        except Exception as e:
            log.warning("RAG 배치 임베딩 실패 (batch %d): %s", i, e)

    log.info("RAG 인덱스 구축 완료: %d 청크", col.count())
    return col.count()


# ═══════════════════════════════════════════════════════════
# 검색
# ═══════════════════════════════════════════════════════════

def retrieve(
    query: str,
    *,
    top_k: int = 5,
    doc_types: list[str] | None = None,
) -> list[dict]:
    """쿼리와 유사한 청크를 검색한다.

    doc_types: 필터링할 문서 타입 (None이면 전체).
      standard_terms, research, kcd_classification,
      kcd_code_list, category_keywords, product_comparison, general
    """
    if not is_rag_enabled():
        return []

    col = _get_collection()
    if col is None or col.count() == 0:
        return []

    try:
        q_emb = _embed_texts([query[:2000]])
        if not q_emb:
            return []

        where_filter = None
        if doc_types:
            where_filter = {"doc_type": {"$in": doc_types}}

        results = col.query(
            query_embeddings=q_emb,
            n_results=min(top_k, col.count()),
            include=["documents", "metadatas", "distances"],
            where=where_filter,
        )

        chunks = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            chunks.append({
                "text": doc,
                "source": meta.get("source", ""),
                "doc_type": meta.get("doc_type", ""),
                "topic": meta.get("topic", ""),
                "distance": dist,
            })
        return chunks

    except Exception as e:
        log.warning("RAG 검색 실패: %s", e)
        return []


# ═══════════════════════════════════════════════════════════
# 비교 리포트용 컨텍스트 조합
# ═══════════════════════════════════════════════════════════

_TRUSTED_DOC_TYPES = {
    "standard_terms", "research",
    "kcd_classification", "kcd_code_list", "product_comparison",
}
_MAX_RAG_CHARS = 3000


def get_context_for_comparison(
    base_name: str, comp_name: str, *, top_k: int = 8,
) -> str:
    """비교 리포트 생성 시 RAG로 도메인 참조를 가져온다.

    신뢰된 doc_type만 사용하며, _MAX_RAG_CHARS 이내로 제한.
    """
    if not is_rag_enabled():
        return ""

    build_index()

    query = (
        f"보험 특약 비교: {base_name} vs {comp_name} "
        f"암보험 KCD 코드 보장범위 질병정의 표준약관 진단금"
    )
    chunks = retrieve(query, top_k=top_k)
    if not chunks:
        return ""

    trusted = [c for c in chunks if c.get("doc_type", "") in _TRUSTED_DOC_TYPES]
    if not trusted:
        return ""

    parts: list[str] = []
    total = 0
    for c in trusted:
        topic = c.get("topic", "")
        label = f"[{c['doc_type']}]{' — ' + topic if topic else ''}"
        entry = f"{label}\n{c['text']}"
        if total + len(entry) > _MAX_RAG_CHARS:
            break
        parts.append(entry)
        total += len(entry)

    return "\n\n---\n\n".join(parts)
