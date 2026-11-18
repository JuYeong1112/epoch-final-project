"""후보 검색을 넓히는 방법들을 v6 검색 + GPT 재순위 위에서 한 번에 비교한다.

놓는 곳: accuracy_v6/ 폴더 (rerank_gpt_v6.py, 재순위.py, retrieve_v2.py 와 같은 폴더). 기존 파일은 고치지 않는다.

비교하는 구성 (조정용 33건 + 확인용 29건, 후보를 지금 저장소 데이터로 직접 만든다)
  A0  후보 20개                          (기준: v6 검색 + GPT 재순위)
  A1  후보 40개
  A2  후보 20개 + 질문을 법령 용어로 바꿔 한 번 더 검색한 후보 10개
  A3  후보 40개 + 법령 용어 후보 10개

규칙 (미리 정해 둔 것)
  - 용어 변환 지시문은 한 번 쓰고 고치지 않는다. 구성도 위 네 가지만 본다. 결과를 보고 지시문이나 개수를 바꿔 다시 돌리지 않는다.
  - 확인용은 이미 한 번 쓴 묶음이라 완전히 새로운 시험이 아니다. 두 묶음 결과를 모두 보고하고, 이 한계를 함께 적는다.
  - 채택 기준: A0 보다 두 묶음 합계(62건)에서 5개 안이 3건 이상 늘고, 조정용에서 줄지 않을 때만.

준비: $env:OPENAI_API_KEY="실제키"; $env:OPENAI_MODEL="gpt-6.1-terra"
사용 (accuracy_v6 폴더에서)
  python rerank_plus_v6.py dry          # 호출 없이 후보 개수와 용어 변환 입력 예시 (비용 0)
  python rerank_plus_v6.py run          # 네 구성 모두 (재순위 62회 x 구성 + 용어 변환 62회; 같은 입력은 캐시)
  $env:CONFIGS="A0,A2"; python rerank_plus_v6.py run     # 일부만
결과: 화면 표 + rerank_plus_perq.csv (질문별 5개 안 적중)
"""
import csv
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
import retrieve_v2 as R
import 재순위 as T
import rerank_gpt_v6 as RG

HERE = RG.HERE
CACHE = HERE / "rerank_plus_cache.json"
EXTRA = 10
CONFIGS = {"A0": (20, 0), "A1": (40, 0), "A2": (20, EXTRA), "A3": (40, EXTRA)}
NAMES = {"A0": "후보 20", "A1": "후보 40", "A2": "후보 20 + 용어변환 10", "A3": "후보 40 + 용어변환 10"}

SYSTEM_RW = """당신은 복지 민원 질문을 법령·고시 검색에 쓸 용어로 바꾸는 보조자입니다.
민원인이 쓴 일상 표현을, 기초연금법·국민기초생활 보장법·긴급복지지원법(시행령·시행규칙·고시 포함)의 조문 제목이나 본문에 나올 법한 법령 용어로 바꿔 핵심어 3~8개를 씁니다.
- 질문에 없는 사실은 만들지 않습니다.
- 질문이 어느 제도에 관한 것인지 분명하면 제도 이름도 함께 씁니다.
- 예시(형식만 보여 줍니다): '장례비 지원' -> '장제급여', '집세가 부담된다' -> '주거급여'.
출력은 JSON 하나만: {"법령용어": ["용어1", "용어2", ...]}"""


def load_cache():
    return json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}


def cached(cache, system, user):
    model = (os.environ.get("RERANK_MODEL") or os.environ.get("OPENAI_MODEL", ""))
    ck = hashlib.sha256(json.dumps([model, system, user], ensure_ascii=False).encode()).hexdigest()
    if ck not in cache:
        raw, _ = RG.call_gpt(user, system=system)
        cache[ck] = raw
        CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache[ck]


def rewrite_terms(cache, r):
    q = R.norm(r["제목"] + " / " + r["질문"])
    raw = cached(cache, SYSTEM_RW, q)
    try:
        terms = json.loads(raw[raw.find("{"): raw.rfind("}") + 1]).get("법령용어", [])
    except ValueError:
        terms = []
    return [t for t in terms if isinstance(t, str)][:8]


def live_search(ret, text, n, skip):
    _, ev = ret.search(text, k_ev=n, skip_groups=skip)
    return ev[:n]


