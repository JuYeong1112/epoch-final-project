"""팀원의 최종 구성(v6j)을 GPT 로 끝까지 돌려 초안을 만든다: v6 검색 -> (보류 판정) -> GPT 재순위 20→8 -> 사업안내 3쪽 -> 지시문 v6/v6j -> GPT 초안 -> 자동 점검.

놓는 곳: accuracy_v6/ 폴더 (generate_v6.py, rerank_gpt_v6.py 와 같은 폴더). 기존 파일은 고치지 않는다.
대상: 시나리오_20건.csv + 범위밖_5건.csv (팀원이 Claude 로 초안을 쓴 것과 같은 25건). 팀원 결과(drafts_v6j.csv 등)와 같은 점검 규칙으로 비교한다.

준비: $env:OPENAI_API_KEY="실제키"; $env:OPENAI_MODEL="gpt-6.1-terra"
사용 (accuracy_v6 폴더에서)
  python generate_gpt_v6.py dry            # 호출 없이: 보류 건수, 첫 프롬프트 확인 (비용 0)
  python generate_gpt_v6.py run            # 재순위 호출 + 초안 호출 (보류가 아닌 질문마다 2회) -> drafts_gpt_v6j.csv
  $env:RERANK_OFF="1"; python generate_gpt_v6.py run   # 재순위 없이 검색 순서 8개 (재순위 효과만 따로 보려고)
  $env:NO_JICHIM="1"; python generate_gpt_v6.py run    # 사업안내(지침) 3쪽을 빼고 (프롬프트가 짧아지면 GPT 가 답을 쓰는지 보려고)
  $env:EVID_K="5"; python generate_gpt_v6.py run       # 근거를 8개 대신 5개로
  $env:CAND_N="40"; python generate_gpt_v6.py run      # 재순위 후보를 20개 대신 40개로 (검색 정확도 시험에서 채택한 구성)
  (설정마다 결과 파일 이름이 달라진다: drafts_gpt_v6j_<설정>.csv. 환경변수는 끝나면 Remove-Item Env:이름 으로 지운다)
같은 입력이면 다시 호출하지 않는다(gen_gpt_cache.json).
"""
import csv
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
import generate_v6 as G
import rerank_gpt_v6 as RG
import 재순위 as X

R = G.R
HERE = G.HERE
X.N_CAND = int(os.environ.get("CAND_N", X.N_CAND))   # CAND_N=40: 재순위 후보를 40개로 (rerank_plus_v6.py 의 A1 구성)
CACHE = HERE / "gen_gpt_cache.json"


TAG = "".join([("_재순위없음" if os.environ.get("RERANK_OFF") == "1" else ""), ("_지침없음" if os.environ.get("NO_JICHIM") == "1" else ""),
               (f"_근거{os.environ['EVID_K']}" if os.environ.get("EVID_K") else ""),
               (f"_후보{os.environ['CAND_N']}" if os.environ.get("CAND_N") else "")])


def cached_call(cache, system, user):
    model = (os.environ.get("RERANK_MODEL") or os.environ.get("OPENAI_MODEL", ""))
    ck = hashlib.sha256(json.dumps([model, system, user], ensure_ascii=False).encode()).hexdigest()
    if ck not in cache:
        raw, _ = RG.call_gpt(user, system=system)
        cache[ck] = raw
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        return raw, True
    return cache[ck], False


