---
title: 라이나 인사이트 - 보험 상품 분석 솔루션
short_description: 라이나생명 암보험 특약 비교 분석 플랫폼
emoji: ⚖️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
---

# InsureCompare — 보험 특약 비교 솔루션

PDF 한 장 올리면 당사 vs 타사 보험 특약을 자동 비교하고 근거 기반 리포트를 생성한다.

---

## 실행

```bash
pip install -r requirements.txt
cp .env.example .env   # OPENROUTER_API_KEY 입력
streamlit run app.py
```

| 환경변수 | 설명 | 필수 |
|---------|------|:---:|
| `OPENROUTER_API_KEY` | OpenRouter API 키 | ✅ |
| `OPENROUTER_MODEL` | LLM 모델 (기본: `qwen/qwen3-235b-a22b`) | |
| `ENABLE_RAG` | 도메인 RAG 활성화 (기본: `true`) | |

---

## 동작 흐름

```
PDF 업로드
  │
  ├─ 1. Parse      회사별 파서로 테이블 추출
  ├─ 2. Normalize  텍스트 정규화, 금액 파싱
  ├─ 3. Classify   급부 카테고리 분류
  └─ 4. Compare    canonical_key 기반 매칭 → 규칙 엔진 우위 판정 → LLM 보조 분석
                                                                         ↓
                                                                    Streamlit 리포트
```

**3단계 UI**: 설정 → 비교 (보장범위 / 지급조건 / 급부금액) → 리포트 (전략요약 / 심층분석 / Evidence 부록)

---

## 우위 판정 기준

| 상황 | 판정 |
|------|------|
| 금액 동일, 조건 동일 | 동일 |
| 금액 동일, 한쪽만 조건부 | 조건 없는 쪽 우위 |
| 금액 상이 | 높은 쪽 우위 + rationale에 조건 차이 표시 |
| 조건이 달라 비교 불가 | 조건상이 |
| 한쪽에만 존재 | 당사단독 / 타사단독 |

**조건부 금액 처리**: `amount_detail`에 복수 조건이 있을 경우 최대 금액 기준으로 비교.  
예) 한화 "1년미만 500만원 / 1년이상 1,000만원" → 1,000만원으로 비교.

---

## 프로젝트 구조

```
app.py                          Streamlit 진입점
views/workbench.py              3단계 UI 렌더링

src/insurance_parser/
  parse/
    utils.py                    파서 공통 유틸
    lina_summary_parser.py      라이나생명 PDF 파서
    hanwha_summary_parser.py    한화생명 PDF 파서
    product_bundle_parser.py    파서 레지스트리
  summary_pipeline/
    pipeline.py                 4단계 파이프라인 오케스트레이터
    normalizer.py               정규화 + comparison_rows 변환
    classifier.py               급부 카테고리 분류
    store.py                    ArtifactStore (JSON 저장/로드)
  comparison/
    normalize.py                급부 매칭 (canonical_key)
    engine.py                   규칙 기반 우위 판정
    enrich.py                   LLM 슬롯 추출 (조건상이 항목)
  llm/
    openrouter.py               OpenRouter 클라이언트
    rag.py                      ChromaDB 도메인 RAG
  report/
    generator.py                리포트 생성 + Evidence 수집

config/
  compare_rules.json            슬롯별 비교 규칙
  synonyms.json                 급부명 동의어 사전

insurance_info/
  kcd9_cancer_codes.json        KCD9 암 코드
  benefit_category_keywords.json  급부 카테고리 키워드
  2026년_생명보험표준약관.txt     표준약관 (RAG용)
```

---

## 신규 회사/상품 추가

1. **파서 작성** — `src/insurance_parser/parse/`에 새 파서 작성, `utils.py` 공통 유틸 재사용
2. **레지스트리 등록** — `product_bundle_parser.py`의 `_PARSERS` dict에 추가
3. **동의어 등록** — 급부명 표기가 다를 경우 `config/synonyms.json`에 추가
4. **카테고리 추가** — 신규 보험 종류면 `benefit_category_keywords.json`에 섹션 추가 (코드 수정 불필요)

---

## 관리 포인트

| 파일 | 확인 시점 |
|------|---------|
| `config/synonyms.json` | 새 회사·상품 추가 시 — 급부명 매칭 누락 여부 확인 |
| `config/compare_rules.json` | 비교 기준 변경 시 — 슬롯 타입·방향 검토 |
| `benefit_category_keywords.json` | 신규 보험 종류 도입 시 — 키워드 섹션 추가 |
| `artifacts/prebuilt_riders.json` | 파서 로직 변경 시 — 삭제 후 재파싱 |

**이상 증상 → 원인**

- 같은 급부인데 단독 판정 → `synonyms.json` 동의어 누락
- 금액이 같은데 우위 판정 → `amount_detail` 파싱 오류
- 새 보험 종류 급부가 전부 `기타` → `benefit_category_keywords.json` 키워드 미등록
- 다른 급부가 같은 키로 매칭 → `synonyms.json` 매핑 범용화 과도

---

## 기술 스택

| 역할 | 기술 |
|------|------|
| PDF 추출 | PyMuPDF |
| LLM | OpenRouter → Qwen3-235B |
| 벡터 DB | ChromaDB |
| UI | Streamlit |
| 배포 | Docker (Hugging Face Spaces) |
