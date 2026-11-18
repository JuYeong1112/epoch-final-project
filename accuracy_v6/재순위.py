"""재순위(reranking) 시험: 검색이 뽑은 후보 20개를 AI 가 다시 줄 세우면 정답 조문이 위로 올라오는가.

순서
  1) python 재순위.py make 조정용      # 질문마다 rerank/prompts/조정용/<faqNo>.txt (질문 + 후보 20개, 정답 없음)
  2) 하위 에이전트가 각 파일을 읽고 rerank/out/조정용/<faqNo>.json 에 {"순위": ["L-0011", ...]} 저장
  3) python 재순위.py score 조정용     # 재순위 전후 3·5·8개 안 비교 (+ 20개 안 = 재순위로 닿을 수 있는 상한)
확인용도 같은 방식. 잠금 묶음(평가용·평가예비)은 검색 대상에서도 시험 대상에서도 뺀다.
최종 순서 = AI 가 고른 순서(후보 안에 있는 번호만, 중복 제거) + 나머지 후보는 원래 순서대로 뒤에 붙임.
"""
import json
import sys

import retrieve_v2 as R

N_CAND = 20
DIR = R.HERE / "rerank"

INSTR = """당신은 복지 민원 담당 공무원을 돕는 보조자입니다.
아래 [민원 질문]에 답하려면 어떤 근거가 필요한지 판단해, [후보 근거] 20개를 '답변에 직접 쓰일 근거'부터 순서대로 다시 줄 세웁니다.
- 질문이 묻는 것(자격, 금액, 신청 방법, 기간 등)에 직접 답하는 조문을 앞에 둡니다.
- 목적·정의처럼 일반적인 조문, 다른 제도의 조문, 서식만 정한 조문은 질문이 그것을 묻지 않는 한 뒤로 보냅니다.
- 후보에 적힌 번호만 씁니다. 20개 모두를 줄 세울 필요는 없고, 앞쪽 8개 이상이면 됩니다.
출력은 JSON 하나만: {"순위": ["번호1", "번호2", ...]}"""


def prompt_text(question, ev):
    """재순위 프롬프트 글: 지시문 + 질문 + 후보 근거 (정답은 넣지 않는다)"""
    lines = [INSTR, "", "[민원 질문]", R.norm(question), "", "[후보 근거]"]
    for e, _ in ev:
        body = R.norm(e["text"])
        lines.append(f'{e["id"]} | {e["name"]} {e["pos"]} {e["title"]} | {body[:350]}{" ..." if len(body) > 350 else ""}')
    return "\n".join(lines)


def apply_order(ids, out_text):
    """AI 가 돌려준 순서 중 후보에 있는 번호만 앞에, 나머지 후보는 원래 순서대로 뒤에"""
    got = json.loads(out_text[out_text.find("{"): out_text.rfind("}") + 1]).get("순위", [])
    new = list(dict.fromkeys([i for i in got if i in ids]))
    return new + [i for i in ids if i not in new]


def rows_of(part):
    split = R.load_split()
    return [r for r in R.read_csv(R.QNA) if split.get(r["group_id"]) == part and R.gold_of(r)]


def cmd_make(part):
    ret = R.Retriever(R.CFG_V6)
    d = DIR / "prompts" / part
    d.mkdir(parents=True, exist_ok=True)
    (DIR / "out" / part).mkdir(parents=True, exist_ok=True)
    cand = {}
    for r in rows_of(part):
        q = r["제목"] + " " + r["질문"]
        _, ev = ret.search(q, k_ev=N_CAND, skip_groups={r["group_id"]})
        ev = ev[:N_CAND]
        cand[r["faqNo"]] = [e["id"] for e, _ in ev]
        lines = [INSTR, "", "[민원 질문]", R.norm(r["제목"] + " / " + r["질문"]), "", "[후보 근거]"]
        for e, _ in ev:
            body = R.norm(e["text"])
            lines.append(f'{e["id"]} | {e["name"]} {e["pos"]} {e["title"]} | {body[:350]}{" ..." if len(body) > 350 else ""}')
        (d / f'{r["faqNo"]}.txt').write_text("\n".join(lines), encoding="utf-8")
    (DIR / f"후보_{part}.json").write_text(json.dumps(cand, ensure_ascii=False), encoding="utf-8")
    print(f"{part}: 프롬프트 {len(cand)}개 -> {d}")


def cmd_score(part, quiet=False):
    cand = json.loads((DIR / f"후보_{part}.json").read_text(encoding="utf-8"))
    ret = R.Retriever(R.CFG_V6)
    pos = {e["id"]: (e["name"], e["pos"]) for e in ret.ev}
    res = {"전": {3: 0, 5: 0, 8: 0, 20: 0}, "후": {3: 0, 5: 0, 8: 0, 20: 0}}
    n = missing = 0
    for r in rows_of(part):
        ids = cand[r["faqNo"]]
        p = DIR / "out" / part / f'{r["faqNo"]}.json'
        if not p.exists():
            missing += 1
            continue
        txt = p.read_text(encoding="utf-8")
        got = json.loads(txt[txt.find("{"): txt.rfind("}") + 1]).get("순위", [])
        new = list(dict.fromkeys([i for i in got if i in ids]))
        new += [i for i in ids if i not in new]
        gold = R.gold_of(r)
        n += 1
        for name, order in (("전", ids), ("후", new)):
            arts = [pos[i] for i in order]
            for k in (3, 5, 8, 20):
                res[name][k] += any(g in arts[:k] for g in gold)
    if not quiet:
        print(f"{part}: 질의 {n}건 (결과 없음 {missing}건)")
        for name in ("전", "후"):
            v = res[name]
            print(f"  재순위 {name}: 3개 안 {v[3]}/{n}={v[3] / n:.1%} | 5개 안 {v[5]}/{n}={v[5] / n:.1%} | 8개 안 {v[8]}/{n}={v[8] / n:.1%} | 20개 안 {v[20]}/{n}")
    return n, res


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if len(a) == 2 and a[0] in ("make", "score") and a[1] in ("조정용", "확인용"):
        (cmd_make if a[0] == "make" else cmd_score)(a[1])
    else:
        print(__doc__)
