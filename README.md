---
title: 라이나 인사이트 - 보험 상품 분석 솔루션
short_description: 라이나생명 암보험 특약 비교 분석 플랫폼
emoji: ⚖️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
---

# 보험 특약 비교 분석 솔루션

라이나생명 사내 전용 암보험 특약 비교 플랫폼.  
PDF 상품요약서를 파싱해 당사·타사 특약을 구조화된 데이터로 비교하고, 인사이트 요약과 리포트를 생성함.

---

## 한눈에 보기

| 항목 | 내용 |
|------|------|
| 입력 | 라이나·타사 상품요약서 PDF |
| 출력 | 급부별 금액 비교, 보장 범위 비교, 인사이트 카드, Markdown/CSV 리포트 |
| 매칭 방식 | 급부명 → `condition\|disease\|action` 슬롯 추출 → canonical_key 비교 |
| LLM 사용 범위 | 슬롯 구조화(선택), 조건상이 재판정(선택) — 비교 판정 자체는 규칙 기반 |
| 운영 관리 파일 | `config/synonyms*.json`, `config/compare_rules.json` |
| 신규 회사 추가 | synonyms 파일 추가 + `python -m tools.check_gaps` |

**왜 같은 보장인데 이름이 다른가**  
표준약관(금감원 고시)은 계약 절차만 표준화함. 급부명·암 분류 기준·지급 조건은 각사 자유 설계.

실제로 라이나와 한화의 같은 보장이 이렇게 달리 표현됨:

| 라이나생명 | 한화생명 | 매칭 결과 |
|-----------|---------|---------|
| 표적항암약물허가치료보험금 | 표적항암약물허가치료자금 | ✅ 매칭 (`표적항암약물`) |
| 암직접치료급여금 | 암(유방암/전립선암제외)치료보험금 | ✅ 매칭 (`일반암\|치료`) |
| 암직접치료상급종합병원통원급여금 | 암직접치료통원급여금 | ✅ 매칭 (`상급종합병원\|통원`) |

급부명에서 의미 슬롯만 추출해 회사 간 명명 차이를 흡수함.

---

## 목차

