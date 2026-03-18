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

보험사 상품요약서 PDF를 자동으로 읽어, 급부(보장 항목)를 회사 간에 나란히 비교해 주는 분석 도구입니다.

---

## 어떤 문제를 해결하나요?

보험 상품을 비교할 때, 회사마다 PDF 형식이 다르고 같은 보장 항목도 다른 이름으로 표기됩니다.  
이 시스템은 각 회사의 PDF를 파싱하고, 급부명을 표준 형태로 변환한 뒤, 자동으로 매칭해 한 화면에서 비교할 수 있게 만들어 줍니다.

---

## 화면 미리보기

**대시보드** — 등록된 상품 현황 및 사용 흐름 안내

![대시보드](docs/screenshot_dashboard.png)

**상품 비교 설정** — 당사 vs 타사 상품 선택 후 비교 시작

![상품 비교 설정](docs/screenshot_compare_setup.png)

**비교 결과 리포트** — Executive Summary · KEY SELLING POINTS 자동 생성

![비교 결과 리포트](docs/screenshot_compare_result.png)

**비교 상세** — 보장 범위·급부별 금액 비교 테이블

![비교 상세](docs/screenshot_compare_detail.png)

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| PDF 자동 파싱 | 보험사별 상품요약서 PDF에서 급부명·금액·조건 추출 |
| 급부명 표준화 | 회사마다 다른 표현을 공통 키(`condition\|disease\|action`)로 변환 |
| 자동 매칭 비교 | 두 상품의 급부를 자동으로 대응시켜 금액/보장 범위 비교 |
| 리포트 생성 | Markdown / CSV 형식으로 비교 결과 다운로드 |
| LLM 보조 분석 | API 키 보유 시, AI가 조건 불명확 항목을 소비자 관점으로 재판정 |

> **LLM 없이도** 파싱·매칭·비교·리포트 모든 기능이 정상 작동합니다.

---

## 처리 흐름 (5단계)

```
PDF 업로드
   │
   ▼
① 파싱   : 회사별 파서가 PDF에서 급부 데이터 추출
   │
   ▼
② 정규화  : 추출 데이터를 공통 스키마(CanonicalBenefit)로 변환
   │
   ▼
③ 분류   : 보장 카테고리(암, 수술, 입원 등) 자동 분류
   │
   ▼
④ 저장   : SummaryRow 형태로 비교 가능한 구조로 저장
   │
   ▼
⑤ 비교   : 두 상품의 급부를 매칭하여 금액·보장범위 비교 및 리포트 생성
           (선택) LLM이 조건 불명확 항목 재판정
```

---

## 파서 구조 — 어떻게 여러 회사를 지원하나요?

각 보험사 PDF는 레이아웃이 달라 회사별 전용 파서가 필요합니다.  
동시에, 테이블 탐색이나 금액 파싱처럼 **공통으로 쓰이는 로직**은 하나의 파일(`utils.py`)에 모아 재사용합니다.

```
BaseSummaryParser (공통 인터페이스)
│
├── LinaProductSummaryParser   ← 라이나생명 전용
│     └── 사용: utils.py의 공통 로직 + 라이나 고유 좌표 파싱
│
├── HanwhaProductSummaryParser ← 한화생명 전용
│     └── 사용: utils.py의 공통 로직 + 한화 고유 헤더·자간 처리
│
└── GenericSummaryParser       ← 신규 보험사 fallback (일반 표 구조 PDF)
```

| 구분 | 파일 | 내용 |
|------|------|------|
| 공통 로직 | `parse/utils.py` | 헤더 컬럼 탐색, 금액 셀 파싱, 급부명 분리 |
| 라이나 전용 | `lina_summary_parser.py` | `□ 무배당` 섹션 탐색, 좌표 기반 fallback |
| 한화 전용 | `hanwha_summary_parser.py` | `■ 특약명(코드)` 헤더, 자간 제거, 질병군 분리 |

---

## 신규 보험사 추가하기 — 4단계

### 1. 파서 파일 작성

`BaseSummaryParser`를 상속해 `parse_pdf()` 메서드만 구현합니다.

```python
# src/insurance_parser/parse/samsung_summary_parser.py
from .product_bundle_parser import BaseSummaryParser, register_summary_parser
from .utils import clean, find_benefit_columns, is_benefit_table

class SamsungSummaryParser(BaseSummaryParser):
    def parse_pdf(self, pdf_path):
        # 삼성 PDF 고유 구조에 맞는 파싱 로직
        ...

register_summary_parser("삼성생명", SamsungSummaryParser)
```

`parse_pdf()`가 반환해야 하는 구조:

```python
{
    "product_name": str,
    "management_no": str,
    "components": {"riders": [str, ...]},
    "contracts": [{
        "name": str,
        "type": "rider",
        "source_pdf": str,
        "reference_amount": str,
        "benefits": [{"benefit_names": [...], "trigger": str, "amounts": [...]}],
        "notes": [str]
    }]
}
```

