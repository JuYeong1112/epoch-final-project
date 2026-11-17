# epoch-final-project

2026 사회보장 데이터 AI 혁신 아이디어 경진대회 · **복지 민원 AI 서비스**

복지 민원 질문이 들어오면 비슷한 과거 질의응답과 관련 법령·고시 조문을 찾고, **찾은 근거만으로** 답변 초안을 쓴 뒤 근거 번호를 자동으로 대조하는 파이프라인입니다.
대상 서비스는 **기초연금 · 생계급여(국민기초생활보장) · 긴급복지지원** 세 가지입니다.

---

## 전체 흐름

```
[수집]                         [정리]          [검색]              [초안 생성]           [점검]
collect.py  (민원 질의응답)  →  clean.py   ─┐
collect_law.py (법령 조문)   ───────────────┼→  retrieve.py  →  generate.py  →  check_citations.py
collect_admrul.py (고시 문단) → 표 보정 스크립트 ┘   (+ embed_build.py)                  → 사람 검토(eval50_review)
```

---

## 진행 상황 (2026-11-17 기준)

| 단계 | 상태 | 내용 |
|---|---|---|
| 질의응답 수집·정리 | 완료 | 국민권익위 민원정책 질의응답 **276건 → 중복 묶음 263개**, 핵심 관련 131건. 전화번호 등 개인정보 패턴 111건 마스킹 |
| 법령 수집 | 완료 | 법령·시행령·시행규칙 9개 → 조문 **341개** (`L-0001`~) |
| 고시 수집 | 완료 | 고시 5종 → 문단 **25개** (`G-0001`~). 이미지로만 있던 표는 원문을 보고 손으로 입력해 넣음 |
| 검색 | 완료 | 글자 조각 TF-IDF + 비슷한 질의가 인용한 조문에 가점. 의미 검색(임베딩)을 섞는 옵션 있음 |
| 초안 생성 | 진행 중 | 지시문 현재 버전 **v5.2** (`generate.py`의 `INSTRUCTION_VERSION`) |
| 평가 | 진행 중 | 평가용 50묶음, 지시문 v5.1 기준 사람 검토 완료: 의미 일치 **O 29 / △ 10 / X 11**, 정답 조문이 근거 5개 안에 든 비율 30/48. v5.2 초안은 생성했고 검토 전 |

**알려진 문제:** X로 판정된 건은 대부분 검색 단계에서 정답 조문을 못 찾은 경우입니다(`[검색 누락]` 메모). 검색 개선이 다음 과제입니다.
`data/emb/` 벡터는 고시 문단이 추가되기 전에 만든 것이라 지금 데이터와 맞지 않습니다. 의미 검색(`HYB>0`)을 쓰려면 먼저 `python src/embed_build.py`를 다시 실행해야 합니다.

---

## 폴더 구조

```
epoch_data/
├─ src/                  코드 (전부 여기서 실행: python src/파일명.py)
├─ data/                 데이터
│   ├─ raw/               질의응답 원본 응답 (collect.py 결과, 지우지 말 것)
│   │   ├─ index.json      수집 목록
│   │   ├─ lists/          목록 API 응답
│   │   └─ items/          항목 본문 276건 (JSON)
│   ├─ clean/             정리된 질의응답 표
│   │   ├─ qna_all.csv     전체 276행 + group_id (평가 분리에 사용)
│   │   ├─ qna_groups.csv  묶음별 대표 행 263개 + 중복횟수 (팀 공유용 표)
│   │   └─ qna_groups_v1.csv  이전 버전
│   ├─ law/               근거 자료
│   │   ├─ chunks.csv      법령 조문 341개 (L-번호)
│   │   ├─ chunks_admrul.csv  고시 문단 25개 (G-번호, 표 보정 반영본)
│   │   ├─ chunks_admrul.bak*.csv  표 보정 전 백업
│   │   ├─ raw/            법령 원본 응답
│   │   └─ raw_admrul/     고시 원본 응답
│   └─ emb/               의미 검색용 벡터 (embed_build.py 결과)
├─ eval/                 평가
│   ├─ groups/            평가·개발에 쓰는 묶음 목록
│   └─ review/            사람 검토표
├─ drafts/               모델·지시문 버전별 답변 초안 CSV (generate.py 결과)
├─ logs/                 (git 제외) 수집·정리 리포트
├─ requirements.txt      필요한 라이브러리 (requests, numpy, scikit-learn, openai)
└─ README.md
```

**근거 번호 체계:** `L-0001` 법령 조문 · `G-0001` 고시 문단 · `Q-6889973` 과거 질의응답(faqNo)

### 법령·고시 범위
- 법령: 기초연금법, 국민기초생활 보장법, 긴급복지지원법 (각각 법·시행령·시행규칙)
- 고시: 2026년 기준 중위소득 및 생계·의료급여 선정기준 / 기초연금 선정기준액 등 세부기준 / 긴급복지지원 금융재산 기준 / 긴급복지지원 위기사유 / 긴급지원 지원금액 및 재산의 합계액 기준

---