1. [사용자 흐름](#1-사용자-흐름)
2. [백엔드 파이프라인](#2-백엔드-파이프라인)
3. [엔진 상세](#3-엔진-상세)
4. [설정 파일 — 운영 관리 포인트](#4-설정-파일--운영-관리-포인트)
5. [신규 보험사 추가 절차](#5-신규-보험사-추가-절차)
6. [로컬 실행 및 배포](#6-로컬-실행-및-배포)
7. [아키텍처 판단 근거](#7-아키텍처-판단-근거)

---

## 1. 사용자 흐름

```
Step 1 │ 상품 선택
       │  사이드바에서 당사(라이나) · 타사 상품 선택
       │  또는 새 PDF 파일 직접 업로드
       │
Step 2 │ 비교 분석 (3개 카드)
       │  ① 보장 범위 — 질병 분류별 보장 건수 (당사 vs 타사)
       │  ② 지급 조건 — 지급한도·감액규정 등 조건 우위 판정
       │  ③ 급부별 금액 — 매칭된 급부 금액 나란히 비교
       │  → 인사이트 카드 (한 줄 포지션 + 핵심 포인트 자동 생성)
       │
Step 3 │ 리포트 생성
         §1 전략적 요약
         §2 핵심 비교내용 (약관 근거 [E1][E2]... 포함)
         §3 차별점 심층분석
         §4 특약 상품 개발 제언
         부록: 약관 원문 근거 목록
         [Markdown 다운로드]  [CSV 다운로드]
```

---

## 2. 백엔드 파이프라인

PDF 업로드 시 5단계를 거쳐 비교 가능한 데이터가 됨.

```
PDF
 │
 ▼
[Stage 1: 파싱]
 회사별 전용 파서로 급부 테이블 추출
 → raw 구조체 {contracts: [{benefits: [...]}]}
 │
 ▼
[Stage 2: 정규화]
 회사·상품 무관한 공통 구조(CanonicalBenefit)로 변환
 복수 금액/조건이 한 셀에 있으면 행으로 분리
 │
 ▼
[Stage 3: 분류]  ← 파싱 결과에 benefit_category 필드 기록 (내부 참고용)
 │
 ▼
[Stage 4: 저장]
 SummaryRow(flat 1행) 변환 → JSON artifact 저장
 dedupe_key로 중복 방지
 │
 ▼
[Stage 5: LLM 슬롯 추출]  ← API 키 있을 때만 (선택적)
 trigger / start_condition / payment_freq /
 payment_limit / reduction_rule 추출
 실패해도 파이프라인 계속 진행
 │
 ▼
[비교 분석 요청 시]
 ① to_comparison_rows()  — 급부별 1행 집약
 ② match_benefits()      — canonical_key 기반 당사/타사 매칭
 ③ build_comparison()    — 규칙 기반 비교 엔진
    └─ coverage_summary  → ① 보장 범위 카드
    └─ slot_table        → ② 지급 조건 카드
    └─ amount_table      → ③ 급부별 금액 카드
    └─ insight           → 인사이트 카드
```

### 파일 구조

```
term_test_v2/
├── app.py                            # 진입점 (CSS + 세션 + 라우팅)
├── views/workbench.py                # 전체 UI
│
├── src/insurance_parser/
│   ├── parse/                        # Stage 1: PDF 파서
│   │   ├── lina_summary_parser.py    # 라이나 전용
│   │   ├── hanwha_summary_parser.py  # 한화 전용
│   │   └── product_bundle_parser.py  # 레지스트리 + GenericSummaryParser
│   │
│   ├── summary_pipeline/             # Stage 2~4
│   │   ├── models.py                 # 데이터 모델
│   │   ├── pipeline.py               # 오케스트레이터
│   │   ├── normalizer.py             # Stage 2: 정규화 + to_comparison_rows
│   │   ├── classifier.py             # Stage 3: benefit_category 분류 (내부용)
│   │   ├── detector.py               # 문서 타입 판별
│   │   └── store.py                  # ArtifactStore (JSON 저장/로드)
│   │
│   ├── comparison/
│   │   ├── normalize.py              # canonical_key + match_benefits + action_from_row
│   │   ├── enrich.py                 # Stage 5: LLM 슬롯 추출
│   │   └── engine.py                 # build_comparison() 비교 엔진
│   │
│   ├── report/generator.py           # 리포트 빌더
│   └── llm/openrouter.py             # OpenRouter API 클라이언트
│
├── config/                           # ← 운영 관리 대상
│   ├── synonyms.json                 # 급부 매칭 동의어 사전 (generic)
│   ├── synonyms_한화생명.json         # 한화 특유 표현
│   ├── synonyms_라이나생명.json        # 라이나 특유 표현 (현재 비어있음)
│   └── compare_rules.json            # 슬롯별 비교 규칙
│
├── insurance_info/                   # 참고 데이터
│   ├── benefit_category_keywords.json  # Stage 3 분류 키워드 (내부용)
│   ├── 2026년_생명보험표준약관.txt       # 법적 용어 정의 참고
│   ├── 보험_리서치.txt                  # 치료 행위 분류 프레임 참고
│   └── kcd9_cancer_codes.json         # KCD 질병코드 참고
│
├── tools/
│   └── check_gaps.py                 # 신규 회사 추가 시 갭 탐지 CLI
│
└── artifacts/
    ├── prebuilt_riders.json           # 사전 파싱된 급부 데이터 (라이나+한화)
    └── upload_*.json                  # 사용자 업로드 파싱 결과
```

---

## 3. 엔진 상세

### 3-1. 급부 매칭 — canonical_key

급부명에서 의미 슬롯만 추출해서 비교. 회사명·상품명·type 접미사는 버림.

```
급부명 원문
  → 공백·괄호·특수문자 제거
  → type 접미사 제거 (자금/보험금/급여금)
  → synonyms.json 기반 longest-match로 슬롯 추출
  → canonical_key = "condition|disease|action"
```

실제 데이터 기준 변환 예시:

| 원문 급부명 | canonical_key | 비고 |
|-----------|--------------|------|
| 비급여(전액본인부담포함) 항암약물·방사선치료자금 | `비급여\|항암방사선` | 조건·행위만 남음 |
| 기타피부암∙갑상선암 주요치료자금 | `기타피부암갑상선암복합\|치료` | 복합 disease 처리 |
| 갑상선암진단자금 | `갑상선암\|진단` | type 접미사(자금) 제거 |
| 급여 NGS유전자패널검사비용 | `급여\|NGS유전자패널검사` | condition + action |
| 암 주요검사비용지원금 | `일반암\|주요검사` | '암' → 일반암, '주요검사비용' → 주요검사 |
| 표적항암약물허가치료보험금 | `표적항암약물` | disease 없음 (action만) |

슬롯 3종:

| 슬롯 | 의미 | 예시 값 | 설계 이유 |
|------|------|--------|---------|
| condition | 지급 조건/채널 | 비급여, 상급종합병원, 3기이상 | 급여 여부·병원 등급이 다르면 별도 급부로 취급해야 함 |
| disease | 질병 분류 | 일반암, 갑상선암, 기타피부암갑상선암복합 | 암 분류 기준이 상품 경쟁력의 핵심 차이 |
| action | 급부 유형 (44종) | 진단, 수술, 항암약물, 검사, 중환자실 | 보장 행위가 다르면 다른 급부 |

- **Longest-match**: `기타피부암갑상선암복합`(11자)과 `기타피부암`(5자)이 동시에 매칭될 때 짧은 것을 고르면 복합 급부가 개별 disease로 쪼개지는 오탐 발생. 긴 것 우선.
- **action 오탐 방지**: `항암약물` 안에 `암`이 있어 disease `일반암`으로 오탐 가능. disease 슬롯 추출 전 action variant를 먼저 마스킹함 (`_mask_action_variants`).  
  → `비급여 항암약물방사선치료자금` : `항암약물`을 마스킹 후 disease 탐색 → `일반암` 오탐 없이 `비급여|항암방사선` 정확히 추출
- **1:N 매칭**: 같은 canonical_key에 여러 급부가 있으면 금액 유사도(문자 집합 겹침 비율)로 greedy 매칭.

---

### 3-2. synonyms.json — 동의어 사전 구성

출처 3종:

| 출처 | 활용 |
|------|------|
| 라이나+한화 PDF 파싱 결과 (귀납 수집) | action·disease 슬롯 variants 직접 추출 |
| `2026년_생명보험표준약관.txt` (금감원 고시) | 법적 동의어 — 제자리암 = 상피내암, 갑상선의악성 등 |
| `보험_리서치.txt` | 치료 행위 분류 프레임 — 항암약물·방사선·수술 등 카테고리 틀 |

파일 구조 (3-레이어):

```
config/synonyms.json           ← generic: 업계 공통 표현
config/synonyms_한화생명.json   ← 한화 특유 복합 disease
config/synonyms_라이나생명.json  ← 라이나 특유 표현 (현재 비어있음)
```

로딩 시 generic + 회사별 파일 자동 병합.  
새 파일(`synonyms_삼성생명.json`)을 추가하면 코드 수정 없이 반영됨.

현재 coverage (라이나+한화 기준):

| 슬롯 | 카테고리 수 | 예시 |
|------|----------|------|
| action | 44종 | 진단, 수술, 항암약물, 표적항암약물, PET검사, 중환자실, 남성난임 |
| disease | 36종 | 일반암, 갑상선암, 기타피부암갑상선암복합, 유방전립선암 |
| condition | 10종 | 비급여, 급여, 상급종합병원, 3기이상, 4기 |

한계: 현재 라이나+한화 데이터 기반이라 다른 회사 특유 표현(예: `HER2양성유방암`)은 별도 추가 필요.  
→ `python -m tools.check_gaps --insurer <회사명>` 으로 갭 탐지 가능.

회사별 특유 표현 사례 (`synonyms_한화생명.json`에서):

```json
"disease": {
  "기타피부암갑상선암복합": [
    "기타피부암갑상선암",
    "기타피부암∙갑상선암",
    "기타피부암·갑상선암"
  ],
  "유방전립선암": ["유방암전립선암", "유방전립선암"],
  "갑상선암전립선암": ["갑상선암및전립선암"]
}
```

→ `기타피부암∙갑상선암 주요치료자금`을 `기타피부암갑상선암복합|치료`로 정확하게 매칭하기 위한 항목.

---

### 3-3. 비교 엔진 — compare_rules.json

금액 비교와 슬롯 비교 두 가지로 구성.

금액 비교:

| 상황 | 판정 | 근거 |
|------|------|------|
| 금액 다름 | 높은 쪽 우위 | 보험금은 클수록 유리 |
| 금액 같음, 한쪽만 조건부 | 조건 없는 쪽 우위 | 무조건 지급 > 조건부 지급 |
| 금액 같음, 둘 다 조건부, 조건 다름 | 조건상이 | 수치 비교 불가 — LLM 후처리로 재판정 가능 |
| 금액 같음, 조건도 같음 | 동일 | |

실제 비교 결과 사례 (라이나 vs 한화):

| 급부명 (canonical_key) | 라이나 | 한화 | 판정 |
|----------------------|-------|------|------|
| 표적항암약물허가치료보험금 (`표적항암약물`) | **3,000만원** | 1년미만 500만원 / 1년이상 1,000만원 | 당사우위 |
| 암직접치료통원급여금 (`통원`) | **3만원** | 1회당 1만원 | 당사우위 |
| 암직접치료입원급여금 (`일반암\|입원`) | **5만원/일** | 1년미만 ~1만원 / 1년이상 ~2만원 | 당사우위 |
| 갑상선암다빈치로봇수술급여금 (`갑상선암\|로봇수술`) | 500만원 | **1년이상 1,000만원** | 타사우위 |
| 암수술급여금 (`일반암\|수술`) | 100만원 | **1회당 500만원** | 타사우위 |

한 급부에 여러 금액 조건이 있으면 최대값을 대표값으로 씀.  
("상대방이 가장 유리한 조건에서도 우리가 이기는가"를 기준으로 삼기 위함)

슬롯 비교:

| 슬롯 | 비교 방식 | 이유 |
|------|---------|------|
| payment_limit | limit_numeric | "최대 5년" vs "최대 10년" — 숫자 비교 가능 |
| reduction_rule | none_is_better | 감액 조건 없는 쪽이 소비자에게 유리 |
| payment_freq | 표시만 | "매년 1회" vs "최초 1회" — 우열 기준 없음 |
| trigger | 표시만 | 지급 사유는 자유 텍스트, 방향 없음 |

실제 슬롯 비교 사례:

| 슬롯 | 급부명 | 라이나 | 한화 | 판정 |
|------|-------|-------|------|------|
| payment_limit | 암직접치료급여금 | 최대 5년 | 최대 10년 | 타사우위 |
| payment_limit | 표적항암약물허가치료보험금 | 최초 1회 | 최대 10년 | 비교불가 (단위 다름) |
| amount_display | 특정면역항암약물허가치료보험금 | **3,000만원** | 1년미만 500만원 | 당사우위 |

`limit_numeric` 단위 처리:

```
"최대 5년"  → ("년", 5)
"최초 1회"  → ("최초_회", 1)   ← "최초"와 "연간"은 의미가 달라 별도 단위
"연간 1회"  → ("연간_회", 1)
단위 다름   → 비교불가
```

---

### 3-4. 인사이트 요약

인사이트 카드(포지션·Key Points·갭 분석)는 LLM 없이 규칙으로만 생성.

LLM이 데이터에 없는 내용을 만들면 의사결정에 직접 영향을 미치므로, 추출된 수치에서 집계만 함.

- 포지션: 당사 우위 건수 vs 타사 우위 건수 → 수치 기반 판정
- 핵심 포인트: 금액 갭 상위 3건 + 단독 보장 카테고리 요약
- 카테고리별 점수: action 슬롯 기준 집계

실제 데이터(라이나 vs 한화) 기준 인사이트 예시:

```
포지션: 당사우위 15건 / 타사우위 3건 / 동일 1건
        → 매칭 19건 중 당사 우세

단독 보장 갭:
  당사(라이나) 단독: 72건
    예) 유방암/전립선암 치료보험금 500만원, 갑상선암치료보험금 500만원,
        기타피부암치료보험금 500만원 등 — 암 분류별 세분 보장

  타사(한화) 단독: 224건
    예) 암통원자금 1회당 2만원, 항암중입자방사선치료자금 500만원,
        갑상선암로봇수술자금 500만원 등 — 일상 치료 지원 급부 다수
```

---

### 3-5. LLM 사용 범위

| 단계 | LLM | 비고 |
|------|-----|------|
| PDF 파싱 | ❌ | PyMuPDF 규칙 기반 |
| canonical_key 매칭 | ❌ | synonyms.json 사전 기반 |
| 금액·슬롯 비교 | ❌ | compare_rules.json 규칙 기반 |
| 인사이트 생성 | ❌ | 추출 데이터 집계 기반 |
| 슬롯 구조화 | ✅ (선택) | 비정형 약관 텍스트 → trigger·감액규정 추출 |
| 조건상이 재판정 | ✅ (선택) | 애매한 조건 비교 → 소비자 관점 우위 판정 |

`OPENROUTER_API_KEY` 없이도 모든 기능 동작.

---

## 4. 설정 파일 — 운영 관리 포인트

코드 수정 없이 파일만 편집하면 됨. 수정 대상은 3개.

### `config/synonyms.json` + `config/synonyms_<회사명>.json`

새 보험사 급부명이 매칭 안 될 때 수정.

```bash
python -m tools.check_gaps                    # 전체 데이터 갭 탐지
python -m tools.check_gaps --insurer 삼성생명  # 특정 회사만
```

갭이 발견되면 해당 회사 파일에 variants 추가:

```json
{
  "_insurer": "삼성생명",
  "disease": {
    "HER2양성유방암": ["HER2양성유방암", "HER2유방암"]
  }
}
```

주의사항:
- 복합 disease(기타피부암+갑상선암 묶음 등)는 `disease` 슬롯에 추가 (condition이 아님)
- longest-match로 동작하므로 variant를 길게 쓸수록 정밀하게 매칭됨
- 파일 저장 후 앱 재시작 없이 자동 반영

### `config/compare_rules.json`

비교 방향이 바뀌거나 새로운 슬롯이 추가될 때 수정.

| type | 사용 시 |
|------|---------|
| `numeric` | 금액 숫자 직접 비교 |
| `limit_numeric` | "최대 N년/회" 패턴 수치 비교 |
| `none_is_better` | 없는 쪽이 유리 (감액, 면책 등) |
| `display_only` | 비교 방향 없음, 표시만 |

### `insurance_info/benefit_category_keywords.json`

PDF 파싱 결과에 `benefit_category` 필드를 기록하는 내부 참고용 분류.  
비교·집계 로직에서는 미사용 (canonical_key action 슬롯으로 대체됨).  
파싱 로그 및 데이터 감사 용도로만 유지.

---

## 5. 신규 보험사 추가 절차

### Step 1 — 파서 작성

`lina_summary_parser.py`를 참고해 `<회사명>_summary_parser.py` 작성 후 레지스트리 등록:

```python
# parse/__init__.py
register_summary_parser("삼성생명", SamsungSummaryParser)
```

GenericSummaryParser(PyMuPDF `find_tables` 기반)로도 대부분의 PDF 구조 처리 가능.  
전용 파서 없이도 기본 동작은 됨.

### Step 2 — 갭 탐지

PDF 업로드 파싱 후 canonical_key 매핑 갭 확인:

```bash
python -m tools.check_gaps --insurer 삼성생명
```

출력 예시:
```
[1] synonyms 미매칭 (canonical_key = 원문 그대로): 5건
  [삼성생명] 'HER2양성유방암 진단보험금'
         canonical_key='HER2양성유방암진단보험금'
```

### Step 3 — synonyms 파일 추가

`config/synonyms_삼성생명.json` 생성 후 갭 항목 추가.  
코드 수정 없이 파일만 추가하면 자동 로딩됨.

### Step 4 — prebuilt 데이터 갱신

```bash
python scripts/parse_linalife_summaries.py  # 라이나 전체 재파싱
# 또는 앱에서 직접 업로드
```

---

## 6. 로컬 실행 및 배포

### 로컬 실행

```bash
pip install -r requirements.txt

# LLM enrichment 사용 시 (선택)
cp .env.example .env
# OPENROUTER_API_KEY=sk-or-...

streamlit run app.py
```

### 배포 (Hugging Face Spaces)

```bash
git push github main   # GitHub 백업
git push origin main   # HF Spaces 빌드 트리거 (통상 1~3분)
```

HF Spaces 환경변수 (Settings → Repository secrets):
- `OPENROUTER_API_KEY` — LLM enrichment 활성화 시 필요
- `ARTIFACT_DIR` — artifact 저장 경로 (기본값: `./artifacts`)

---

## 7. 아키텍처 판단 근거

### RAG가 아닌 정적 사전(synonyms.json)으로 매칭하는 이유

보험 급부명은 정형화된 도메인 언어. `[조건] + [disease] + [행위] + [type 접미사]` 패턴에서 크게 벗어나지 않음.

| 항목 | LLM·벡터 검색 | 정적 사전 |
|------|-------------|---------|
| 재현성 | 매번 다를 수 있음 | 항상 동일 |
| 속도 | 임베딩 + 검색 필요 | O(1) 해시맵 조회 |
| 오류 추적 | 어려움 | canonical_key 로그로 즉시 확인 |
| 신규 용어 대응 | 재학습 또는 인덱스 갱신 | 파일 한 줄 추가 |
| 도메인 정확도 | 일반 언어 기준 | 보험 도메인 특화 |

신규 용어 누락은 `tools/check_gaps.py`로 탐지.

### 비교 판정에 LLM을 쓰지 않는 이유

비교 결과는 영업 판단과 상품 개발에 직접 쓰임.  
같은 데이터를 비교했을 때 항상 같은 결과가 나와야 함.

LLM은 "비정형 텍스트 → 구조화" 단계(슬롯 추출)에서만 쓰고,  
판정은 `compare_rules.json` 규칙으로 처리.

단, 조건이 서로 달라 수치 비교가 불가능한 `조건상이` 케이스에 한해  
LLM이 후처리로 소비자 관점 우위를 판정함 (`resolve_mixed_pairs`).

### benefit_category_ko를 비교에 쓰지 않는 이유

파싱 시점 keyword 매칭으로 결정되는 `benefit_category_ko`의 문제:

1. keyword 매칭 오분류로 `기타` 카테고리 발생 (전체의 ~10%)
2. canonical_key의 action 슬롯이 이미 44종으로 더 정밀하게 분류 중

실제 분류 비교:

| 급부명 | benefit_category_ko (파싱 시) | action_from_row() (비교 시) |
|-------|----------------------------|-----------------------------|
| 표적항암약물허가치료보험금 | 치료 | 표적항암약물 |
| NGS유전자패널검사비용 | 기타 | NGS유전자패널검사 |
| 암직접치료상급종합병원통원급여금 | 치료 | 통원 |
| 남성난임정자채취지원금 | 기타 | 남성난임 |

→ `benefit_category_ko`의 "치료" 범주는 너무 넓고, "기타" 오분류가 발생해 집계 신뢰도가 낮음.  
action 슬롯은 44종으로 더 세밀하게 구분되어 실제 급부 유형을 정확히 반영함.

비교·집계·UI 표시는 모두 `action_from_row()`(canonical_key에서 action 슬롯 추출)로 처리.  
`benefit_category_ko`는 파싱 결과에만 남겨 데이터 감사 용도로 유지.
