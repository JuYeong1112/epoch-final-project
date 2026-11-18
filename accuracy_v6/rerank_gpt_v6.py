"""GPT 재순위를 팀원의 v6 검색 위에서 돌려, 팀원의 Claude 재순위와 같은 후보·같은 지시문으로 비교한다.

놓는 곳: 저장소의 accuracy_v6/ 폴더 (retrieve_v2.py, 재순위.py 와 같은 폴더). 기존 파일은 고치지 않는다.

준비
  $env:OPENAI_API_KEY="실제키"; $env:OPENAI_MODEL="gpt-6.1-terra"   (RERANK_MODEL 로 재순위만 다른 모델 가능)

사용 (accuracy_v6 폴더에서)
  python rerank_gpt_v6.py dry 조정용       # 호출 없이: 후보가 팀원 후보_조정용.json 과 같은지 + 첫 프롬프트 + 팀원 Claude 결과 점수
  python rerank_gpt_v6.py run 조정용       # GPT 호출(질문당 1회). 결과는 out_gpt/조정용/<번호>.json (팀원과 같은 형식)
  $env:CONFIRM="1"; python rerank_gpt_v6.py run 확인용   # 확인용은 마지막에 한 번만. CONFIRM=1 이 없으면 막는다.

비교 조건: 후보(검색 v6 설정 20개), 지시문(팀원 재순위.py 의 INSTR), 점수 계산이 모두 팀원과 같고 순서를 정하는 모델만 다르다.
같은 입력이면 다시 호출하지 않는다(rerank_gpt_cache.json).
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
import retrieve_v2 as R
import 재순위 as T

HERE = Path(__file__).resolve().parent
CACHE = HERE / "rerank_gpt_cache.json"
OUT = HERE / "out_gpt"


def call_gpt(user, system=None):
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = (os.environ.get("RERANK_MODEL") or os.environ.get("OPENAI_MODEL", "")).strip()
    if not key or not model:
        sys.exit("환경변수 OPENAI_API_KEY 와 OPENAI_MODEL 이 필요합니다.")
    import openai
    try:
        key.encode("ascii")
    except UnicodeEncodeError:
        sys.exit("OPENAI_API_KEY 에 한글 등이 들어 있습니다. 실제 키(sk-로 시작)를 넣으세요.")
    client = openai.OpenAI(api_key=key, max_retries=4, timeout=float(os.environ.get("REQ_TIMEOUT", "120")))
    use_temp = True
    for attempt in range(8):
        try:
            kw = {"temperature": 0} if use_temp else {}
            try:
                resp = client.chat.completions.create(
                    model=model, messages=([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}],
                    response_format={"type": "json_object"}, **kw)
            except openai.BadRequestError as e:
                if use_temp and "temperature" in str(e).lower():
                    use_temp = False
                    continue
                raise
            return resp.choices[0].message.content, model
        except (openai.APITimeoutError, openai.APIConnectionError) as e:
            print(f"  연결 문제로 다시 요청합니다 ({attempt + 1}/8): {type(e).__name__}")
            time.sleep(5)
        except openai.RateLimitError as e:
            msg = str(e)
            if "insufficient_quota" in msg or ("billing" in msg.lower() and "quota" in msg.lower()):
                sys.exit("잔액/한도 문제로 보입니다: " + msg[:200])
            wait = 15 * (attempt + 1)
            print(f"  분당 한도에 걸려 {wait}초 기다립니다 ({attempt + 1}/8)...")
            time.sleep(wait)
    sys.exit("한도 때문에 계속 실패했습니다. 같은 명령을 다시 실행하세요(끝난 건은 건너뜁니다).")


def build(r, ev):
    """팀원 재순위.py 의 cmd_make 와 같은 프롬프트"""
    lines = [T.INSTR, "", "[민원 질문]", R.norm(r["제목"] + " / " + r["질문"]), "", "[후보 근거]"]
    for e, _ in ev:
        body = R.norm(e["text"])
        lines.append(f'{e["id"]} | {e["name"]} {e["pos"]} {e["title"]} | {body[:350]}{" ..." if len(body) > 350 else ""}')
    return "\n".join(lines)


def candidates(ret, r):
    q = r["제목"] + " " + r["질문"]
    _, ev = ret.search(q, k_ev=T.N_CAND, skip_groups={r["group_id"]})
    return ev[: T.N_CAND]


def saved_candidates(ret, saved, r):
    """팀원이 Claude 에게 보여 준 후보(저장본)를 그대로 쓴다. 이렇게 해야 GPT 와 Claude 가 똑같은 후보를 본다."""
    by_id = {e["id"]: e for e in ret.ev}
    return [(by_id[i], 0.0) for i in saved[r["faqNo"]] if i in by_id]


def score(part, rows, ret, getter, label):
    pos = {e["id"]: (e["name"], e["pos"]) for e in ret.ev}
    cand = json.loads((HERE / "rerank" / f"후보_{part}.json").read_text(encoding="utf-8"))
    res = {3: 0, 5: 0, 8: 0, 20: 0}
    n = miss = 0
    for r in rows:
        ids = cand[r["faqNo"]]
        got = getter(r["faqNo"])
        if got is None:
            miss += 1
            continue
        new = list(dict.fromkeys([i for i in got if i in ids]))
        new += [i for i in ids if i not in new]
        gold = R.gold_of(r)
        n += 1
        arts = [pos[i] for i in new]
        for k in res:
            res[k] += any(g in arts[:k] for g in gold)
    if n:
        print(f"  {label}: 질의 {n}건(결과 없음 {miss}) | 3개 안 {res[3]} | 5개 안 {res[5]} ({res[5] / n:.1%}) | 8개 안 {res[8]} | 20개 안 {res[20]}")
    return res


def teammate_getter(part):
    def g(fq):
        p = HERE / "rerank" / "out" / part / f"{fq}.json"
        if not p.exists():
            return None
        t = p.read_text(encoding="utf-8")
        return json.loads(t[t.find("{"): t.rfind("}") + 1]).get("순위", [])
    return g


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("dry", "run") or sys.argv[2] not in ("조정용", "확인용"):
        print(__doc__)
        return
    cmd, part = sys.argv[1:]
    if part == "확인용" and cmd == "run" and os.environ.get("CONFIRM") != "1":
        sys.exit("확인용은 마지막에 한 번만 봅니다. 정말 지금 보려면 $env:CONFIRM=\"1\" 을 설정하고 다시 실행하세요.")
    ret = R.Retriever(R.CFG_V6)
    rows = T.rows_of(part)
    saved = json.loads((HERE / "rerank" / f"후보_{part}.json").read_text(encoding="utf-8"))
    diff = [r["faqNo"] for r in rows if [e["id"] for e, _ in candidates(ret, r)] != saved[r["faqNo"]]]
    print(f"{part}: 질의 {len(rows)}건 | 지금 저장소 데이터로 다시 만든 후보가 팀원 저장본과 다른 질의: {len(diff)}건")
    print("  비교는 팀원 저장본 후보 기준으로 합니다(GPT·Claude 가 같은 후보를 보게). 차이는 데이터 버전(고시 24/25문단) 영향일 수 있습니다.")
    if cmd == "dry":
        print(build(rows[0], saved_candidates(ret, saved, rows[0])))
        print()
        score(part, rows, ret, lambda fq: [e for e in saved[fq]], "재순위 전(검색 순서)")
        score(part, rows, ret, teammate_getter(part), "팀원 Claude 재순위")
        return
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    model = (os.environ.get("RERANK_MODEL") or os.environ.get("OPENAI_MODEL", ""))
    (OUT / part).mkdir(parents=True, exist_ok=True)
    for n, r in enumerate(rows, 1):
        ev = saved_candidates(ret, saved, r)
        prompt = build(r, ev)
        ck = hashlib.sha256(json.dumps([model, prompt], ensure_ascii=False).encode()).hexdigest()
        if ck not in cache:
            raw, _ = call_gpt(prompt)
            cache[ck] = raw
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  {n}/{len(rows)} {r['faqNo']} 재순위 완료", flush=True)
        raw = cache[ck]
        try:
            got = json.loads(raw[raw.find("{"): raw.rfind("}") + 1]).get("순위", [])
        except ValueError:
            got = []
        (OUT / part / f'{r["faqNo"]}.json').write_text(json.dumps({"순위": got}, ensure_ascii=False), encoding="utf-8")
    print(f"[{part}] 모델 {model}")
    score(part, rows, ret, lambda fq: saved[fq], "재순위 전(검색 순서)")
    score(part, rows, ret, teammate_getter(part), "팀원 Claude 재순위")

    def gg(fq):
        p = OUT / part / f"{fq}.json"
        return json.loads(p.read_text(encoding="utf-8")).get("순위", []) if p.exists() else None
    score(part, rows, ret, gg, "GPT 재순위")


if __name__ == "__main__":
    main()
