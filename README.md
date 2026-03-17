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

타사 보험 상품 PDF 한 장을 올리면, 당사 상품과 특약을 자동으로 비교하고 근거 기반 리포트를 생성한다.

---

## 이런 문제를 해결합니다

> "갑상선암진단보험금 1,000만원" vs "갑상선의악성진단보험금 1,000만원"  
> — 같은 보장인데 표기가 달라 비교가 안 되는 문제

보험사마다 같은 보장을 다르게 표기한다. 이 시스템은 표기 차이를 자동으로 정규화하고,
보장 범위·지급 조건·금액을 항목별로 나란히 비교해 우위를 판정한다.

---

## 사용 흐름 (비개발자용)

```
① 파일 업로드
   타사 보험 상품 PDF를 올린다.
   (당사 상품은 사전 등록되어 있음)
        │
        ▼
② 자동 분석 (내부 처리, 약 10~30초)
   ┌─────────────────────────────────────────────┐
   │ 1. 읽기     PDF에서 특약 목록·금액 표 추출   │
   │ 2. 정규화   금액 단위 통일, 텍스트 정리      │
   │ 3. 분류     특약을 카테고리별로 묶기         │
   │ 4. 매칭     당사↔타사 같은 보장 항목 연결    │
   │ 5. 판정     항목별 우위·동일·조건상이 결정   │
   └─────────────────────────────────────────────┘
        │
        ▼
③ 리포트 확인
   [보장범위] 당사에만 있는 항목 / 타사에만 있는 항목
   [지급조건] 같은 금액이지만 조건이 다른 항목
   [급부금액] 금액 차이가 있는 항목
        │
        ▼
④ 전략 리포트
   전략 요약 / 심층 분석 / Evidence 부록
```

---

## 우위 판정 기준

| 상황 | 판정 |
|------|------|
| 금액·조건 모두 같음 | 동일 |
| 금액이 다름 | 높은 쪽 우위 |
| 복수 조건이 있는 경우 | 조건상이 — 조건별 금액 전체를 UI에서 직접 확인 |
| 한쪽에만 존재 | 당사단독 / 타사단독 |

---

## 핵심 개념: 특약명 정규화

### 왜 필요한가?

같은 보장인데 보험사마다 표기가 다르다.

```
라이나생명: "갑상선암진단보험금"
한화생명:   "갑상선의악성진단보험금"
         → 둘 다 같은 보장 (KCD C73)
```

이를 자동으로 같은 항목으로 인식하기 위해 **동의어 사전(`config/synonyms.json`)**을 사용한다.

### 동의어 사전은 어떻게 만들었나?

아래 3개 도메인 파일을 LLM에 제공해 동의어 관계를 분류하고 생성했다.

| 파일 | 역할 |
|------|------|
| `insurance_info/kcd9_cancer_codes.json` | 질병 분류 근거 — C44=기타피부암, C73=갑상선암, D00-D09=제자리암 등 KCD 코드 매핑 |
| `insurance_info/2026년_생명보험표준약관.txt` | 행위 분류 근거 — 수술·치료·검사의 법적 정의 |
| `insurance_info/보험_리서치.txt` | 암 분류 체계 — 일반암/소액암/유사암 구분 기준 |

### 왜 AI(RAG) 매칭이 아닌 정적 사전인가?

암보험 특약명은 표준약관과 KCD 코드가 **나올 수 있는 표현을 규정하는 닫힌 도메인**이다.
AI 유사도 매칭은 "남성난임"과 "남성특화암"처럼 유사하지만 **다른 보장을 같은 항목으로 오인**할 수 있다.
정적 사전은 결과가 항상 일정하고, 오류 발생 시 원인을 즉시 파악할 수 있다.

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

## 신규 회사/상품 추가

```
1. 파서 작성        src/insurance_parser/parse/ 에 새 파서 추가
        │
2. 레지스트리 등록  product_bundle_parser.py 의 _PARSERS 에 추가
        │
3. 동의어 등록      config/synonyms.json 에 새 표기 추가
        │
4. 카테고리 추가    신규 보험 종류면 benefit_category_keywords.json 에 섹션 추가
                   (코드 수정 불필요)
```

---

## 운영 관리

### 문제 발생 시 원인 찾기

| 증상 | 원인 | 수정 위치 |
|------|------|----------|
| 같은 보장인데 "단독" 판정 | 동의어 누락 | `config/synonyms.json` |
| 금액 같은데 "우위" 판정 | 금액 파싱 오류 | 해당 회사 파서 |
| 새 상품 특약이 전부 "기타" 분류 | 카테고리 키워드 미등록 | `benefit_category_keywords.json` |
| 다른 보장이 같은 항목으로 매칭 | 동의어 과도 등록 | `config/synonyms.json` |

### `synonyms.json` 업데이트 시점

**새 상품 파싱 후 매칭 누락 발견 시**
```
매칭 안 된 특약명 확인
  → kcd9_cancer_codes.json 또는 표준약관에서 어느 분류인지 확인
  → synonyms.json 해당 항목의 표기 목록에 추가
```

**KCD 코드 개정 시** (대개정 5~10년, 소개정 수시 — 건강보험심사평가원 고시)
```
kcd9_cancer_codes.json 업데이트
  → 질병 분류가 바뀐 항목만 synonyms.json 반영
  → 행위(수술·치료·검사) 카테고리는 KCD와 무관하므로 건드리지 않음
```

**표준약관 개정 시** (금융위 고시, 연 1~2회)
```
2026년_생명보험표준약관.txt 최신본으로 교체
  → 새로 편입된 치료법·행위 용어를 synonyms.json action 항목에 추가
  → 질병(disease) 카테고리는 KCD 기준이므로 여기서 건드리지 않음
```

**수정 후 아래 케이스가 정상인지 반드시 확인**
```
남성난임  ≠  남성특화암    ← 다른 보장이 같은 키로 묶이면 안 됨
재건수술  ≠  수술          ← 다른 보장이 같은 키로 묶이면 안 됨
갑상선의악성  →  갑상선암  ← 표기만 다른 같은 보장은 같은 키로 묶여야 함
```

---

## 프로젝트 구조 (개발자용)

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
    pipeline.py                 파이프라인 오케스트레이터
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
  보험_리서치.txt                  암 분류 체계 리서치 (일반암/소액암/유사암)
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
