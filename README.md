---
title: 라이나 인사이트 - 보험 상품 분석 솔루션
emoji: ⚖️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
---

# InsureCompare — 보험 특약 비교 AI 솔루션

라이나생명 기준으로 타사(한화생명 등) 암보험 특약을 비교 분석하는 도구.

## 핵심 기능

1. **PDF 파싱** — 상품요약서/약관 PDF를 업로드하면 회사별 전용 파서로 급부 정보 추출
2. **도메인 RAG** — KCD9 암코드, 생명보험 표준약관 등 도메인 참조를 ChromaDB에 임베딩
3. **특약 비교 리포트** — 당사 vs 타사 특약 단위 비교 분석 (4섹션 구조)
4. **Streamlit UI** — 3단계 워크플로우 (설정 → 비교 → 리포트)

## 보험상품 구조

```
상품군 (예: 암보험)
  └─ 상품 = 주계약(필수특약) + 선택특약 1, 2, ... n
     비교 단위는 항상 "특약"
```

## 실행 방법

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 환경변수 (.env)

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `OPENROUTER_API_KEY` | OpenRouter API 키 (필수) | — |
| `OPENROUTER_MODEL` | LLM 모델 | `qwen/qwen3-235b-a22b` |
| `OPENROUTER_EMBEDDING_MODEL` | 임베딩 모델 | `intfloat/multilingual-e5-large` |
| `ENABLE_RAG` | RAG 활성화 | `true` |

## 프로젝트 구조

```
app.py                          # Streamlit 진입점
views/workbench.py              # 메인 UI (3단계 UX)

src/insurance_parser/
  config.py                     # 회사별 프로파일 로더
  models.py                     # Pydantic 데이터 모델

  extract/                      # PDF → 텍스트 추출
    base.py
    pymupdf_ext.py

  parse/                        # 회사별 상품요약서 파서
    lina_summary_parser.py
    hanwha_summary_parser.py
    product_bundle_parser.py

  normalize/                    # 텍스트/이름 정규화
    names.py
    text.py

  summary_pipeline/             # 핵심 4단 파이프라인
    pipeline.py                 #   parse → normalize → classify → export
    models.py                   #   SummaryRow, CanonicalBenefit 등
    classifier.py               #   급부 카테고리 분류
    detector.py                 #   문서 타입 판별
    normalizer.py               #   정규화 + comparison_rows 변환
    store.py                    #   ArtifactStore (JSON 저장/로드)

  llm/                          # LLM + RAG
    openrouter.py               #   OpenRouter API (오픈소스 모델)
    rag.py                      #   ChromaDB 기반 도메인 RAG

  report/                       # 리포트 생성
    generator.py                #   SummaryReportBuilder (규칙 기반)

config/companies/               # 회사별 YAML 설정
insurance_info/                 # 도메인 참조 (KCD9, 표준약관 등)
hanwhalife/                     # 한화생명 PDF 원본
artifacts/                      # 파싱 결과 JSON
output/                         # 파싱 출력
scripts/                        # 유틸리티 스크립트
```

## 기술 스택

- **PDF 추출**: PyMuPDF
- **LLM**: OpenRouter (Qwen3-235B-A22B, 오픈소스)
- **임베딩**: Multilingual-E5-Large (오픈소스, 90+언어)
- **벡터 DB**: ChromaDB (로컬)
- **UI**: Streamlit
- **데이터 모델**: Pydantic v2