def candidates(ret, r, n, extra, cache):
    q = r["제목"] + " " + r["질문"]
    skip = {r["group_id"]}
    base = live_search(ret, q, n, skip)
    if not extra:
        return base
    terms = rewrite_terms(cache, r)
    if not terms:
        return base
    ev2 = live_search(ret, q + " " + " ".join(terms), n, skip)
    have = {e["id"] for e, _ in base}
    add = [x for x in ev2 if x[0]["id"] not in have][:extra]
    return base + add


def prompt_for(r, cands):
    instr = T.INSTR.replace("20개", f"{len(cands)}개")
    lines = [instr, "", "[민원 질문]", R.norm(r["제목"] + " / " + r["질문"]), "", "[후보 근거]"]
    for e, _ in cands:
        body = R.norm(e["text"])
        lines.append(f'{e["id"]} | {e["name"]} {e["pos"]} {e["title"]} | {body[:350]}{" ..." if len(body) > 350 else ""}')
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("dry", "run"):
        print(__doc__)
        return
    dry = sys.argv[1] == "dry"
    names = [c.strip() for c in os.environ.get("CONFIGS", "A0,A1,A2,A3").split(",") if c.strip()]
    ret = R.Retriever(R.CFG_V6)
    pos = {e["id"]: (e["name"], e["pos"]) for e in ret.ev}
    rows = [(p, r) for p in ("조정용", "확인용") for r in T.rows_of(p)]
    cache = load_cache()
    if dry:
        p, r = rows[0]
        print(f"대상 {len(rows)}건 (조정용 {sum(1 for x in rows if x[0] == '조정용')} + 확인용 {sum(1 for x in rows if x[0] == '확인용')})")
        print("=== 용어 변환 지시문 ===\n" + SYSTEM_RW + "\n=== 입력 예 ===\n" + R.norm(r["제목"] + " / " + r["질문"]))
        for nm in names:
            n, ex = CONFIGS[nm]
            c = live_search(ret, r["제목"] + " " + r["질문"], n, {r["group_id"]})
            print(f"{nm} {NAMES[nm]}: 첫 질문 후보 {len(c)}개(용어 변환 후보는 호출 후 추가)")
        return
    res = {nm: {} for nm in names}      # nm -> faqNo -> (part, hit3, hit5, hit8, cand)
    for nm in names:
        n, ex = CONFIGS[nm]
        for i, (p, r) in enumerate(rows, 1):
            cands = candidates(ret, r, n, ex, cache)
            ids = [e["id"] for e, _ in cands]
            raw = cached(cache, "", prompt_for(r, cands))
            try:
                order = T.apply_order(ids, raw)
            except ValueError:
                order = ids
            gold = R.gold_of(r)
            arts = [pos[x] for x in order]
            res[nm][r["faqNo"]] = (p, *(int(any(g in arts[:k] for g in gold)) for k in (3, 5, 8)), int(any(g in arts for g in gold)))
            if i % 10 == 0:
                print(f"  {nm} {i}/{len(rows)}", flush=True)
    print("\n구성별 결과 (정답 조문이 k개 안에 든 질의 수)")
    for part in ("조정용", "확인용", "합계"):
        print(f"[{part}]")
        for nm in names:
            sub = [v for v in res[nm].values() if part == "합계" or v[0] == part]
            n = len(sub)
            print(f"  {nm} {NAMES[nm]:22s} 질의 {n} | 3개 안 {sum(v[1] for v in sub)} | 5개 안 {sum(v[2] for v in sub)} | 8개 안 {sum(v[3] for v in sub)} | 후보 안 {sum(v[4] for v in sub)}")
    if "A0" in res:
        for nm in names:
            if nm == "A0":
                continue
            g = [f for f, v in res[nm].items() if v[2] and not res["A0"][f][2]]
            l = [f for f, v in res[nm].items() if not v[2] and res["A0"][f][2]]
            print(f"{nm} 대 A0 (5개 안): 새로 맞음 {len(g)}건 {g} | 틀려짐 {len(l)}건 {l}")
    out = HERE / "rerank_plus_perq.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["faqNo", "묶음"] + [f"{nm}_5개안" for nm in names] + [f"{nm}_후보안" for nm in names])
        for p, r in rows:
            w.writerow([r["faqNo"], p] + [res[nm][r["faqNo"]][2] for nm in names] + [res[nm][r["faqNo"]][4] for nm in names])
    print("저장:", out)


if __name__ == "__main__":
    main()
