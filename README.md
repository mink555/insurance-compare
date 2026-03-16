---
title: 라이나 인사이트 - 보험 상품 분석 솔루션
short_description: 라이나생명 암보험 특약 비교 분석 플랫폼
emoji: ⚖️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
thumbnail: https://huggingface.co/spaces/minkyyee/insurance-compare/resolve/main/image.png
---

# InsureCompare — 보험 특약 비교 AI 솔루션

> **결론**: PDF 한 장 올리면 당사 vs 타사 보험 특약을 자동 비교하고, 근거 기반 리포트를 생성한다.

## 왜 만들었나 (이유)

보험 상품 비교는 수작업으로 하면 **특약명 표기 차이**, **질병 분류 기준 차이**, **금액 조건 해석** 등에서 실수가 발생한다.
이 도구는 PDF 파싱 → 정규화 → 규칙 기반 비교 → LLM 보조 분석을 자동화하여, 분석자가 "판단"에만 집중할 수 있게 한다.

## 어떻게 동작하나 (근거)

### 전체 흐름도

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
│  ① PDF 파서     │  │  ② 슬롯 비교  ─▶ 금액/조건/대기기간 등        │
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

### 핵심 파이프라인: Summary Pipeline (4단계)

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
│   • 보험 타입 자동 감지 (cancer/ci/health 등)                 │
├──────────────────────────────────────────────────────────────┤
│ Stage 4: Export  ─  comparison_rows로 변환                    │
│   • 같은 급부명의 상세 행들을 하나의 비교 단위로 집약           │
│   • 질병 변형(trigger_variants), 조건별 금액(amount_detail)   │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
  SummaryRow[] → ArtifactStore 저장
```

### 비교 엔진: 3-Layer 구조

```
Layer 1: 급부 매칭 (normalize.py)
    canonical_key("암진단금|일반암") 기준으로 당사↔타사 급부 1:1 매칭
    │
    ▼
Layer 2: 슬롯 비교 (engine.py)
    compare_rules.json 기반으로 각 차원(금액/조건/대기기간 등) 비교
    │
    ▼
Layer 3: LLM 보조 (enrich.py)
    "조건상이" 판정된 항목만 LLM이 약관 문장 → 비교 슬롯 추출
```

### UI 3단계 워크플로우

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
  models.py                         # 핵심 데이터 모델 (ProductBundle 등)

  parse/                            # 회사별 PDF 파서
    lina_summary_parser.py          #   라이나생명 전용
    hanwha_summary_parser.py        #   한화생명 전용
    product_bundle_parser.py        #   파서 레지스트리

  summary_pipeline/                 # 4단계 파이프라인
    pipeline.py                     #   parse → normalize → classify → export
    models.py                       #   SummaryRow, CanonicalBenefit 등
    normalizer.py                   #   정규화 + comparison_rows 변환
    classifier.py                   #   급부 카테고리 분류
    detector.py                     #   문서 타입 판별
    store.py                        #   ArtifactStore (JSON 저장/로드)

  comparison/                       # 비교 엔진
    normalize.py                    #   급부 매칭 (canonical_key 기반)
    engine.py                       #   규칙 기반 슬롯 비교
    enrich.py                       #   LLM 슬롯 추출 (조건상이 항목)

  llm/                              # LLM + RAG
    openrouter.py                   #   OpenRouter API 클라이언트
    rag.py                          #   ChromaDB 기반 도메인 RAG

  report/                           # 리포트 생성
    generator.py                    #   SummaryReportBuilder

config/
  compare_rules.json                # 비교 규칙 (슬롯별 판정 로직)
  synonyms.json                     # 동의어 사전

insurance_info/                     # 도메인 참조 데이터
  kcd9_cancer_codes.json            #   KCD9 암 코드 분류
  2026년_생명보험표준약관.txt         #   표준약관 텍스트
  benefit_category_keywords.json    #   급부 카테고리 키워드
```

## 기술 스택

| 역할 | 기술 |
|------|------|
| PDF 추출 | PyMuPDF |
| LLM | OpenRouter → Qwen3-235B-A22B (오픈소스) |
| 임베딩 | Multilingual-E5-Large (90+언어) |
| 벡터 DB | ChromaDB (로컬) |
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