## 파일 설명

### 수집 (`src/`)
| 파일 | 하는 일 | 필요한 키 |
|---|---|---|
| `collect.py` | 민원정책 질의응답 수집 (공공데이터포털) → `data/raw/` | `DATA_GO_KR_KEY` |
| `collect_law.py` | 법령 본문 수집 후 조문 단위로 자름 → `data/law/chunks.csv` | `LAW_OC` |
| `collect_admrul.py` | 고시 본문 수집 후 문단 단위로 자름 → `data/law/chunks_admrul.csv` | `LAW_OC` |
| `add_admrul.py` | 고시 1건을 찾아서 `chunks_admrul.csv` 끝에 추가 | `LAW_OC` |

### 고시 표 보정 (`src/`, 이미지로만 제공된 표를 글자로 입력)
| 파일 | 대상 |
|---|---|
| `apply_tables.py` | G-0017 2026년 기준 중위소득 표 |
| `split_g0017.py` | G-0017을 항목별 4개 문단으로 분리 |
| `apply_tables2.py` | G-0010 기초연금 기본재산한도액, G-0018 긴급복지 금융재산 기준 |
| `apply_tables3.py` | 긴급지원 지원금액·재산 합계액 기준 표 6개 |

> ⚠️ `src/collect_admrul.py`를 다시 실행하면 표 보정이 모두 사라집니다. 그 뒤에는 아래 순서로 다시 실행하세요.
> `apply_tables.py` → `split_g0017.py` → `apply_tables2.py` → `add_admrul.py add 2100000269954` → `apply_tables3.py`

### 정리·검색·생성·점검 (`src/`)
| 파일 | 하는 일 |
|---|---|
| `clean.py` | HTML 제거, 개인정보 마스킹, 중복 묶기, 근거 조문 추출 → `data/clean/` |
| `retrieve.py` | 질문 하나에 비슷한 질의응답과 근거 문단을 찾음. `eval`로 검색 정확도 점검 |
| `embed_build.py` | 의미 검색용 벡터 생성 → `data/emb/` (질의응답·법령·고시가 바뀌면 다시 실행) |
| `generate.py` | 찾은 근거로 답변 초안 생성 → `drafts/` (모델명·지시문 버전·근거 번호 함께 기록) |
| `check_citations.py` | 초안의 근거 번호, 숫자, 조문 번호가 실제 근거에 있는지 형식 점검 (generate.py가 자동 호출) |

### 평가 (`eval/`)
| 파일 | 내용 |
|---|---|
| `groups/eval_groups.txt` | 평가용 group_id 50개. 이 묶음들은 검색 대상에서 빠짐 (데이터 누수 방지) |
| `groups/dev_groups.txt` | 지시문 개발용 group_id 7개 |
| `groups/eval_group_ids_v1_1.csv` | 평가 묶음 선정표 (평가용 50 + 예비 4) |
| `review/eval50_review.csv` | 평가 50건 사람 검토표 (작업본) |
| `review/eval50_review_final_v51.csv` | 지시문 v5.1 초안 검토 확정본: 의미 일치(O/△/X), 오류 메모 |

---

## 시작하기

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

키는 **환경변수로만** 넣습니다. 파일이나 채팅에 키를 적지 마세요.
```powershell
$env:DATA_GO_KR_KEY="..."   # 공공데이터포털 (질의응답 수집)
$env:LAW_OC="..."           # 법제처 OPEN API (법령·고시 수집)
$env:OPENAI_API_KEY="..."   # 초안 생성·임베딩
$env:OPENAI_MODEL="..."     # 초안 생성에 쓸 모델 이름
```

데이터(`data/`)는 저장소에 이미 들어 있습니다. 수집부터 다시 할 필요 없이 검색과 생성부터 바로 해볼 수 있습니다.

```powershell
# 검색만 해보기 (키 필요 없음)
python src/retrieve.py ask "부부가 기초연금을 받으면 금액이 줄어드나요?"
python src/retrieve.py eval

# AI 호출 없이 프롬프트만 확인 (비용 0)
python src/generate.py dry "질문"

# 초안 생성
python src/generate.py ask "질문"
python src/generate.py batch --groups eval/groups/eval_groups.txt   # 평가 50건 일괄 생성
python src/generate.py batch --first 5                  # 시험 5건
```

의미 검색을 섞으려면 `$env:HYB="0.5"`를 설정합니다. 0이 기본값이고, 이때는 글자 방식만 씁니다.

---

## 주의사항
- 외부 AI에는 **개인정보를 가린 글만** 보냅니다. 질문을 직접 넣을 때도 이름과 연락처는 빼세요.
- 이름 같은 개인정보는 자동으로 마스킹되지 않습니다. `data/clean/` 표는 눈으로 확인해야 합니다.
- `check_citations.py`는 형식만 점검합니다. 답변의 뜻이 근거와 맞는지는 사람이 읽고 판단해야 합니다.
- 제안서용 기록: 수집일, 키워드, 기간, 수집 건수, 사용한 모델 이름·버전, 지시문 버전.
