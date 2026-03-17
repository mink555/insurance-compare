---
title: 라이나 인사이트 - 보험 상품 분석 솔루션
short_description: 라이나생명 암보험 특약 비교 분석 플랫폼
emoji: ⚖️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
---

# InsureCompare — 보험 특약 비교 AI 솔루션

> **결론**: PDF 한 장 올리면 당사 vs 타사 보험 특약을 자동 비교하고, 근거 기반 리포트를 생성한다.

## 왜 만들었나

보험 상품 비교는 수작업으로 하면 **특약명 표기 차이**, **질병 분류 기준 차이**, **금액 조건 해석** 등에서 실수가 발생한다.
이 도구는 PDF 파싱 → 정규화 → 규칙 기반 비교 → LLM 보조 분석을 자동화하여, 분석자가 "판단"에만 집중할 수 있게 한다.

## 전체 흐름도

```
┌─────────────────────────────────────────────────────────────────────┐
│                        사용자 (Streamlit UI)                         │
│   STEP 1: 설정        STEP 2: 비교           STEP 3: 리포트          │
│   ┌──────────┐       ┌──────────────┐       ┌─────────────┐        │
│   │ 상품 선택  │──────▶│ 3-Table 비교  │──────▶│ 분석 리포트   │       │
│   │ PDF 업로드 │       │ 카드 UI       │       │ MD/CSV 내보내기│      │
│   └─────┬────┘       └──────┬───────┘       └─────────────┘        │
└─────────┼──────────────────┼───────────────────────────────────────┘
          │                  │
          ▼                  ▼
┌─────────────────┐  ┌──────────────────────────────────────────────┐
│  Summary Pipeline│  │           Comparison Engine                   │
│  (4단계 처리)     │  │                                              │
│                  │  │  ① 급부 매칭  ─▶ canonical_key 기반           │
│  ① PDF 파서     │  │  ② 슬롯 비교  ─▶ 금액 단독 판정               │
│  ② 정규화       │  │  ③ 우위 판정  ─▶ 규칙 엔진 (compare_rules)    │
│  ③ 급부 분류    │  │  ④ LLM 보조   ─▶ 조건상이 항목만 분석          │
│  ④ 비교행 변환  │  │                                              │
└─────────────────┘  └──────────────────────────────────────────────┘
          │                  │
          ▼                  ▼
┌─────────────────┐  ┌──────────────────┐
│  ArtifactStore   │  │  도메인 RAG       │
│  (JSON 저장/로드) │  │  (ChromaDB)       │
│                  │  │  표준약관 / KCD9   │
└─────────────────┘  └──────────────────┘
```

## 핵심 파이프라인: Summary Pipeline (4단계)

```
PDF 업로드
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ Stage 1: Parse  ─  회사별 전용 파서로 테이블 추출              │
│   • 라이나 → lina_summary_parser.py (좌표 기반 fallback)     │
│   • 한화   → hanwha_summary_parser.py                       │
├──────────────────────────────────────────────────────────────┤
│ Stage 2: Normalize  ─  텍스트 정규화, 금액 파싱               │
│   • "1,000만원" → 숫자 변환                                  │
│   • 특약명 표기 통일 (무배당, 약관 접미사 제거)                 │
├──────────────────────────────────────────────────────────────┤
│ Stage 3: Classify  ─  급부 카테고리 분류                      │
│   • 암 종류 (일반암/고액암/소액암/유사암)                      │
│   • 보험 타입 자동 감지 (암보험/치매보험/CI 등)                │
├──────────────────────────────────────────────────────────────┤
│ Stage 4: Export  ─  comparison_rows로 변환                    │
│   • 같은 급부명의 상세 행들을 하나의 비교 단위로 집약           │
│   • 질병 변형(trigger_variants), 조건별 금액(amount_detail)   │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
  SummaryRow[] → ArtifactStore 저장
```

## 비교 엔진: 우위 판정 로직

### 판정 원칙

| 상황 | 판정 |
|------|------|
| 금액이 숫자로 동일 | **동일** |
| 당사 금액 > 타사 금액 | **당사우위** (이유: 금액↑) |
| 당사 금액 < 타사 금액 | **타사우위** (이유: 금액↓) |
| 금액이 텍스트로 달라 비교 불가 | **금액상이** |
| 한쪽에만 존재 | **당사단독 / 타사단독** |

### 조건부 금액 처리

`amount_detail`에 "1년미만 500만원 / 1년이상 1,000만원" 형태가 있을 경우,
`_extract_representative_amount()`가 **1년이상 금액**을 대표값으로 추출해 비교한다.
단, 같은 행 안의 데이터만 사용하며 다른 행의 컨텍스트는 참조하지 않는다.

### 비교 차원 (compare_rules.json)

