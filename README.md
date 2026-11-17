# 민원정책 질의응답 수집·정리 (답변 초안 담당용)

## 폴더 구성
```
작업폴더/
  collect.py          수집 (목록 -> 항목 본문)
  clean.py            정리 (HTML 제거, 개인정보 마스킹, 중복 묶기, 관련도 표시)
  requirements.txt    필요한 라이브러리
  raw/                collect.py 가 만든 원본 응답  (지우지 마세요)
  clean/              clean.py 가 만든 표 (qna_all.csv, qna_groups.csv)
  logs/               counts.csv, clean_report.txt
```

## 1. 준비 (처음 한 번)
1. VS Code에서 위 폴더를 연다 (File > Open Folder).
2. 터미널 열기 (Terminal > New Terminal). `python --version` 으로 Python 확인.
   - 안 나오면 `py --version` 시도. 그래도 안 되면 python.org 에서 설치(PATH 체크).
3. `pip install -r requirements.txt`
4. `collect.py` 맨 위 `LIST_URL`, `ITEM_URL` 채우기
   - 공공데이터포털 미리보기 새 탭 주소창에서 **물음표(?) 앞까지만** 복사
   - 목록: .../PolicyQnaList , 항목: .../PolicyQnaItem
   - serviceKey 등 변수는 붙이지 않는다
5. 인증키를 터미널에 넣는다 (터미널을 닫으면 다시 입력)
   - PowerShell : `$env:DATA_GO_KR_KEY="인증키"`
   - macOS/Linux: `export DATA_GO_KR_KEY="인증키"`
   - 키를 파일 안에 쓰거나 팀 채팅에 올리지 않는다.

## 2. 수집
```
# (선택) 조건별 건수 표 만들기 -> logs/counts.csv
python collect.py count --keywords 기초연금 생계급여 긴급복지 --types 1 2 3 --search-types 1 2

# 시험: 본문 5건만
python collect.py collect --keywords 기초연금 생계급여 긴급복지 --types 3 --search-types 1 2 --limit 5

# 전체 수집 (이미 받은 건은 건너뜀)
python collect.py collect --keywords 기초연금 생계급여 긴급복지 --types 3 --search-types 1 2
```
- 날짜 범위 기본값은 20240101~20261110. 바꾸려면 `--from 20240101 --to 20261110`
- E03 = 그 조건에 데이터 없음(오류 아님). 0건으로 처리된다.
- 키 오류가 나면 Encoding/Decoding 어느 키든 동작하지만, 마이페이지에서 키가 맞는지 확인.

## 3. 정리
```
python clean.py
```
- `clean/qna_groups.csv` : 비슷한 항목을 묶은 표 (중복횟수 열 포함) -> 팀에 넘기는 표
- `clean/qna_all.csv`    : 전체 행 + group_id -> 평가용 분리(같은 group_id는 한쪽에만)에 사용
- `logs/clean_report.txt`: 행 수, 묶음 수, 핵심관련 수, 마스킹 횟수
- `핵심관련 = Y` : 제목·질문·근거 조문에 서비스 낱말이 있는 항목
  (`SERVICE_TERMS` 를 clean.py 위쪽에서 고칠 수 있다)

## 4. 반드시 눈으로 확인
- qna_groups.csv 에서 핵심관련=Y 행 20건 정도: 서비스 관련이 맞는지, 이름·연락처가 남았는지
- 이름 같은 개인정보는 자동 마스킹이 되지 않는다.
- 비슷한 항목 묶는 기준은 clean.py 의 `Q_SIM`, `A_SIM` 값 (데이터 보고 조정)

## 5. 기록할 것 (제안서 평가 조건에 필요)
- 수집일, 키워드, type/searchType, 기간, 수집 건수
- 사용한 AI 모델 이름·버전, 지시문 버전
