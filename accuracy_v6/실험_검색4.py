"""4차 검색 실험: 4-1 조문별 시민질문 색인. v6(2차까지) 설정 위에 한 번 시험하고 실험기록.csv 뒤에 덧붙인다.
판정 규칙은 실험_검색.py 와 같다. 후보 값은 실행 전에 정했다 (0.2, 0.3, 0.5, 0.8).
주의: 확인용 묶음은 네 번째로 판정에 쓴다.
사용: python 실험_검색4.py
"""
import csv
import sys

import retrieve_v2 as R
import 실험_검색 as E

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    E.rows.extend(R.read_csv(E.LOG))
    n0 = len(E.rows)
    cur = dict(R.CFG_V6)
    cur_res = E.run(cur)
    cur, cur_res = E.try_change(cur, cur_res, "4-1 조문별 시민질문 색인",
                                [(f"docq={v}", {"docq": v}) for v in (0.2, 0.3, 0.5, 0.8)])
    with open(E.LOG, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=E.FIELDS)
        w.writeheader()
        w.writerows(E.rows)
    for r in E.rows[n0:]:
        print(r["단계"], "|", r["설정"], "|", r["판정"], "| 조정용", r["조정용_전(3/5/8)"], "->", r["조정용_후(3/5/8)"], "| 확인용", r["확인용_전(3/5/8)"], "->", r["확인용_후(3/5/8)"])