| 차원 | 타입 | 설명 |
|------|------|------|
| `amount_display` | numeric | 금액 — **유일한 우위 판정 기준** |
| `payment_freq` | display_only | 지급횟수 — 참고용 표시만 |
| `payment_limit` | display_only | 지급한도 — 참고용 표시만 |
| `reduction_rule` | display_only | 감액조건 — 참고용 표시만 |
| `start_condition` | display_only | 보장개시 — 참고용 표시만 |
| `trigger` | display_only | 지급사유 — 참고용 표시만 |

## UI 3단계 워크플로우

```
STEP 1 (설정)           STEP 2 (비교)              STEP 3 (리포트)
┌─────────────┐       ┌──────────────────┐       ┌─────────────────┐
│ • 당사 상품   │       │ Card 1: 보장 범위  │       │ §1 전략적 요약    │
│ • 타사 상품   │──────▶│ Card 2: 지급 조건  │──────▶│ §2 핵심 비교     │
│ • PDF 업로드  │       │ Card 3: 급부 금액  │       │ §3 심층 분석     │
│ • 대시보드    │       │ Sticky 요약 바     │       │ §4 Evidence 부록 │
└─────────────┘       └──────────────────┘       └─────────────────┘
```

## 실행 방법

```bash
# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일에서 OPENROUTER_API_KEY를 실제 키로 교체

# 실행
streamlit run app.py
```

## 환경변수

| 변수 | 설명 | 필수 |
|------|------|:----:|
| `OPENROUTER_API_KEY` | OpenRouter API 키 | ✅ |
| `OPENROUTER_MODEL` | LLM 모델 (기본: `qwen/qwen3-235b-a22b`) | |
| `OPENROUTER_EMBEDDING_MODEL` | 임베딩 모델 (기본: `intfloat/multilingual-e5-large`) | |
| `ENABLE_RAG` | 도메인 RAG 활성화 (기본: `true`) | |

## 프로젝트 구조

```
app.py                              # Streamlit 진입점 + 디자인 시스템 CSS
patch_og.py                         # OG 메타 태그 패치 (Docker 빌드용)
views/workbench.py                  # 3단계 UI (setup → compare → report)

src/insurance_parser/
  models.py                         # 상품 번들 모델 (ProductBundle, BundleStatus)

  parse/                            # 회사별 PDF 파서
    utils.py                        #   파서 공통 유틸 (clean, normalize_benefit_name)
    lina_summary_parser.py          #   라이나생명 전용
    hanwha_summary_parser.py        #   한화생명 전용
    product_bundle_parser.py        #   파서 레지스트리 + GenericSummaryParser

  summary_pipeline/                 # 4단계 파이프라인
    pipeline.py                     #   parse → normalize → classify → export
    models.py                       #   SummaryRow, CanonicalBenefit 등
    normalizer.py                   #   정규화 + comparison_rows 변환
    classifier.py                   #   급부 카테고리 분류 (키워드 기반)
    detector.py                     #   문서 타입 판별 (요약서 vs 약관)
    store.py                        #   ArtifactStore (JSON 저장/로드/중복제거)

  comparison/                       # 비교 엔진
    normalize.py                    #   급부 매칭 (canonical_key 기반)
    engine.py                       #   규칙 기반 슬롯 비교 + 우위 판정
    enrich.py                       #   LLM 슬롯 추출 (조건상이 항목)

  llm/                              # LLM + RAG
    openrouter.py                   #   OpenRouter API 클라이언트
    rag.py                          #   ChromaDB 기반 도메인 RAG (ENABLE_RAG=true 시 활성)

  report/                           # 리포트 생성
    generator.py                    #   SummaryReportBuilder

config/
  compare_rules.json                # 비교 규칙 (슬롯별 판정 로직)
  synonyms.json                     # 동의어 사전 (급부명 → canonical 매핑)

insurance_info/                     # 도메인 참조 데이터
  kcd9_cancer_codes.json            #   KCD9 암 코드 분류
  benefit_category_keywords.json    #   급부 카테고리 키워드 (보험 종류별)
  2026년_생명보험표준약관.txt         #   표준약관 텍스트 (RAG용)
```

## 신규 회사/상품 추가 방법

### 1. 새 파서 작성

```python
# src/insurance_parser/parse/samsung_summary_parser.py
from .utils import clean, normalize_benefit_name  # 공통 유틸 재사용

def parse(pdf_path: str) -> dict:
    """삼성생명 상품요약서 파서. 반환 형식은 lina_summary_parser와 동일."""
    ...
```

### 2. 파서 레지스트리 등록

```python
# src/insurance_parser/parse/product_bundle_parser.py
_PARSERS = {
    "라이나생명": lina_summary_parser.parse,
    "한화생명": hanwha_summary_parser.parse,
    "삼성생명": samsung_summary_parser.parse,  # 추가
}
```

