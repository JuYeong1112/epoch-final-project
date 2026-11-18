"""2단계 검색 개선 실험: 1-1 ~ 1-6 을 순서대로 하나씩 적용하고 실험기록.csv 에 남긴다.

규칙 (사람 판단이 끼지 않도록 코드로 정했다)
  - 후보 값은 조정용 묶음에서만 비교해 고른다. 고르는 기준: 5개 안 건수 -> 3개 안 건수 -> 8개 안 건수 순.
  - 고른 값이 조정용에서 지금보다 나아야 하고, 확인용에서 5개 안·3개 안 건수가 하나도 줄지 않아야 채택한다.
  - 채택하지 않으면 되돌린다(다음 실험은 이전 설정에서 출발).
사용: python 실험_검색.py
"""
import csv
import datetime
import sys

import retrieve_v2 as R

LOG = R.HERE / "실험기록.csv"
FIELDS = ["순번", "날짜", "단계", "항목", "설정", "조정용_전(3/5/8)", "조정용_후(3/5/8)", "확인용_전(3/5/8)", "확인용_후(3/5/8)", "판정", "이유"]
rows = []


def fmt(r):
    return f'{r[3]}/{r[5]}/{r[8]} (n={r["n"]}, 5개 안 {r[5] / r["n"]:.1%})'


def key(r):
    return (r[5], r[3], r[8])


def log(**kw):
    kw.setdefault("날짜", datetime.date.today().isoformat())
    kw["순번"] = len(rows) + 1
    rows.append({f: kw.get(f, "") for f in FIELDS})


def run(cfg):
    return R.audit(cfg, quiet=True)


def try_change(cur, cur_res, item, cands):
    """cands: [(설명, cfg 변경 dict)]  -> (새 설정, 새 결과)"""
    best = None
    for desc, ch in cands:
        cfg = dict(cur, **ch)
        res = run(cfg)
        log(단계="조정 탐색", 항목=item, 설정=desc, **{"조정용_전(3/5/8)": fmt(cur_res["조정용"]), "조정용_후(3/5/8)": fmt(res["조정용"])})
        if best is None or key(res["조정용"]) > key(best[2]["조정용"]):
            best = (desc, cfg, res)
    desc, cfg, res = best
    t_ok = key(res["조정용"]) > key(cur_res["조정용"])
    c_ok = res["확인용"][5] >= cur_res["확인용"][5] and res["확인용"][3] >= cur_res["확인용"][3]
    if not t_ok:
        verdict, why = "버림", "조정용에서 나아지지 않음 (확인용은 재지 않음)"
        log(단계="판정", 항목=item, 설정=desc, 판정=verdict, 이유=why,
            **{"조정용_전(3/5/8)": fmt(cur_res["조정용"]), "조정용_후(3/5/8)": fmt(res["조정용"])})
        return cur, cur_res
    verdict = "채택" if c_ok else "버림"
    why = "조정용 개선, 확인용 하락 없음" if c_ok else "조정용은 나아졌지만 확인용에서 줄어듦"
    log(단계="판정", 항목=item, 설정=desc, 판정=verdict, 이유=why,
        **{"조정용_전(3/5/8)": fmt(cur_res["조정용"]), "조정용_후(3/5/8)": fmt(res["조정용"]),
           "확인용_전(3/5/8)": fmt(cur_res["확인용"]), "확인용_후(3/5/8)": fmt(res["확인용"])})
    return (cfg, res) if c_ok else (cur, cur_res)


