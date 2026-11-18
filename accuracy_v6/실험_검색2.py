"""2차 검색 실험: 2-1 ~ 2-4 를 v6 설정 위에 하나씩 쌓는다. 실험기록.csv 뒤에 줄을 덧붙인다.

판정 규칙은 실험_검색.py 와 같다 (후보 값은 조정용에서만 고르고, 확인용 5개 안·3개 안이 줄지 않아야 채택).
후보 목록은 실행 전에 정했다 (조정용 누락 진단: 여러 질의가 함께 인용한 조문과 제1조(목적)가 정답을 밀어냄).
주의: 확인용 묶음은 1차 실험에서 이미 한 번 썼다. 같은 묶음으로 두 번째 판정하는 것이라 독립 확인의 힘은 약해진다.
사용: python 실험_검색2.py
"""
import csv
import sys

import retrieve_v2 as R
import 실험_검색 as E


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    E.rows.extend(R.read_csv(E.LOG))
    n0 = len(E.rows)
    cur = dict(R.CFG_V6)
    cur_res = E.run(cur)
    E.log(단계="기준값", 항목="2차 출발점 = v6 설정", 설정=str(R.ADOPTED),
          **{"조정용_전(3/5/8)": E.fmt(cur_res["조정용"]), "확인용_전(3/5/8)": E.fmt(cur_res["확인용"])})
    cur, cur_res = E.try_change(cur, cur_res, "2-1 비슷한 질의 가점 배율",
                                [(f"nb_w={v}", {"nb_w": v}) for v in (0.5, 0.75, 1.5, 2.0)])
    cur, cur_res = E.try_change(cur, cur_res, "2-2 가점을 인용 조문 수로 나눔",
                                [(f"nb_norm={v}", {"nb_norm": v}) for v in (0.5, 1.0)])
    cur, cur_res = E.try_change(cur, cur_res, "2-3 제1조(목적) 감점",
                                [(f"purpose={v}", {"purpose": v}) for v in (0.5, 0.2)])
    cur, cur_res = E.try_change(cur, cur_res, "2-4 가점에 쓰는 비슷한 질의 수",
                                [(f"k_nb={v}", {"k_nb": v}) for v in (3, 8, 10)])
    with open(E.LOG, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=E.FIELDS)
        w.writeheader()
        w.writerows(E.rows)
    for r in E.rows[n0:]:
        print(r["단계"], "|", r["항목"], "|", r["설정"], "|", r["판정"], "| 조정용", r["조정용_전(3/5/8)"], "->", r["조정용_후(3/5/8)"], "| 확인용", r["확인용_전(3/5/8)"], "->", r["확인용_후(3/5/8)"])
    print("2차 최종 설정에서 v6 와 다른 값:", {k: v for k, v in cur.items() if v != R.CFG_V6[k]})


if __name__ == "__main__":
    main()
