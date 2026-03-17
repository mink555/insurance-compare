---
title: 라이나 인사이트 - 보험 상품 분석 솔루션
short_description: 라이나생명 암보험 특약 비교 분석 플랫폼
emoji: ⚖️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
---

# 보험 특약 비교 AI 솔루션

라이나생명 사내 전용 보험 특약 비교 플랫폼. PDF 상품요약서를 자동 파싱하여 당사·타사 특약을 구조화된 데이터로 비교합니다.

---

## 목차

1. [시스템 흐름](#1-시스템-흐름)
2. [레이어 구조](#2-레이어-구조)
3. [핵심 로직 상세](#3-핵심-로직-상세)
   - 3-1. PDF 파싱 (Stage 1)
   - 3-2. 정규화 (Stage 2)
   - 3-3. 분류 (Stage 3)
   - 3-4. Export / Dedup (Stage 4)
   - 3-5. LLM Enrichment (Stage 5, Optional)
   - 3-6. 매칭 로직 (canonical\_key)
   - 3-7. 비교 엔진 (compare\_rules)
4. [설정 파일 관리 포인트](#4-설정-파일-관리-포인트)
5. [운영 관리 포인트](#5-운영-관리-포인트)
6. [로컬 실행](#6-로컬-실행)
7. [배포](#7-배포)

---

## 1. 시스템 흐름

### 1-1. 사용자 관점 흐름

```
사용자
  │
  ├─ [Step 1: 상품 선택]
  │    사이드바에서 당사·타사 상품 선택
  │    새 회사 PDF 업로드 (선택적)
  │
  ├─ [Step 2: 비교 분석]
  │    ① 보장 범위 비교 카드 (질병 분류별 보장 건수)
  │    ② 지급 조건 비교 카드 (슬롯별 우위 판정)
  │    ③ 급부별 금액 비교 카드 (전체 급부 금액 나란히)
  │    → "리포트 생성" 버튼
  │
  └─ [Step 3: 리포트]
       §1 전략적 요약
       §2 핵심 비교내용 (Evidence [E1], [E2]...)
       §3 차별점 심층분석
       §4 특약 상품 개발 제언
       부록: Evidence 목록
       [Markdown 다운로드] [CSV 다운로드]
```

### 1-2. 백엔드 파이프라인 흐름

```
PDF 업로드
    │
    ▼
[Stage 1: Parse]  ← 회사별 파서 레지스트리 (fallback: GenericSummaryParser)
    │  출력: raw dict {contracts: [{benefits: [...]}]}
    │
    ▼
[Stage 2: Normalize]  ← 회사/상품 무관 공통 구조로 변환
    │  출력: CanonicalBenefit[]
    │  • 급부명 정규화
    │  • AmountEntry 분리 (복수 금액 조건 처리)
    │
    ▼
[Stage 3: Classify]  ← benefit_category_keywords.json
    │  출력: CanonicalBenefit[].benefit_category 채움
    │  • 진단/수술/치료/입원/통원/사망 등
    │  • other 비율 > 5% 시 WARNING 로그
    │
    ▼
[Stage 4: Export]  ← SummaryRow (flat DataFrame 행)
    │  출력: SummaryRow[]  →  JSON artifact 저장
    │  • dedupe_key 생성 (중복 방지)
    │  • bundle_status 기록 (BOTH/SUMMARY_ONLY/TERMS_ONLY)
    │
    ▼
[Stage 5: Enrich]  ← OPENROUTER_API_KEY 있을 때만 (Optional)
    │  출력: SummaryRow[].slots 채움
    │  • trigger / start_condition / payment_freq /
    │    payment_limit / reduction_rule 슬롯 LLM 추출
    │  • 실패 시 slots=None, 파이프라인 계속 진행
    │
    ▼
[artifacts/prebuilt_riders.json]  또는  [artifacts/upload_*.json]
    │
    ▼
[비교 분석 요청 시]
    │
    ├─ to_comparison_rows()  ← detail rows → 급부별 1행 집약
    │
    ├─ match_benefits()  ← canonical_key 기반 당사/타사 급부 매칭
    │
    ├─ enrich_rows()  ← 매칭된 쌍만 추가 LLM 슬롯 추출 (Optional)
    │
    └─ build_comparison()  ← 규칙 기반 비교 엔진
         │
         ├─ coverage_summary  → ① 보장 범위 카드
         ├─ slot_table        → ② 지급 조건 카드
         └─ amount_table      → ③ 급부별 금액 카드
```

---

## 2. 레이어 구조

```
term_test_v2/
├── app.py                          # Streamlit 진입점 (CSS + 세션 + 라우팅)
├── views/
│   └── workbench.py                # 전체 UI (Step1~3)
│
├── src/insurance_parser/
│   ├── parse/                      # Stage 1: PDF 파서
│   │   ├── lina_summary_parser.py  # 라이나 전용
│   │   ├── hanwha_summary_parser.py # 한화 전용
│   │   └── product_bundle_parser.py # 레지스트리 + GenericSummaryParser
│   │
│   ├── summary_pipeline/           # Stage 2~4 파이프라인
│   │   ├── models.py               # 데이터 모델 (DocumentBundle, SummaryRow 등)
│   │   ├── pipeline.py             # run_pipeline() 오케스트레이터
│   │   ├── normalizer.py           # Stage2: normalize + export + to_comparison_rows
│   │   ├── classifier.py           # Stage3: benefit_category 분류
│   │   ├── detector.py             # 문서 타입 판별
│   │   └── store.py                # ArtifactStore (JSON 저장/로드)
│   │
│   ├── comparison/                 # 비교 엔진
│   │   ├── normalize.py            # canonical_key + match_benefits
│   │   ├── enrich.py               # Stage5: LLM 슬롯 추출
│   │   └── engine.py               # build_comparison() 규칙 엔진
│   │
│   ├── report/
│   │   └── generator.py            # ComparisonReport + SummaryReportBuilder
│   │
│   └── llm/
│       └── openrouter.py           # OpenRouter API 클라이언트
│
├── config/
│   ├── synonyms.json               # canonical_key 동의어 사전 ← 운영 관리 대상
│   └── compare_rules.json          # 슬롯별 비교 규칙 ← 운영 관리 대상
│
├── insurance_info/
│   └── benefit_category_keywords.json  # Stage3 분류 키워드 ← 운영 관리 대상
│
└── artifacts/
    ├── prebuilt_riders.json        # 사전 파싱된 급부 데이터
    └── upload_*.json               # 업로드 파싱 결과
```

---

## 3. 핵심 로직 상세

### 3-1. PDF 파싱 (Stage 1)

**회사별 전용 파서 레지스트리 방식**

```
register_summary_parser("라이나생명", LinaProductSummaryParser)
register_summary_parser("한화생명",   HanwhaProductSummaryParser)
# 새 회사 추가 시 이 한 줄만 추가하면 됨
```

| 조건 | 동작 |
|------|------|
| 전용 파서 등록됨 | 해당 파서로 파싱 |
| 미등록 | `GenericSummaryParser` (PyMuPDF find_tables fallback) |
| 파싱 실패 | errors 기록 후 계속 진행 (앱 죽지 않음) |

**`GenericSummaryParser`**: PyMuPDF `find_tables()`로 테이블 구조 추출. 헤더에 "급부/보장/지급" 포함 여부로 급부 테이블 판별. 회사 하드코딩 없음.

---

### 3-2. 정규화 (Stage 2)

**`normalize_summary_data()`** → `CanonicalBenefit[]`

- 회사/상품명 무관한 공통 구조로 변환
- 한 셀에 여러 금액/조건이 있을 때 `AmountEntry` 분리
- `amount_detail`: 조건별 금액을 JSON string으로 직렬화

**`to_comparison_rows()`**: detail_rows를 `(benefit_name, insurer)` 기준으로 집약. 복수 AmountEntry가 있는 경우 대표값 + detail 모두 보존.

---

### 3-3. 분류 (Stage 3)

**`benefit_category_keywords.json`** 기반 키워드 매칭

| category | category_ko | 예시 급부 키워드 |
|----------|-------------|-----------------|
| diagnosis | 진단 | 진단, 진단자금, 진단금 |
| surgery | 수술 | 수술, 수술자금, 수술비 |
| treatment | 치료 | 치료, 항암, 방사선 |
| hospitalization | 입원 | 입원, 직접치료입원 |
| outpatient | 통원 | 통원, 외래 |
| death | 사망 | 사망, 사망보험금 |
| other | 기타 | 미분류 |

**운영 포인트**: `other` 비율이 5% 초과 시 자동 WARNING 로그 출력 + 미분류 급부명 목록 기록. 로그 확인 후 `benefit_category_keywords.json`에 키워드 추가.

---

### 3-4. Export / Dedup (Stage 4)

**`dedupe_key`**: `(insurer, product_name, contract_name, benefit_name, amount)` 해시. 동일 파일 재업로드 시 중복 방지.

**`ArtifactStore`**:
- `save_upload()`: file_hash 기반 중복 저장 방지
- `load_all()`: prebuilt + uploads 합산 → dedupe_key 기준 최종 dedup
- 구 포맷(v1.0 배열) / 신 포맷(v1.1 `{_meta, rows}`) 모두 지원

---

### 3-5. LLM Enrichment (Stage 5, Optional)

**조건**: `OPENROUTER_API_KEY` 환경변수 설정 시에만 실행.

**추출 슬롯**:
| 슬롯 | 설명 | 예시 |
|------|------|------|
| trigger | 보험금 지급 사유 | "암 진단확정" |
| start_condition | 보장 개시 조건 | "암보장개시일 이후" |
| payment_freq | 지급 횟수/주기 | "매년 1회" |
| payment_limit | 지급 한도 | "최대 5년" |
| reduction_rule | 감액 규칙 | "1년 이내 50%" |

**Graceful degradation**: API 키 없음 → slots=None, 비교 시 fallback 필드 사용. LLM 응답 파싱 실패 → 해당 행만 slots=None.

**비교 시 슬롯 우선순위**: `slots.payment_limit` > `coverage_limit` raw 필드

---

### 3-6. 매칭 로직 (`canonical_key`)

**핵심 원칙**: 급부명의 의미 슬롯만 추출하여 회사 간 명명 차이를 흡수.

```
급부명 → normalize_text()  → type suffix 제거 → 슬롯 추출 → 키 생성
                                                 condition | disease | action
```

**슬롯 추출 방식**: `synonyms.json` 역방향 맵 기반 **Longest-match**

| 슬롯 | 내용 | 예시 |
|------|------|------|
| condition | 지급 채널/장소/특수조건 | 비급여, 상급종합병원, 초기이외, Ⅱ차 |
| disease | 질병 분류 | 일반암, 갑상선암, 기타피부암갑상선암복합 |
| action | 급부 유형 | 진단, 수술, 항암약물, 입원, 통원 |

**복합 disease 처리**:

`기타피부암·갑상선암 주요치료자금` → disease 슬롯에서 `기타피부암갑상선암복합` (길이=11) > `기타피부암` (길이=5) → **longest-match로 복합 레이블 선택**

**coverage_summary 복합 카운팅**:

`_COMPOSITE_DISEASE_MAP`으로 복합 레이블을 개별 질병으로 확장:
- `기타피부암갑상선암복합` → `["기타피부암", "갑상선암"]` 각각 카운트

**`category_ko` fallback**:

급부명만으로 action 슬롯 추출 불가 시 (`canonical_key("갑상선암", "진단")`) → `_CATEGORY_KO_TO_ACTION["진단"] = "진단"` 사용 → `갑상선암|진단`

**1:N 매칭 처리**: 같은 canonical_key에 여러 급부가 있을 때 금액 유사도(문자 집합 겹침 비율)로 Greedy 매칭.

---

### 3-7. 비교 엔진 (`compare_rules`)

**금액 비교 (`_compare_amounts`)**:

| 상황 | 판정 |
|------|------|
| 금액 다름 | 높은 쪽 우위 |
| 금액 같음, 한쪽만 조건부 | 조건 없는 쪽 우위 |
| 금액 같음, 둘 다 조건부, 조건 다름 | 조건상이 |
| 금액 같음, 둘 다 비조건부 또는 동일 조건 | 동일 |

**대표값 원칙**: `amount_detail`이 있으면 **최대값**을 대표값으로 사용. "상대방이 가장 유리한 조건에서도 우리가 이기는가"를 판단 기준으로 삼음.

**슬롯 비교 (`compare_rules.json`)**:

| 슬롯 | type | 근거 |
|------|------|------|
| amount_display | numeric | 금액은 숫자 직접 비교 |
| payment_limit | limit_numeric | "최대 5년" vs "최대 10년": 단위 같을 때만 숫자 비교, 높을수록 유리 |
| reduction_rule | none_is_better | 감액 조건 없을수록 유리, 있으면 비교불가 |
| payment_freq | display_only | 지급 주기는 방향 없음, 표시만 |
| start_condition | display_only | 보장 개시 조건은 자유 텍스트, 표시만 |
| trigger | display_only | 지급 사유는 자유 텍스트, 표시만 |

**`limit_numeric` 단위 키**:
- `"최대 5년"` → `("년", 5)`
- `"최초 1회"` → `("최초_회", 1)` ← "최초"와 "연간"은 의미 달라 다른 단위
- `"연간 1회"` → `("연간_회", 1)`
- 단위 다른 경우 → `비교불가`

**`조건상이` LLM 재판정**: `resolve_mixed_pairs()` — API 키 있을 때 소비자 관점에서 당사우위/타사우위/대등 재판정.

---

## 4. 설정 파일 관리 포인트

### `config/synonyms.json`

급부 매칭의 핵심 사전. **코드 수정 없이** 이 파일만 편집하면 새 용어 인식 가능.

```json
{
  "action": {
    "수술": ["수술"],
    "관혈수술": ["관혈수술"],          // 개복/관혈 수술 — 별도 canonical
    "내시경수술": ["내시경수술"],       // 내시경 — 별도 canonical
    "항암약물": ["항암약물", "항암화학", ...]
  },
  "disease": {
    "일반암": ["암"],
    "갑상선암": ["갑상선암", "갑상선의악성", "갑상선"],
    "기타피부암갑상선암복합": [         // 복합 급부 — disease 슬롯에 있어야 longest-match 동작
      "기타피부암갑상선암",
      "기타피부암∙갑상선암", ...
    ]
  },
  "condition": {
    "비급여": ["비급여", "전액본인부담포함"],
    "상급종합병원": ["상급종합병원"],
    "Ⅱ차": ["Ⅱ"]                      // 특약 2차 버전 구분
  }
}
```

**추가 시 주의**:
- **Longest-match**로 동작하므로 구체적인 변형일수록 variant를 길게 작성
- 복합 disease는 반드시 `disease` 슬롯에 추가 (condition 아님)
- 동의어 추가 후 앱 재시작 시 자동 리로드 (파일 변경만으로 적용)

### `config/compare_rules.json`

슬롯별 비교 방향 설정. 새 슬롯 추가 시 여기에도 등록 필요.

| type | 사용 시 |
|------|---------|
| `numeric` | 금액 숫자 직접 비교 |
| `limit_numeric` | "최대 N년/회" 패턴 수치 비교 |
| `none_is_better` | 없는 쪽이 유리 (감액, 면책 등) |
| `display_only` | 비교 방향 없음, 표시만 |
| `rank` | 순위 리스트 기반 비교 |

### `insurance_info/benefit_category_keywords.json`

Stage3 분류 키워드. 새 보험 종류 추가 시 미분류 급부가 발생하면 여기에 키워드 추가.

---

## 5. 운영 관리 포인트

### 정기 점검 항목

| 항목 | 확인 방법 | 조치 |
|------|----------|------|
| `other` 비율 | 업로드 후 Streamlit 로그 | `benefit_category_keywords.json` 키워드 추가 |
| 매칭 누락 | Step2 "당사단독/타사단독" 건수 비정상 증가 | `synonyms.json` 변형 추가 |
| 과집약 | 다른 의미의 급부가 같은 key로 매칭 | `synonyms.json`에서 별도 canonical 분리 |
| 금액 비교 오류 | `조건상이` 건수 과다 | `compare_rules.json` 또는 `_parse_limit` 로직 점검 |

### 신규 보험사 추가 절차

```
1. lina_summary_parser.py를 참고하여 new_parser.py 작성
2. parse/__init__.py에서 register_summary_parser() 호출 추가
3. 상품요약서 PDF 업로드 → 파싱 결과 확인
4. canonical_key 생성 결과 검토 (오탐/누락 확인)
5. synonyms.json에 신규 용어 추가
6. artifacts/prebuilt_riders.json 갱신 (스크립트: scripts/parse_linalife_summaries.py 참고)
```

### `artifacts/prebuilt_riders.json` 갱신

사전 파싱된 데이터. 신규 상품 추가 또는 데이터 정정 시 재생성.

```bash
# 예시: 라이나 전체 상품 재파싱
python scripts/parse_linalife_summaries.py
```

### LLM 비용 관리

| 시점 | 설명 | 제어 |
|------|------|------|
| 업로드 직후 | 전체 급부 enrichment | `OPENROUTER_API_KEY` 미설정 시 스킵 |
| 비교 분석 시 | 매칭된 쌍만 추가 enrichment | 세션 캐시(`wb_slots_cache`)로 중복 호출 방지 |
| 조건상이 재판정 | `resolve_mixed_pairs()` | 조건상이 건수가 0이면 호출 없음 |

---

## 6. 로컬 실행

```bash
# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정 (선택: LLM enrichment 사용 시)
cp .env.example .env
# OPENROUTER_API_KEY=sk-or-...

# 실행
streamlit run app.py
```

---

## 7. 배포

### Hugging Face Spaces

```bash
git push github main   # GitHub push
git push hf main       # HF Spaces 자동 빌드 트리거
```

**빌드 후 반영 시간**: 통상 1~3분.

**환경변수 설정** (HF Spaces → Settings → Repository secrets):
- `OPENROUTER_API_KEY` — LLM enrichment 활성화 시 필수
- `ARTIFACT_DIR` — artifact 저장 경로 (기본값: `./artifacts`)

---

## 아키텍처 판단 근거

### "왜 RAG가 아닌 정적 사전(synonyms.json)인가?"

| 비교 항목 | RAG | 정적 사전 |
|----------|-----|---------|
| 재현성 | 매번 다를 수 있음 | 항상 동일 |
| 속도 | 임베딩 + 검색 필요 | O(1) 해시맵 |
| 관리 비용 | 인덱스 갱신 필요 | 파일 한 줄 추가 |
| 도메인 정확도 | 일반 LLM 기준 | 보험 도메인 특화 |
| 오류 추적 | 어려움 | canonical_key 로그로 즉시 확인 |

보험 도메인의 급부명은 **정형화된 패턴**이 있고, 이미 알려진 용어 집합이 유한합니다. 신규 용어는 synonyms.json 한 줄 추가로 즉시 반영됩니다.

### "왜 LLM 없이 규칙 기반 비교인가?"

비교 판정은 **재현성**이 중요합니다. 동일 데이터를 비교했을 때 항상 같은 결과가 나와야 합니다. LLM은 슬롯 추출(비정형 텍스트 → 구조화) 단계에서만 사용하고, 판정 자체는 `compare_rules.json`의 규칙으로 처리합니다.

LLM은 판정이 애매한 `조건상이` 케이스에 대해서만 **후처리로** 우위 판정에 관여합니다 (`resolve_mixed_pairs`).