def same_output(cfg_a, cfg_b):
    a, b = R.Retriever(cfg_a), R.Retriever(cfg_b)
    diff = n = 0
    for r in R.read_csv(R.QNA):
        if r["group_id"] in a.locked:
            continue
        q = r["제목"] + " " + r["질문"]
        for k in (3, 5, 8):
            n += 1
            ea = [e["id"] for e, _ in a.search(q, k_ev=k, skip_groups={r["group_id"]})[1]]
            eb = [e["id"] for e, _ in b.search(q, k_ev=k, skip_groups={r["group_id"]})[1]]
            diff += ea != eb
    return diff, n


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cur = dict(R.CFG_BASE)
    cur_res = run(cur)
    log(단계="기준값", 항목="retrieve.py 와 같은 설정", 설정="스위치 모두 끔",
        **{"조정용_전(3/5/8)": fmt(cur_res["조정용"]), "확인용_전(3/5/8)": fmt(cur_res["확인용"])})

    # 1-1 근거 개수 5 -> 8: 검색 순위는 그대로이고, 초안에 넘기는 근거 수만 바뀐다. 5개 안 대비 8개 안 건수로 비교.
    t, c = cur_res["조정용"], cur_res["확인용"]
    ok = t[8] > t[5] and c[8] > c[5]
    log(단계="판정", 항목="1-1 근거 개수 5→8", 설정="EVIDENCE_K 5 → 8",
        **{"조정용_전(3/5/8)": f"5개 안 {t[5]}/{t['n']}", "조정용_후(3/5/8)": f"8개 안 {t[8]}/{t['n']}",
           "확인용_전(3/5/8)": f"5개 안 {c[5]}/{c['n']}", "확인용_후(3/5/8)": f"8개 안 {c[8]}/{c['n']}"},
        판정="채택" if ok else "버림",
        이유="두 묶음 모두 늘어남" if ok else "확인용에서 늘지 않음 (근거만 길어지고 이득 없음)")

    cur, cur_res = try_change(cur, cur_res, "1-2 용어사전을 법령 검색에 적용", [("dict=켬", {"dict": True})])
    cur, cur_res = try_change(cur, cur_res, "1-3 제도 판별 후 다른 제도 감점",
                              [(f"fam={v}", {"fam": v}) for v in (0.8, 0.6, 0.4, 0.2)])
    cur, cur_res = try_change(cur, cur_res, "1-4 조문 제목 가중치",
                              [(f"title={v}", {"title": v}) for v in (0.1, 0.2, 0.3, 0.5, 0.8)])
    cur, cur_res = try_change(cur, cur_res, "1-5 유사도 낮은 이웃 질의 가점 제외",
                              [(f"nb_min={v}", {"nb_min": v}) for v in (0.1, 0.15, 0.2, 0.25, 0.3)])

    # 1-6 고시 번호 -> 이름·위치: 결과가 바뀌면 안 되는 정리 작업. 출력이 완전히 같으면 채택.
    new = dict(cur, govname=True)
    diff, n = same_output(cur, new)
    res = run(new)
    log(단계="판정", 항목="1-6 고시 번호를 이름·위치로 교체", 설정="govname=켬",
        **{"조정용_전(3/5/8)": fmt(cur_res["조정용"]), "조정용_후(3/5/8)": fmt(res["조정용"]),
           "확인용_전(3/5/8)": fmt(cur_res["확인용"]), "확인용_후(3/5/8)": fmt(res["확인용"])},
        판정="채택" if diff == 0 else "버림",
        이유=f"잠금 아닌 전 질의 {n}회 검색에서 근거 목록 차이 {diff}회 (고시 파일이 바뀌어도 번호가 밀리지 않게 하는 정리)")
    if diff == 0:
        cur, cur_res = new, res

    # 1-1 재판정: 다른 변경을 다 적용한 뒤 같은 규칙(두 묶음 모두 8개 안이 5개 안보다 많을 것)으로 다시 본다.
    # 주의: 이 줄은 위 실험에서 확인용 8개 안 숫자를 이미 본 뒤에 더한 것이다(결과보고.md 에 적음).
    t, c = cur_res["조정용"], cur_res["확인용"]
    ok = t[8] > t[5] and c[8] > c[5]
    log(단계="판정", 항목="1-1 재판정(1-2~1-6 적용 뒤)", 설정="EVIDENCE_K 5 → 8",
        **{"조정용_전(3/5/8)": f"5개 안 {t[5]}/{t['n']}", "조정용_후(3/5/8)": f"8개 안 {t[8]}/{t['n']}",
           "확인용_전(3/5/8)": f"5개 안 {c[5]}/{c['n']}", "확인용_후(3/5/8)": f"8개 안 {c[8]}/{c['n']}"},
        판정="채택" if ok else "버림",
        이유="두 묶음 모두 늘어남. 확인용 8개 안 숫자를 본 뒤 추가한 재판정" if ok else "늘지 않음")

    with open(LOG, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        if r["단계"] != "조정 탐색":
            print(r["항목"], "|", r["설정"], "|", r["판정"], "|", r["조정용_전(3/5/8)"], "->", r["조정용_후(3/5/8)"], "| 확인용", r["확인용_전(3/5/8)"], "->", r["확인용_후(3/5/8)"])
    print("최종 설정:", {k: v for k, v in cur.items() if v != R.CFG_BASE[k]})


if __name__ == "__main__":
    main()
