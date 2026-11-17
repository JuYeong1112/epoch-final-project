"""이미지로만 제공된 표를 글자로 옮겨 law/chunks_admrul.csv 에 넣는다 (G-0017: 2026년 기준 중위소득 고시).

사용:  python apply_tables.py
  - 먼저 law/chunks_admrul.csv 를 law/chunks_admrul.bak.csv 로 복사해 둔다.
  - G-0017 본문에서 '[표(이미지) ...]' 표시를 나온 순서대로 아래 6개로 바꾼다.
  - 이미 바꾼 뒤에는 표시가 없으므로 다시 실행해도 아무 일도 하지 않는다.
  - 값은 법제처 원문 화면(시행 2026-01-01, 보건복지부고시 제2025-135호)에서 옮겼다. 반드시 원문과 대조한다.
  ※ collect_admrul.py 를 다시 실행하면 이 작업이 사라지므로, 그 뒤에는 이 스크립트를 다시 실행한다.
"""
import csv
import re
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent
CSV = BASE / "law" / "chunks_admrul.csv"
BAK = BASE / "law" / "chunks_admrul.bak.csv"
LOG = BASE / "logs" / "manual_tables.txt"
CID = "G-0017"
NOTE = " (법제처 원문 표를 글자로 옮김, 2026-11-11 입력)"

TABLES = [
    "[표] 기준 중위소득 (원/월): 1인가구 2,564,238원, 2인가구 4,199,292원, 3인가구 5,359,036원, "
    "4인가구 6,494,738원, 5인가구 7,556,719원, 6인가구 8,555,952원, 7인가구 9,515,150원." + NOTE,
    "[표] 생계급여 선정기준 (원/월): 1인가구 820,556원, 2인가구 1,343,773원, 3인가구 1,714,892원, "
    "4인가구 2,078,316원, 5인가구 2,418,150원, 6인가구 2,737,905원, 7인가구 3,044,848원." + NOTE,
    "[식] 생계급여액 = 생계급여 최저보장수준(대상자 선정기준) - 소득인정액" + NOTE,
    "[표] 보장시설 생계급여 선정기준 (원/월): 1인가구 1,025,695원, 2인가구 1,679,717원, 3인가구 2,143,614원, "
    "4인가구 2,597,895원, 5인가구 3,022,688원, 6인가구 3,422,381원, 7인가구 3,806,060원." + NOTE,
    "[표] 보장시설 생계급여 수급자 1인당 최저보장수준 (원/월, 보장시설의 규모별): 30인 미만 426,741원, "
    "30인 이상 100인 미만 372,206원, 100인 이상 300인 미만 350,882원, 300인 이상 350,841원." + NOTE,
    "[표] 의료급여 선정기준 (원/월): 1인가구 1,025,695원, 2인가구 1,679,717원, 3인가구 2,143,614원, "
    "4인가구 2,597,895원, 5인가구 3,022,688원, 6인가구 3,422,381원, 7인가구 3,806,060원." + NOTE,
]
MARK = re.compile(r"\[표\(이미지\):[^\]]*\]")


def main():
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames
        rows = list(rd)
    target = next((r for r in rows if r["chunk_id"] == CID), None)
    if target is None:
        raise SystemExit(f"{CID} 를 찾지 못했습니다.")
    marks = MARK.findall(target["본문"])
    if not marks:
        print("이미지 표시가 없습니다. 이미 적용됐거나 문단이 바뀌었습니다. 아무것도 바꾸지 않았습니다.")
        return
    if len(marks) != len(TABLES):
        raise SystemExit(f"이미지 표시 {len(marks)}곳, 준비한 표 {len(TABLES)}개: 개수가 달라 중단합니다.")
    if not BAK.exists():
        shutil.copy(CSV, BAK)
    it = iter(TABLES)
    target["본문"] = MARK.sub(lambda m: " " + next(it) + " ", target["본문"])
    with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text(f"{CID} 이미지 표 {len(TABLES)}곳을 글자로 옮김 (2026-11-11). 원문과 대조 필요.\n", encoding="utf-8")
    print(f"{CID}: 표 {len(TABLES)}곳 적용. 백업: {BAK}")


if __name__ == "__main__":
    main()
