---
title: 보험 특약 비교 분석 솔루션
short_description: 보험사 상품요약서 PDF 파싱 · 비교 플랫폼
emoji: ⚖️
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
---

# 보험 특약 비교 분석 플랫폼

보험사 상품요약서 PDF를 파싱해 급부(보장 항목)를 회사 간에 자동 매칭·비교하고 리포트를 생성하는 분석 도구.

---

## 화면 미리보기

**대시보드** — 등록 상품 현황 및 사용 흐름

![대시보드](docs/screenshot_dashboard.png)

**상품 비교 설정** — 당사 vs 타사 상품 선택

![상품 비교 설정](docs/screenshot_compare_setup.png)

**비교 결과 리포트** — Executive Summary · KEY SELLING POINTS 자동 생성

![비교 결과 리포트](docs/screenshot_compare_result.png)

**비교 상세** — 보장 범위·급부별 금액 비교 테이블

![비교 상세](docs/screenshot_compare_detail.png)

---

## 왜 이렇게 만들었나 — 핵심 의사결정 6가지

### 1. PDF 파싱 전략: PyMuPDF `find_tables()` + 좌표 fallback

보험 상품요약서는 표(table) 기반 문서. 텍스트 추출보다 **표 구조를 그대로 가져오는 게** 급부명·금액·지급사유 컬럼을 안정적으로 분리할 수 있음.

**PyMuPDF를 선택한 이유**: 보험 상품요약서는 실선(벡터 선)으로 그려진 표 구조가 대부분. PyMuPDF의 `find_tables()`는 PDF 내부 drawing 오브젝트(rect/line)를 직접 읽어 표 경계를 인식하므로, 이 유형에서 가장 정확. 실제로 80,000페이지 규모 DocLayNet 벤치마크(arXiv 2410.09871)에서 **약관·매뉴얼 계열 문서의 표 탐지 1위가 PyMuPDF**. pdfplumber는 실선 없는 표(공백·들여쓰기 기반)에서 강점이 있지만, 이 프로젝트의 PDF 유형에서는 동등하거나 낮음.

`find_tables()`를 1순위로 사용. 하지만 일부 PDF는 시각적으로 표처럼 보여도 선 없이 텍스트 블록만 배치한 경우가 있음(라이나생명 일부 페이지). 이때는 **x좌표 범위 + y좌표 정렬**로 열을 직접 구성하는 좌표 기반 fallback을 사용.

```
find_tables() 성공 → 표 구조 그대로 사용
find_tables() 실패 → 페이지 텍스트 블록을 x좌표로 컬럼 분류 후 y좌표로 행 정렬
```

임베딩·전체 텍스트 RAG를 쓰지 않은 이유: 보험 급부 비교는 "어떤 항목이 얼마?" 라는 구조화된 질문. 벡터 유사도 검색보다 **정확한 컬럼 파싱 + 수치 비교**가 훨씬 신뢰도 높음.

---

### 2. 청킹 단위: 1 급부명 × 1 금액 조건 = 1행

파싱 후 데이터를 쪼개는 단위가 비교 품질을 결정함. 다음 4단계로 분해:

```
PDF 문서
  └─ 계약/특약 단위  (LinaProductSummaryParser → ContractSectionFinder)
       └─ 급부 행 단위  (is_benefit_table + find_benefit_columns → 컬럼 파악)
            └─ 급부명 분리  (split_benefit_names: '∙'/'·' 구분자로 복수 급부 분리)
                 └─ 금액 조건 분리  (parse_amounts_from_cell: 연차별·조건별 금액 분리)
```

**급부명 분리가 필요한 이유**: 한 셀에 "일반암 진단자금∙고액암 진단자금"처럼 두 급부가 같이 적혀있는 경우가 많음. 하나의 행으로 놔두면 canonical key 매칭 시 어느 급부인지 특정 불가.

**금액 조건 분리가 필요한 이유**: "1년 이내 500만원 / 1년 이후 1,000만원"처럼 경과기간별 감액 구조가 있음. 이를 한 셀로 다루면 두 상품의 금액을 숫자로 비교할 수 없음.