### 2. 레지스트리에 등록

`parse/__init__.py`에 import 한 줄 추가:

```python
from . import samsung_summary_parser  # noqa: F401
```

### 3. 동의어 사전 추가

`config/synonyms_삼성생명.json` 파일을 만들어 회사 고유 표현을 등록합니다.  
파일을 추가하면 코드 수정 없이 자동으로 로딩됩니다.

```json
{
  "_insurer": "삼성생명",
  "disease": {
    "HER2양성유방암": ["HER2양성유방암", "HER2유방암"]
  }
}
```

### 4. 매핑 갭 확인

파싱 후 매칭이 안 된 급부명을 확인하고 동의어 사전을 보완합니다.

```bash
python -m tools.check_gaps --insurer 삼성생명
```

---

## 운영 관리 — 코드 수정 없이 조정 가능한 항목

### 급부 매칭이 안 될 때 → `config/synonyms*.json`

급부명을 `condition|disease|action` 세 슬롯으로 분해해 매칭합니다.

| 슬롯 | 의미 | 예시 |
|------|------|------|
| `action` | 급부 유형 | 진단, 수술, 항암약물, 통원, 입원 |
| `disease` | 질병 분류 | 일반암, 갑상선암, 기타피부암갑상선암복합 |
| `condition` | 지급 조건 | 비급여, 급여, 상급종합병원, 3기이상 |

변환 예시:

| 급부명 원문 | canonical_key |
|-----------|--------------|
| 비급여 항암약물·방사선치료자금 | `비급여\|항암방사선` |
| 갑상선암진단자금 | `갑상선암\|진단` |
| 암직접치료상급종합병원통원급여금 | `상급종합병원\|통원` |

### 비교 기준을 바꾸고 싶을 때 → `config/compare_rules.json`

| type | 사용 시 |
|------|---------|
| `numeric` | 금액 숫자 비교 — 클수록 유리 |
| `limit_numeric` | "최대 N년/회" 수치 비교 |
| `none_is_better` | 없는 쪽이 유리 (감액·면책) |
| `display_only` | 방향 없음, 표시만 |

---

## LLM 사용 범위

| 단계 | LLM | 비고 |
|------|-----|------|
| PDF 파싱 | No | PyMuPDF 규칙 기반 |
| 급부명 매칭 | No | synonyms.json 사전 기반 |
| 금액 비교 | No | compare_rules.json 규칙 기반 |
| 슬롯 구조화 | 선택 | 비정형 약관 텍스트 → 조건 슬롯 추출 |
| 조건상이 재판정 | 선택 | 수치 비교 불가 케이스를 소비자 관점으로 판정 |

`OPENROUTER_API_KEY` 없이도 모든 기능 동작합니다.

---

## 파일 구조

```
term_test_v2/
├── app.py                            # Streamlit 앱 진입점
├── views/workbench.py                # UI 화면
│
├── src/insurance_parser/
│   ├── parse/
│   │   ├── utils.py                  # 공통 파서 로직
│   │   ├── product_bundle_parser.py  # BaseSummaryParser + 레지스트리
│   │   ├── lina_summary_parser.py    # 라이나생명 전용
│   │   └── hanwha_summary_parser.py  # 한화생명 전용
│   │
│   ├── summary_pipeline/
│   │   ├── models.py                 # 데이터 모델
│   │   ├── pipeline.py               # 파이프라인 오케스트레이터
│   │   ├── normalizer.py             # 정규화 + export
│   │   ├── classifier.py             # 보장 카테고리 분류
│   │   ├── detector.py               # 문서 타입 판별
│   │   └── store.py                  # ArtifactStore
│   │
│   └── comparison/
│       ├── normalize.py              # canonical_key + 급부 매칭
│       ├── enrich.py                 # LLM 슬롯 추출 (선택)
│       └── engine.py                 # 비교 엔진
│
├── config/                           # 코드 수정 없이 편집 가능
│   ├── synonyms.json
│   ├── synonyms_한화생명.json
│   ├── synonyms_라이나생명.json
│   └── compare_rules.json
│
├── tools/check_gaps.py               # canonical_key 갭 탐지 CLI
└── artifacts/                        # 파싱 결과 JSON 저장소
```

---

## 로컬 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

LLM enrichment를 활성화하려면:

```bash
cp .env.example .env
# .env 파일에서 OPENROUTER_API_KEY=sk-or-... 입력
```

## 배포 (Hugging Face Spaces)

```bash
git push github main   # GitHub 백업
git push origin main   # HF Spaces 빌드 트리거
```

HF Spaces 환경변수 (Settings → Repository secrets):
- `OPENROUTER_API_KEY` — LLM enrichment 활성화
- `ARTIFACT_DIR` — artifact 저장 경로 (기본값: `./artifacts`)