### 3. 동의어 등록 (필요 시)

급부명 표기가 다를 경우 `config/synonyms.json`에 추가:

```json
{
  "암치료보험금": "암직접치료급여금",
  "암주요치료보험금": "암직접치료급여금"
}
```

### 4. 급부 카테고리 추가 (신규 보험 종류 시)

`insurance_info/benefit_category_keywords.json`에 새 보험 종류 섹션 추가.
코드 변경 불필요 — `classifier.py`가 JSON만 읽어서 자동 분류.

## 기술 스택

| 역할 | 기술 |
|------|------|
| PDF 추출 | PyMuPDF |
| LLM | OpenRouter → Qwen3-235B-A22B (오픈소스) |
| 임베딩 | Multilingual-E5-Large (90+언어) |
| 벡터 DB | ChromaDB (로컬, ENABLE_RAG=true 시 활성) |
| UI | Streamlit |
| 데이터 모델 | Pydantic v2 |
| 배포 | Docker (HuggingFace Spaces) |

## 보험 상품 구조 참고

```
상품군 (예: 암보험)
  └─ 상품 = 주계약 + 선택특약 1, 2, ... n
       │
       └─ 비교 단위는 항상 "특약" (contract_name)
            └─ 급부 (benefit_name) = 비교의 최소 단위
                 └─ canonical_key = "급부명|질병분류" 형태로 매칭
```

## 사람 관리 포인트

AI가 자동 처리하지만, 아래 항목은 **사람이 직접 확인·수정**해야 정확도가 유지된다.

### 정기 확인 (상품 변경 시마다)

| 파일 | 언제 | 무엇을 |
|------|------|--------|
| `config/synonyms.json` | 새 회사·상품 추가 시 | 새 급부명이 기존 canonical_key로 매칭되는지 확인. 안 되면 동의어 추가 |
| `config/compare_rules.json` | 비교 기준 변경 시 | 슬롯별 `type` (numeric / display_only) 및 `higher_is_better` 재검토 |
| `insurance_info/benefit_category_keywords.json` | 신규 보험 종류 도입 시 | 새 보험 종류 섹션과 키워드 추가 |

### 신규 회사 PDF 도입 시

1. **파서 작성** — 해당 회사 PDF 레이아웃에 맞는 파서 작성 후 `_PARSERS` 레지스트리 등록
2. **샘플 파싱 검증** — `artifacts/prebuilt_riders.json`에 저장된 결과 중 `benefit_name`, `amount`, `amount_detail` 필드가 올바르게 파싱됐는지 직접 확인
3. **canonical_key 충돌 점검** — 같은 키로 매칭되면 안 되는 급부가 묶이거나, 매칭돼야 하는 급부가 누락되진 않는지 비교 결과 검토

### 비교 결과 이상 시 체크리스트

| 증상 | 원인 후보 | 확인 위치 |
|------|----------|-----------|
| 분명 같은 급부인데 `당사단독 / 타사단독`으로 분리됨 | canonical_key 불일치 | `synonyms.json` 동의어 누락 |
| 금액이 같은데 `타사우위` / `당사우위` 판정 | `amount_detail` 파싱 오류 | 파싱 결과 JSON에서 `amount_detail` 값 직접 확인 |
| 조건부 금액 비교가 이상함 | `_extract_representative_amount()` 패턴 미인식 | `engine.py` `_RE_YEAR_COND` 정규식 확장 필요 |
| 완전히 다른 급부가 같은 키로 매칭됨 | 동의어 과도한 범용화 | `synonyms.json`에서 해당 매핑 분리 |
| 새 보험 종류 급부가 전부 `기타`로 분류됨 | 카테고리 키워드 미등록 | `benefit_category_keywords.json` 해당 섹션 추가 |

### 데이터 축적 관리

- `artifacts/prebuilt_riders.json` — 파싱 결과 캐시. **파서 로직 변경 시 삭제 후 재파싱** 필요
- `artifacts/uploads/` — 사용자 업로드 파일. 운영 환경에서는 주기적 정리 권장
- `insurance_info/.chroma_db/` — RAG 인덱스. 도메인 문서(`insurance_info/*.txt`) 변경 시 `build_index(force=True)` 재실행

## 알려진 한계 및 TODO

- **약관 교차 검증** 미구현: 현재는 상품요약서만 파싱. 약관(TermsParser) 연동 후 급부 조건 교차 검증 예정.
- **RAG 기능**: `ENABLE_RAG=true` 설정 필요. 로컬 ChromaDB 인덱스 빌드에 수 분 소요.
- **신규 회사 파서**: 파서 레지스트리에 등록되지 않은 회사는 `GenericSummaryParser`(텍스트 기반 fallback)로 처리되며 정확도가 낮을 수 있음.