최종 비교 화면에서는 `to_comparison_rows()`로 다시 급부 단위로 집약. "1 급부 = 1 비교행"이 사용자 관점의 자연스러운 단위이기 때문.

---

### 3. 급부 매칭: 임베딩 없이 canonical key (규칙 기반)

회사마다 급부명 표기가 다름:

| 라이나생명 | 한화생명 |
|---|---|
| 비급여(전액본인부담 포함) 항암약물·방사선치료자금 | 비급여(전액본인부담 포함) 항암약물·방사선치료자금 |
| 갑상선암 진단자금 | 갑상선암 진단특약금 |
| 암직접치료 상급종합병원 통원급여금 | 암직접치료 상급병원 통원비 |

"표현은 달라도 같은 보장인가?"를 판단하는 게 핵심.

**임베딩/LLM을 안 쓴 이유**: 보험 도메인 표현은 어휘 집합이 유한하고 명확함. `action`(진단/수술/치료/입원 등 44종) + `disease`(일반암/갑상선암 등 36종) + `condition`(비급여/상급종합병원 등 10종) 세 슬롯으로 분해하면 회사 무관한 canonical key를 만들 수 있음.

```
"비급여(전액본인부담 포함) 항암약물·방사선치료자금"
  → normalize_text (공백/괄호 제거) → "비급여항암약물방사선치료자금"
  → _strip_type_suffix (자금/보험금 등 접미사 제거)
  → condition 추출: "비급여"
  → disease 추출: action 마스킹 후 → 없음
  → action 추출: longest-match → "항암방사선"
  → canonical_key = "비급여|항암방사선"
```

action 마스킹이 필요한 이유: "항암약물방사선치료" 안에 "암"이 포함되어 `disease=일반암`으로 오탐할 수 있기 때문. action variant를 먼저 공백으로 마스킹한 뒤 disease를 추출.

longest-match를 쓰는 이유: "갑상선암"이 "암"보다 구체적이기 때문. "갑상선암" variant가 있으면 "암" 대신 "갑상선암"을 선택해야 함.

1:N 매칭(같은 canonical key를 가진 행이 복수)이 발생하면 금액 문자열 유사도로 greedy 매칭.

---

### 4. 회사별 파서 분리: 레지스트리 패턴

5개 이상 보험사를 지원할 때 `if 회사명 == "라이나"` 분기를 추가하는 방식은 유지보수 지옥. 대신 `BaseSummaryParser` 추상 클래스 + 레지스트리 딕셔너리로 분리.

```python
# parse_pdf() 하나만 구현하면 됨
class SamsungSummaryParser(BaseSummaryParser):
    def parse_pdf(self, pdf_path: Path) -> dict:
        ...

register_summary_parser("삼성생명", SamsungSummaryParser)
```

공통 로직(`utils.py`)과 회사 전용 로직의 경계:

| 구분 | 근거 | 내용 |
|------|------|------|
| 공통 (`utils.py`) | 표 구조 자체는 업계 표준 포맷 | `is_benefit_table`, `find_benefit_columns`, `parse_amounts_from_cell`, `split_benefit_names` |
| 라이나 전용 | `□ 무배당` 섹션 구조, 좌표 기반 fallback, 특수 주석 블록 | `ContractSectionFinder`, `_fallback_parse_by_coords`, `_normalize_note_text` |
| 한화 전용 | `■ 특약명(코드)` 헤더, 자간(letter-spacing) 문자 깨짐, 질병군 분리 열 | `_strip_spacing`, `RE_CONTRACT_HEADER`, 질병군 행 분리 로직 |

---

### 5. 데이터 모델: 3단 변환 구조

파서 raw dict → `CanonicalBenefit` → `SummaryRow` 순으로 변환.

각 단계를 분리한 이유:

| 단계 | 타입 | 이유 |
|------|------|------|
| Stage 1 파서 출력 | raw `dict` | 회사별 구조가 달라 공통 타입 강제 불가 |
| Stage 2 정규화 | `CanonicalBenefit` | 회사 무관 공통 구조로 통일. 이후 모든 처리는 여기서부터 |
| Stage 3 분류 | `CanonicalBenefit` (benefit_category 채움) | 분류 실패가 파싱을 블록하지 않도록 별도 단계 |
| Stage 4 export | `SummaryRow` | DataFrame/CSV 변환에 맞는 flat 구조. dedupe_key로 중복 제거 |
| Stage 5 enrichment | `SummaryRow.slots` | LLM이 없어도 1~4단계는 완전 동작. 선택적 보강 |

`SummaryRow`는 dedupe_key(MD5, business key 기반)로 중복 제거. 같은 PDF를 두 번 업로드하거나 라이나 번들에서 같은 특약이 중복 포함되는 케이스를 방어.

---

### 6. LLM 사용 범위: 가능한 한 좁게

| 단계 | LLM | 근거 |
|------|-----|------|
| PDF 파싱 | No | 표 구조 → 규칙으로 충분. LLM은 위치 정보 없음 |
| 급부명 매칭 | No | synonyms.json 어휘 범위 내. 틀리면 사전 수정이 더 예측 가능 |
| 금액 비교 | No | 수치 비교는 규칙이 항상 정확 |
| 슬롯 구조화 | 선택 | 지급사유 원문이 비정형일 때만. graceful degradation으로 API 키 없으면 스킵 |
| 조건상이 재판정 | 선택 | "1년 이내 500만원 vs 1년 미만 500만원"처럼 수치 비교 불가한 케이스만 소비자 관점 판정 |

LLM을 최소화한 이유: 보험 비교는 **정확도 요구가 높음**. LLM 오탐이 금액 비교 결과를 뒤집으면 신뢰 하락. 규칙 기반은 틀려도 원인이 명확하고 수정 가능.

---

## 처리 흐름

```
PDF 업로드
   │
   ▼
① 파싱 (Stage 1)
   회사별 파서(레지스트리 조회) → find_tables() + 좌표 fallback
   → raw dict {contracts: [{name, benefits: [{benefit_names, trigger, amounts}]}]}
   │
   ▼
② 정규화 (Stage 2)
   raw dict → CanonicalBenefit
   급부명 복수이면 분리 (1급부 = 1 CanonicalBenefit)
   contract 단위 중복 제거
   │
   ▼
③ 분류 (Stage 3)
   benefit_category 채움 (진단/수술/치료/입원/…)
   실패해도 파이프라인 계속 진행
   │
   ▼
④ 저장 (Stage 4)
   CanonicalBenefit → SummaryRow (flat)
   금액 조건 복수이면 행 분리
   dedupe_key(MD5) 기준 중복 제거
   artifacts/에 JSON 저장
   │
   ▼
⑤ 비교 요청
   canonical_key 생성 → match_benefits → build_comparison
   (선택) LLM 슬롯 구조화 + 조건상이 재판정
   → 리포트 생성 (Markdown / CSV)
```

---

## 신규 보험사 추가 — 4단계

### Step 1. 파서 작성

`BaseSummaryParser` 상속 후 `parse_pdf()` 구현:

```python
# src/insurance_parser/parse/samsung_summary_parser.py
from .product_bundle_parser import BaseSummaryParser, register_summary_parser
from .utils import clean, find_benefit_columns, is_benefit_table  # 공통 로직 재사용

class SamsungSummaryParser(BaseSummaryParser):
    def parse_pdf(self, pdf_path: Path) -> dict:
        # 삼성 PDF 고유 구조만 여기에
        ...

register_summary_parser("삼성생명", SamsungSummaryParser)
```

`parse_pdf()` 반환 스키마 (모든 파서 공통):

```python
{
    "product_name": str,
    "contracts": [{
        "name": str,
        "reference_amount": str,
        "benefits": [{"benefit_names": [...], "trigger": str, "amounts": [...]}],
        "notes": [str]
    }]
}
```

### Step 2. 레지스트리 등록

`parse/__init__.py`에 한 줄 추가:

```python
from . import samsung_summary_parser  # noqa: F401
```

### Step 3. 동의어 사전 추가

