"""이미지로만 제공된 표 2곳을 글자로 옮겨 law/chunks_admrul.csv 에 넣는다.
  G-0010  기초연금 고시 제8조  기본재산한도액 (지역별, 만 원)
  G-0018  긴급복지지원 금융재산 기준  2026년 가구원 수별 금융재산 금액 (천 원)

사용:  python src/apply_tables2.py     (한 번 적용한 뒤에는 다시 실행해도 아무 일도 하지 않는다)
값은 법제처 원문 화면에서 옮겼다. 반드시 원문과 대조한다.
G-0018 은 어제 입력한 기준 중위소득 표(백 원 단위 절사 + 600만 원)와 계산이 맞는 것을 확인했다.
※ collect_admrul.py 를 다시 실행하면 사라지므로, 그 뒤에는 apply_tables.py -> split_g0017.py -> apply_tables2.py 순서로 다시 실행한다.
"""
import csv
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data"
CSV = BASE / "law" / "chunks_admrul.csv"
BAK = BASE / "law" / "chunks_admrul.bak2.csv"
NOTE = " (법제처 원문 표를 글자로 옮김, 2026-11-11 입력)"
MARK = re.compile(r"\[표\(이미지\):[^\]]*\]")

TABLES = {
    "G-0010": "[표] 기본재산한도액 (지역별, 금액 단위: 만 원): 대도시 13,500만 원, 중소도시 8,500만 원, 농어촌 7,250만 원." + NOTE,
    "G-0018": "[표] 2026년 가구원 수별 금융재산 금액 (단위: 천 원): 1인 8,564천 원, 2인 10,199천 원, 3인 11,359천 원, "
              "4인 12,494천 원, 5인 13,556천 원, 6인 14,555천 원. 7인 가구 이상은 1인 증가 시마다 960천 원 추가." + NOTE,
}


def main():
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames
        rows = list(rd)
    done = 0
    for r in rows:
        if r["chunk_id"] in TABLES:
            ms = MARK.findall(r["본문"])
            if len(ms) == 1:
                if not BAK.exists():
                    shutil.copy(CSV, BAK)
                r["본문"] = MARK.sub(lambda m: " " + TABLES[r["chunk_id"]] + " ", r["본문"])
                done += 1
                print(r["chunk_id"], "적용")
            elif len(ms) == 0:
                print(r["chunk_id"], "이미지 표시가 없습니다 (이미 적용됨). 건너뜁니다.")
            else:
                print(r["chunk_id"], f"이미지 표시 {len(ms)}곳: 예상과 달라 건너뜁니다.")
    if done:
        with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        print(f"{done}곳 적용. 백업: {BAK}")


if __name__ == "__main__":
    main()