def build_one(ret, idx, jc, cache, tid, q, g, call_ok):
    """한 질문의 초안 프롬프트를 만든다. 반환: (보류사유, 시스템, 사용자글, items, qa, ev, 지침사용, 호출수)"""
    reason = G.hold_reason(ret, q)
    skip = {g} if g else None
    qa, cand, extra = G.rerank_search(ret, q, skip)
    if reason:
        return reason, None, None, {}, qa, [], False, 0
    calls = 0
    ids = [e["id"] for e, _ in cand]
    if os.environ.get("RERANK_OFF") == "1":
        order = ids
    else:
        if not call_ok:
            order = ids
        else:
            raw, new = cached_call(cache, "", X.prompt_text(q, cand).replace("20개", f"{len(cand)}개"))
            calls += int(new)
            try:
                order = X.apply_order(ids, raw)
            except ValueError:
                order = ids
    k_ev = int(os.environ.get("EVID_K", G.K_EV_V6))
    ev = [(dict(idx[i], cited_by=[]), 0.0) for i in order[: k_ev]]
    have = {e["id"] for e, _ in ev}
    _, ev8 = ret.search(q, k_qa=G.K_QA, k_ev=k_ev, skip_groups=skip)
    ev += [x for x in ev8 if x[0]["id"].startswith("G-") and x[0]["id"] not in have]   # 팀원 v6r 과 같은 방식: 고정 고시는 뒤에 붙임
    fam = R.question_family(q)
    use_j = fam is not None and os.environ.get("NO_JICHIM") != "1"
    if use_j:
        ev += jc.top(q, fam)
    prompt, items = G.build_prompt(q, qa, ev)
    return "", (G.SYSTEM_V6J if use_j else G.SYSTEM_V6), prompt, items, qa, ev, use_j, calls


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("dry", "run"):
        print(__doc__)
        return
    dry = sys.argv[1] == "dry"
    ret = R.Retriever(R.CFG_V6)
    idx = {e["id"]: e for e in ret.ev}
    jc = G.Jichim()
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    model = (os.environ.get("RERANK_MODEL") or os.environ.get("OPENAI_MODEL", "")) or "(dry)"
    rows, holds, first = [], [], None
    tg = G.targets()
    for n, (tid, q, g) in enumerate(tg, 1):
        reason, system, user, items, qa, ev, use_j, _ = build_one(ret, idx, jc, cache, tid, q, g, call_ok=not dry)
        if reason:
            holds.append((tid, reason))
            d = {"요지": "", "유형": "범위 밖 보류", "답변초안": G.HOLD_TEXT, "확인필요": [reason]}
            used = "규칙(모델 호출 없음)"
        else:
            if dry:
                first = first or (tid, system, user)
                continue
            raw, new = cached_call(cache, system, user)
            if new:
                print(f"  {n}/{len(tg)} {tid} 초안 완료", flush=True)
            try:
                d = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            except ValueError:
                d = {"요지": "", "유형": "", "답변초안": raw, "확인필요": ["JSON 형식 아님: 직접 확인"]}
            used = model
        res = G.check(d.get("답변초안", ""), items)
        rows.append({"번호": tid, "질문": q, "요지": d.get("요지", ""), "유형": d.get("유형", ""),
                     "답변초안": d.get("답변초안", ""), "확인필요": "; ".join(d.get("확인필요", []) or []),
                     "찾은_질의": ", ".join(f'Q-{r["faqNo"]}' for r, _ in qa),
                     "찾은_근거": ", ".join(e["id"] for e, _ in ev), "모델": used,
                     "지시문버전": G.INSTRUCTION_VERSION + ("+재순위없음" if os.environ.get("RERANK_OFF") == "1" else "+GPT재순위") + ("+지침" if use_j else "") + TAG,
                     "보류사유": reason, **{"점검_" + k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in res.items()}})
    print(f"대상 {len(tg)}건 | 보류 {len(holds)}건: {holds}")
    if dry:
        print("\n=== 첫 질문", first[0], "===\n" + first[1][:300] + " ...\n\n" + first[2][:1500])
        return
    out = HERE / f"drafts_gpt_v6j{TAG}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    for part, pre in (("시나리오 20건", "S"), ("범위 밖 5건", "X")):
        rs = [r for r in rows if r["번호"].startswith(pre)]
        cnt = {s: sum(r["점검_자동점검"] == s for r in rs) for s in ("통과", "확인필요", "근거부족", "범위밖")}
        print(f"GPT {part}: {len(rs)}건 {cnt}")
    print("저장:", out, "| 팀원 Claude 결과와 비교: drafts_v6j.csv, python generate_v6.py collect")


if __name__ == "__main__":
    main()