`config/synonyms_삼성생명.json` 생성. 코드 수정 없이 자동 로딩.

```json
{
  "_insurer": "삼성생명",
  "disease": { "HER2양성유방암": ["HER2양성유방암", "HER2유방암"] }
}
```

### Step 4. 매칭 갭 확인

```bash
python -m tools.check_gaps --insurer 삼성생명
```

canonical_key 미매핑 급부명 목록 출력 → synonyms에 추가 → Step 3 반복.

---

## 운영 관리 — 코드 수정 없이 조정 가능한 항목

### `config/synonyms*.json` — 급부 매칭이 안 될 때

세 슬롯 구조:

| 슬롯 | 의미 | 예시 (일부) |
|------|------|------|
| `action` | 급부 유형 (44종) | 진단, 수술, 항암약물, 항암방사선, 통원, 입원, NGS유전자패널검사 |
| `disease` | 질병 분류 (36종) | 일반암, 갑상선암, 경계성종양, 기타피부암갑상선암복합 |
| `condition` | 지급 조건 (10종) | 비급여, 급여, 상급종합병원, 3기이상 |

변환 예:

| 급부명 원문 | canonical_key |
|-----------|--------------|
| 비급여(전액본인부담 포함) 항암약물·방사선치료자금 | `비급여\|항암방사선` |
| 갑상선암 진단자금 | `갑상선암\|진단` |
| 암직접치료 상급종합병원 통원급여금 | `상급종합병원\|통원` |

### `config/compare_rules.json` — 비교 방향 기준

| type | 사용 시 |
|------|---------|
| `numeric` | 금액 숫자 비교 — 클수록 유리 |
| `limit_numeric` | "최대 N년/회" 수치 비교 |
| `none_is_better` | 없는 쪽이 유리 (감액·면책 조항) |
| `display_only` | 방향 없음, 표시만 |

---

## 파일 구조

```
term_test_v2/
├── src/insurance_parser/
│   ├── parse/
│   │   ├── utils.py                  # 공통 파서 로직 (Generic 영역)
│   │   ├── product_bundle_parser.py  # BaseSummaryParser + 레지스트리
│   │   ├── lina_summary_parser.py    # 라이나생명 전용
│   │   └── hanwha_summary_parser.py  # 한화생명 전용
│   │
│   ├── summary_pipeline/
│   │   ├── models.py                 # CanonicalBenefit / SummaryRow / PipelineResult
│   │   ├── pipeline.py               # 파이프라인 오케스트레이터 (Stage 1~5)
│   │   ├── normalizer.py             # Stage 2 / Stage 4 변환
│   │   ├── classifier.py             # Stage 3 benefit_category 분류
│   │   ├── detector.py               # 문서 타입 판별 (SUMMARY / TERMS / UNKNOWN)
│   │   └── store.py                  # ArtifactStore (JSON 저장/로드)
│   │
│   └── comparison/
│       ├── normalize.py              # canonical_key 생성 + match_benefits
│       ├── enrich.py                 # Stage 5 LLM 슬롯 추출 (선택)
│       └── engine.py                 # build_comparison 비교 엔진
│
├── config/
│   ├── synonyms.json                 # 업계 공통 동의어 사전
│   ├── synonyms_한화생명.json
│   ├── synonyms_라이나생명.json
│   └── compare_rules.json
│
├── tools/check_gaps.py               # canonical_key 미매핑 급부 확인 CLI
├── app.py                            # Streamlit 진입점
└── artifacts/                        # 파싱 결과 JSON 저장소
```

---

## 로컬 실행

```bash
pip install -r requirements.txt
streamlit run app.py

# LLM enrichment 활성화 (선택)
cp .env.example .env
# OPENROUTER_API_KEY=sk-or-...
```

## 배포

```bash
git push github main   # GitHub
git push origin main   # Hugging Face Spaces 빌드 트리거
```

HF Spaces 환경변수 (Settings → Repository secrets):
- `OPENROUTER_API_KEY` — LLM enrichment 활성화
- `ARTIFACT_DIR` — artifact 저장 경로 (기본값: `./artifacts`)
