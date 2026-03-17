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
  └─ 4. Compare    canonical_key 매칭 → 규칙 엔진 우위 판정 → LLM 보조 분석
                                                                    ↓
                                                               Streamlit 리포트
```

**3단계 UI**: 설정 → 비교 (보장범위 / 지급조건 / 급부금액) → 리포트 (전략요약 / 심층분석 / Evidence 부록)

---

## 우위 판정 기준

| 상황 | 판정 |
|------|------|
| 금액·조건 동일 | 동일 |
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
  synonyms.json                 급부명 동의어 사전
  compare_rules.json            슬롯별 비교 규칙

insurance_info/
  kcd9_cancer_codes.json          KCD9 암 코드 (C00-C97, D00-D48)
  benefit_category_keywords.json  급부 카테고리 키워드
  2026년_생명보험표준약관.txt       생명보험 표준약관 (2025.3.31 기준)
  보험_리서치.txt                  암 분류 체계 리서치 (일반암/소액암/유사암 분류 기준)
```

---

## `synonyms.json` — 급부명 동의어 사전

특약명을 `disease | action | condition | type` 4개 슬롯으로 분해해 `canonical_key`를 만드는 데 사용한다.  
보험사마다 같은 개념을 다르게 표기하는 경우를 커버한다.

```json
"갑상선암": ["갑상선암", "갑상선의악성"]
"항암약물": ["항암약물", "항암화학", "항암치료약물"]
"비급여":   ["비급여", "전액본인부담포함", "전액본인부담"]
```

**RAG 대신 정적 사전을 쓰는 이유**  
암보험 특약명은 표준약관과 KCD 코드가 용어를 규정하는 닫힌 도메인이다.  
나올 수 있는 표현의 범위가 제한적이므로, 벡터 유사도 기반 확률적 매칭보다  
명시적 규칙 기반 매칭이 정확도·디버깅 용이성·재현성 모두 유리하다.

**초기 생성 방법**  
아래 3개 파일을 도메인 지식으로 LLM에 제공해 동의어 관계를 분류하고 JSON으로 생성했다.

| 파일 | 역할 |
|------|------|
| `kcd9_cancer_codes.json` | disease 카테고리 근거 (C44=기타피부암, C73=갑상선암, D00-D09=제자리암 등) |
| `2026년_생명보험표준약관.txt` | action 카테고리 근거 (수술·치료·검사 행위 정의) |
| `보험_리서치.txt` | 일반암/소액암/유사암 분류 체계 |

---

## 신규 회사/상품 추가

1. **파서 작성** — `src/insurance_parser/parse/`에 새 파서 작성, `utils.py` 공통 유틸 재사용
2. **레지스트리 등록** — `product_bundle_parser.py`의 `_PARSERS` dict에 추가
3. **동의어 등록** — 급부명 표기가 다를 경우 `config/synonyms.json`에 추가
4. **카테고리 추가** — 신규 보험 종류면 `benefit_category_keywords.json`에 섹션 추가 (코드 수정 불필요)

---

## 운영 관리

### 이상 증상 → 원인

| 증상 | 원인 |
|------|------|
| 같은 급부인데 단독 판정 | `synonyms.json` 동의어 누락 |
| 금액이 같은데 우위 판정 | `amount_detail` 파싱 오류 |
| 새 보험 종류 급부가 전부 `기타` | `benefit_category_keywords.json` 키워드 미등록 |
| 다른 급부가 같은 키로 매칭 | `synonyms.json` 매핑 과도 |

### `synonyms.json` 수정이 필요한 시점

**새 상품 파싱 시**
- 매칭 안 된 특약명 확인 → `kcd9_cancer_codes.json` 또는 표준약관에서 분류 확인 → 해당 key의 variants에 추가
- 영향 범위: `action` + `disease` 카테고리

**KCD 코드 개정 시** (대개정 5~10년, 소개정 수시)
- `kcd9_cancer_codes.json` 업데이트 → disease 분류가 바뀐 항목만 `synonyms.json` 반영
- 영향 범위: `disease` 카테고리만

**표준약관 개정 시** (금융위 고시, 연 1~2회)
- `2026년_생명보험표준약관.txt` 최신본으로 교체 → 변경된 용어를 `synonyms.json`에 반영
- 영향 범위: `action` 카테고리 (주로 신규 치료법 편입 시)

### 수정 후 확인 사항

수정할 때마다 아래 케이스가 여전히 정상인지 확인한다.

```
남성난임  ≠  남성특화암   (disease key가 다른지)
재건수술  ≠  수술         (별도 key인지)
갑상선의악성  →  갑상선암  (variant가 올바른 key로 매핑되는지)
```

---

## 기술 스택

| 역할 | 기술 |
|------|------|
| PDF 추출 | PyMuPDF |
| LLM | OpenRouter → Qwen3-235B |
| 벡터 DB | ChromaDB |
| UI | Streamlit |
| 배포 | Docker (Hugging Face Spaces) |
