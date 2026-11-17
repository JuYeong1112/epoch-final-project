"""이미지로만 제공된 표 6곳을 글자로 옮겨 law/chunks_admrul.csv 의 '긴급지원 지원금액 및 재산의 합계액 기준' 문단에 넣는다.
  1 생계지원 금액(원/월)  3 주거지원 한도액(원/월)  4 사회복지시설 이용지원 한도액(원/월)
  5 교육지원금액(원/분기)  6 그 밖의 지원금액(원/월)  7 재산의 합계액 기준(천 원)
값은 법제처 원문 화면(보건복지부고시 제2025-210호, 시행 2025. 12. 17.)에서 옮겼다. 원문과 반드시 대조한다.

사용:  python apply_tables3.py     (한 번 적용한 뒤에는 다시 실행해도 아무 일도 하지 않는다)
※ collect_admrul.py 를 다시 실행하면 사라지므로, 그 뒤에는 apply_tables.py -> split_g0017.py -> apply_tables2.py ->
   add_admrul.py add 2100000269954 -> apply_tables3.py 순서로 다시 한다.
"""
import csv
import re
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
CSV = BASE / "law" / "chunks_admrul.csv"
BAK = BASE / "law" / "chunks_admrul.bak4.csv"
NAME = "긴급지원 지원금액 및 재산의 합계액 기준"
NOTE = " (법제처 원문 표를 글자로 옮김, 2026-11-13 입력)"
MARK = re.compile(r"\[표\(이미지\):[^\]]*\]")

AREA = "대도시는 특별시, 광역시의 '구'(도농복합 '군' 포함), 특례시이고, 중소도시는 도의 '시'와 세종특별자치시이며, 농어촌은 도의 '군'이다."

TABLES = [
    # 1. 생계지원 금액
    "[표] 생계지원 금액 (원/월): 1인 783,000원, 2인 1,286,600원, 3인 1,644,000원, 4인 1,994,600원, 5인 2,324,400원, 6인 2,636,700원. "
    "가구원 수가 7인 이상인 경우 1인 증가 시마다 301,000원씩 추가 지급. 2021년부터 생계지원 금액에 냉방비 포함." + NOTE,
    # 3. 주거지원 한도액
    "[표] 주거지원 한도액 (원/월): 대도시 1~2인 398,900원, 3~4인 662,500원, 5~6인 874,100원; "
    "중소도시 1~2인 299,100원, 3~4인 435,600원, 5~6인 574,200원; 농어촌 1~2인 189,000원, 3~4인 250,500원, 5~6인 330,000원. "
    "가구원 수가 7인 이상인 경우 1인 증가 시마다 대도시 105,800원, 중소도시 69,300원, 농어촌 39,800원씩 추가 지급. " + AREA + NOTE,
    # 4. 사회복지시설의 이용지원 한도액
    "[표] 사회복지시설의 이용지원 한도액 (원/월): 입소자 1인 552,000원, 2인 941,700원, 3인 1,218,400원, 4인 1,494,100원, "
    "5인 1,770,800원, 6인 2,047,400원. 입소자가 7인 이상인 경우 1인 증가 시마다 286,400원씩 추가 지급." + NOTE,
    # 5. 교육지원금액
    "[표] 교육지원금액 (원/분기): 초등학생 127,900원, 중학생 180,000원, 고등학생 214,000원 및 수업료(해당 학교장이 고지한 금액)·입학금(해당 학교장이 고지한 금액)." + NOTE,
    # 6. 그 밖의 지원금액
    "[표] 그 밖의 지원금액 (원/월): 연료비 150,000원, 해산비 700,000원, 장제비 800,000원, 전기요금 500,000원 이내. "
    "「산업재해보상보험법」 제71조 등 다른 법률에 의하여 장제비를 지급받은 자는 그 금액을 차감하여 지급함." + NOTE,
    # 7. 재산의 합계액 기준
    "[표] 재산의 합계액 기준 (단위: 천 원): 주거용 재산 공제한도액 대도시 69,000천 원, 중소도시 42,000천 원, 농어촌 35,000천 원; "
    "기준금액 대도시 241,000천 원, 중소도시 152,000천 원, 농어촌 130,000천 원. 산식: (조사결과의 총액)-(공제액)≤(기준금액). " + AREA + NOTE,
]


def main():
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames
        rows = list(rd)
    done = 0
    for r in rows:
        if r["고시명"] != NAME:
            continue
        ms = MARK.findall(r["본문"])
        if len(ms) == len(TABLES):
            if not BAK.exists():
                shutil.copy(CSV, BAK)
            it = iter(TABLES)
            r["본문"] = MARK.sub(lambda m: " " + next(it) + " ", r["본문"])
            done += 1
            print(r["chunk_id"], f"표 {len(TABLES)}곳 적용")
        elif len(ms) == 0:
            print(r["chunk_id"], "이미지 표시가 없습니다 (이미 적용됨). 건너뜁니다.")
        else:
            print(r["chunk_id"], f"이미지 표시 {len(ms)}곳: 예상({len(TABLES)}곳)과 달라 건너뜁니다.")
    if done:
        with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        print(f"{done}곳 적용. 백업: {BAK}")
    elif not any(r["고시명"] == NAME for r in rows):
        print("해당 고시 문단이 없습니다. add_admrul.py add 2100000269954 를 먼저 실행하세요.")


if __name__ == "__main__":
    main()
