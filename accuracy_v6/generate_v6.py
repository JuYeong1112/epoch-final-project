"""초안 생성 v6: 지시문 v6 + 범위 밖 보류. 외부 AI API 는 부르지 않는다.

AI 에게 보낼 글(프롬프트)을 파일로 만들고, 초안은 Claude Code 의 하위 에이전트가 프롬프트 파일만 보고 쓴다.
돌아온 초안(JSON)을 모아 check_citations_v2 로 점검한다.

사용 (정확도개선_v6 폴더에서)
  python generate_v6.py threshold     # 조정용에서 보류 기준값 계산
  python generate_v6.py scenarios     # 확인용에서 유형이 고르게 20건 -> 시나리오_20건.csv
  python generate_v6.py prompts       # prompts/기준/, prompts/v6/ 에 프롬프트 파일 만들기
  python generate_v6.py collect       # drafts_raw/ 의 초안을 모아 drafts_기준.csv, drafts_v6.csv + 점검
  python generate_v6.py rerank_make   # (v6r) 재순위 프롬프트 -> rerank/prompts/시나리오/
  python generate_v6.py prompts_rerank  # (v6r) 재순위 결과로 근거 8개를 골라 prompts/v6r/ 에 초안 프롬프트
  python generate_v6.py dry "질문"     # v6 프롬프트를 화면에 출력 (보류면 보류 문구)
"""
import csv
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
import ast


def _load_from_generate():
    """generate.py 에서 SYSTEM 지시문과 fmt_date·build_prompt 함수만 그대로 가져온다.
    (generate.py 를 통째로 불러오면 원래 폴더에 drafts/ 폴더를 만들어서, 필요한 부분만 읽는다)"""
    tree = ast.parse((HERE.parent / "src" / "generate.py").read_text(encoding="utf-8"))
    keep = [n for n in tree.body if (isinstance(n, ast.Assign) and n.targets[0].id == "SYSTEM")
            or (isinstance(n, ast.FunctionDef) and n.name in ("fmt_date", "build_prompt"))]
    ns = {}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "generate.py", "exec"), ns)
    return ns["SYSTEM"], ns["build_prompt"]


SYSTEM, build_prompt = _load_from_generate()     # 기존 지시문과 프롬프트 조립 방식을 그대로 쓴다
import retrieve_v2 as R
from check_citations_v2 import HOLD_TEXT, check

INSTRUCTION_VERSION = "v6-2026-11-18"
MODEL_NAME = "Claude Code 하위 에이전트 (claude-opus-5-5)"

ADD_V6 = """
추가 규칙 (v6)
9. 도입 문장을 쓰지 않습니다. 근거 번호를 붙일 수 없는 도입·연결 문장은 빼고 바로 답부터 씁니다.
   나쁜 예: 기초연금 신청 방법은 다음과 같습니다.
   좋은 예: 기초연금을 받으려는 사람은 주소지 시장·군수·구청장에게 지급을 신청할 수 있습니다. [L-0011]
   (예시의 번호는 형식을 보여 주는 것입니다. 실제로는 이번 참고 자료에 적힌 번호만 씁니다.)
10. 비슷한 질의응답(Q-)에 적힌 금액·기준이 고시(G-)의 금액·기준과 다르면 고시를 따르고 고시 번호와 시행일자를 붙입니다. 질의응답의 금액은 지난해 기준일 수 있으므로, 고시가 없으면 질의응답의 금액을 올해 금액처럼 쓰지 않고 "올해 금액은 담당자 확인 필요"로 씁니다.
11. 질문의 일부만 참고 자료로 답할 수 있으면, 답할 수 있는 부분은 근거 번호와 함께 쓰고 모자라는 부분만 "○○는 담당자 확인 필요"라고 한 문장으로 씁니다. 일부가 모자란다는 이유로 답 전체를 미루지 않습니다.
12. 아래는 형식 예시입니다. 내용을 이번 답에 옮기지 않습니다.
[예시 1] 질문: 기초생활수급 신청 때 부양의무자를 조사하는 경우와 하지 않는 경우가 있나요?
{"요지": "급여별 부양의무자 조사 여부 문의", "유형": "부양의무자 기준", "답변초안": "생계급여는 2021년부터 부양의무자 기준이 폐지되었으나, 부양의무자의 소득·재산 수준이 일정 기준을 넘으면 조사가 이루어질 수 있습니다. [Q-6881852] 주거급여는 2018년부터, 교육급여는 2015년부터 부양의무자 기준이 폐지되어 조사하지 않습니다. [Q-6881852] 의료급여는 부양의무자 기준이 적용됩니다. [Q-6881852]", "확인필요": []}
[예시 2] 질문: 2년 전에 아파트를 처분했는데 왜 아직 재산에 반영되나요?
{"요지": "처분한 재산이 계속 반영되는 이유 문의", "유형": "재산 산정", "답변초안": "2011년 7월 1일 이후 재산을 처분한 경우 처분 당시 재산의 가액을 기타(증여)재산으로 반영합니다. [Q-6889733] 다만 다른 재산을 취득했거나 부채 상환, 의료비 등에 사용했다면 그 금액만큼 공제할 수 있습니다. [Q-6889733] 매월 차감하는 자연적 소비금액의 올해 기준은 담당자 확인 필요입니다.", "확인필요": ["올해 자연적 소비금액"]}"""
SYSTEM_V6 = SYSTEM + "\n" + ADD_V6

# 범위 밖 보류: 세 제도의 말이 하나도 없거나, 가장 비슷한 근거(법령·고시)의 직접 유사도가 기준값 미만이면 보류
SCOPE_EXTRA = ["희망저축", "자산형성", "저축계좌", "정부양곡", "부정수급"]   # 조정용 질문에서 본 범위 안 말
HOLD_SIM = 0.09      # python generate_v6.py threshold 로 계산한 값 (조정용)
K_QA = 3
K_EV_BASE, K_EV_V6 = 5, R.K_EV_V6
PROMPTS = HERE / "prompts"
ITEMS = HERE / "prompts_items"     # 점검용 근거 글. 하위 에이전트에게는 주지 않는다
RAW = HERE / "drafts_raw"
DEV_GROUPS = {"G0001", "G0002", "G0005", "G0016", "G0017"}   # 개발용 5건이 속한 묶음: 시나리오에서 뺀다


def in_scope_words(q):
    return any(w in q for ws in R.FAM_WORDS.values() for w in ws) or any(w in q for w in SCOPE_EXTRA)


def top_sim(ret, q):
    return float(ret._scores(ret.v_ev, ret.m_ev, q).max())


def hold_reason(ret, q):
    if not in_scope_words(q):
        return "제도 판별 실패"
    s = top_sim(ret, q)
    if s < HOLD_SIM:
        return f"근거 유사도 {s:.3f} < {HOLD_SIM}"
    return ""


def cmd_threshold():
    """조정용 질의 중 제도가 판별된 것의 최소 유사도에서 0.01 을 빼고 0.01 단위로 내림"""
    split = R.load_split()
    ret = R.Retriever(R.CFG_V6)
    sims, fail = [], 0
    for r in R.read_csv(R.QNA):
        if split.get(r["group_id"]) != "조정용":
            continue
        q = r["제목"] + " " + r["질문"]
        if not in_scope_words(q):
            fail += 1
            continue
        sims.append(top_sim(ret, q))
    t = int((min(sims) - 0.01) * 100) / 100
    print(f"조정용 {len(sims) + fail}건: 제도 판별 실패 {fail}건, 판별된 {len(sims)}건의 최소 유사도 {min(sims):.3f} -> 기준값 {t}")


def question_text(r):
    return (r["제목"] + "\n" + r["질문"]) if r["제목"].strip() != r["질문"].strip() else r["질문"]


def cmd_scenarios():
    import openpyxl
    wb = openpyxl.load_workbook(HERE / "02_민원질의_상세분류_검증.xlsx", read_only=True)
    typ = {r[0]: r[4] for r in list(wb.worksheets[0].iter_rows(values_only=True))[2:] if r[0]}
    split = R.load_split()
    first = {}
    for r in R.read_csv(R.QNA):
        if split.get(r["group_id"]) == "확인용" and r["group_id"] not in DEV_GROUPS:
            first.setdefault(r["group_id"], r)
    by_type = {}
    for g in sorted(first):
        by_type.setdefault(typ.get(g, "미분류"), []).append(g)
    rnd = random.Random(R.SEED)
    for t in by_type:
        rnd.shuffle(by_type[t])
    picked = []
    while len(picked) < 20 and any(by_type.values()):     # 유형을 돌아가며 하나씩
        for t in sorted(by_type):
            if by_type[t] and len(picked) < 20:
                picked.append((t, by_type[t].pop(0)))
    with open(HERE / "시나리오_20건.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["번호", "group_id", "faqNo", "서비스", "유형명", "질문"])
        for i, (t, g) in enumerate(picked, 1):
            r = first[g]
            w.writerow([f"S{i:02d}", g, r["faqNo"], r["서비스"], t, question_text(r)])
    print(f"확인용 {len(first)}묶음(개발용 묶음 제외)에서 {len(picked)}건, 유형 {len({t for t, _ in picked})}종")


def targets():
    out = [(r["번호"], r["질문"], r["group_id"]) for r in R.read_csv(HERE / "시나리오_20건.csv")]
    out += [(r["번호"], r["질문"], None) for r in R.read_csv(HERE / "범위밖_5건.csv")]
    return out


RR = HERE / "rerank"


def rerank_search(ret, q, skip):
    """v6r: 후보 20개를 뽑아 재순위 결과(rerank/out/시나리오/<번호>.json)로 줄 세운 뒤 위 8개 + 고정 고시"""
    import 재순위 as X
    qa, ev = ret.search(q, k_qa=K_QA, k_ev=X.N_CAND, skip_groups=skip)
    return qa, ev[:X.N_CAND], ev[X.N_CAND:]


def cmd_rerank_make():
    """보류되지 않은 대상마다 재순위 프롬프트 -> rerank/prompts/시나리오/<번호>.txt"""
    import 재순위 as X
    v6 = R.Retriever(R.CFG_V6)
    d = RR / "prompts" / "시나리오"
    d.mkdir(parents=True, exist_ok=True)
    (RR / "out" / "시나리오").mkdir(parents=True, exist_ok=True)
    n = 0
    for tid, q, g in targets():
        if hold_reason(v6, q):
            continue
        _, cand, _ = rerank_search(v6, q, {g} if g else None)
        (d / f"{tid}.txt").write_text(X.prompt_text(q, cand), encoding="utf-8")
        n += 1
    print(f"재순위 프롬프트 {n}개 -> {d}")


def cmd_prompts_rerank():
    """v6r 프롬프트: v6 지시문 + 재순위한 근거 8개 (+ 검색이 덧붙인 고정 고시)"""
    import 재순위 as X
    v6 = R.Retriever(R.CFG_V6)
    idx = {e["id"]: e for e in v6.ev}
    for sub in (PROMPTS / "v6r", ITEMS / "v6r"):
        sub.mkdir(parents=True, exist_ok=True)
    meta = [m for m in R.read_csv(PROMPTS / "목록.csv") if m["설정"] != "v6r"]
    for tid, q, g in targets():
        reason = hold_reason(v6, q)
        skip = {g} if g else None
        qa, cand, extra = rerank_search(v6, q, skip)
        if reason:
            ev = []
        else:
            order = X.apply_order([e["id"] for e, _ in cand], (RR / "out" / "시나리오" / f"{tid}.json").read_text(encoding="utf-8"))
            ev = [(dict(idx[i], cited_by=[]), 0.0) for i in order[:K_EV_V6]]
            # 재순위가 금액 고시를 밀어내지 않도록: 원래 v6 검색(근거 8개)에 들어 있던 고시는 모두 뒤에 붙인다
            have = {e["id"] for e, _ in ev}
            _, ev8 = v6.search(q, k_qa=K_QA, k_ev=K_EV_V6, skip_groups=skip)
            ev += [x for x in ev8 if x[0]["id"].startswith("G-") and x[0]["id"] not in have]
        prompt, items = build_prompt(q, qa, ev)
        (ITEMS / "v6r" / f"{tid}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        p = PROMPTS / "v6r" / f"{tid}.txt"
        if reason:
            if p.exists():
                p.unlink()
        else:
            p.write_text("=== 시스템 지시문 ===\n" + SYSTEM_V6 + "\n\n=== 사용자 입력 ===\n" + prompt, encoding="utf-8")
        meta.append({"번호": tid, "설정": "v6r", "보류": reason, "찾은_질의": ", ".join(f'Q-{r["faqNo"]}' for r, _ in qa),
                     "찾은_근거": ", ".join(e["id"] for e, _ in ev), "근거개수설정": f"{K_QA}/{K_EV_V6}(재순위 20→8)"})
    with open(PROMPTS / "목록.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(meta[0].keys()))
        w.writeheader()
        w.writerows(meta)
    print("v6r 프롬프트:", len(list((PROMPTS / "v6r").glob("*.txt"))))


# ---- v6j: v6r 근거 + 보건복지부 사업안내(지침) -------------------------------------
JICHIM = HERE / "data" / "지침_전체_2026.csv"
K_JICHIM = 3
ADD_JICHIM = """
13. J-숫자는 보건복지부 사업안내(업무 지침)입니다. 법령(L-)·고시(G-)와 같은 방식으로 문장 끝에 번호를 붙여 인용합니다. 지침과 법령·고시가 다르면 법령·고시를 따릅니다. 지침에는 시행일자가 없으므로 참고 자료에 적힌 지침 이름을 써서 "2026년 ○○ 사업안내 기준"처럼 씁니다."""
SYSTEM_V6J = SYSTEM_V6 + "\n" + ADD_JICHIM


class Jichim:
    """제도별 지침(기초연금·국민기초생활보장·긴급복지지원) 쪽마다 글자 조각 TF-IDF.
    질문의 제도가 하나로 판별되면 그 제도 지침에서만 위 3쪽을 고른다. 색인은 제도마다 따로 만든다."""
    def __init__(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.by = {}
        for r in R.read_csv(JICHIM):
            self.by.setdefault(r["제도"], []).append(r)
        self.idx = {}
        for fam, rows in self.by.items():
            v = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
            self.idx[fam] = (v, v.fit_transform([R.norm(r["본문"]) for r in rows]))

    def top(self, q, fam, k=K_JICHIM):
        if fam not in self.idx:
            return []
        v, m = self.idx[fam]
        rows = self.by[fam]
        sc = (m @ v.transform([R.norm(q)]).T).toarray().ravel()
        return [({"id": rows[i]["chunk_id"], "src": "지침", "name": rows[i]["지침명"], "pos": rows[i]["위치"], "title": "",
                  "date": rows[i]["시행일자"], "text": rows[i]["본문"], "cited_by": []}, float(sc[i]))
                for i in (int(j) for j in sc.argsort()[::-1][:k])]


def cmd_prompts_jichim():
    """v6j 프롬프트: v6r 근거(목록.csv 의 v6r 줄과 같은 번호) + 기초연금 질문이면 지침 3쪽"""
    v6 = R.Retriever(R.CFG_V6)
    idx = {e["id"]: e for e in v6.ev}
    jc = Jichim()
    meta = {(m["번호"], m["설정"]): m for m in R.read_csv(PROMPTS / "목록.csv")}
    for sub in (PROMPTS / "v6j", ITEMS / "v6j"):
        sub.mkdir(parents=True, exist_ok=True)
    rows = [m for m in meta.values() if m["설정"] != "v6j"]
    made = []
    for tid, q, g in targets():
        m = meta[(tid, "v6r")]
        fam = R.question_family(q)
        if m["보류"] or fam is None:
            continue
        qa, _ = v6.search(q, k_qa=K_QA, k_ev=K_EV_V6, skip_groups={g} if g else None)
        ev = [(dict(idx[i], cited_by=[]), 0.0) for i in m["찾은_근거"].split(", ") if i]
        ev += jc.top(q, fam)
        prompt, items = build_prompt(q, qa, ev)
        (ITEMS / "v6j" / f"{tid}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        (PROMPTS / "v6j" / f"{tid}.txt").write_text("=== 시스템 지시문 ===\n" + SYSTEM_V6J + "\n\n=== 사용자 입력 ===\n" + prompt, encoding="utf-8")
        rows.append({"번호": tid, "설정": "v6j", "보류": "", "찾은_질의": ", ".join(f'Q-{r["faqNo"]}' for r, _ in qa),
                     "찾은_근거": ", ".join(e["id"] for e, _ in ev), "근거개수설정": f"{K_QA}/{K_EV_V6}(재순위)+지침{K_JICHIM}"})
        made.append(tid)
    with open(PROMPTS / "목록.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("v6j 프롬프트(제도가 하나로 판별된 질문):", made)


def cmd_prompts():
    base = R.Retriever(R.CFG_BASE)
    v6 = R.Retriever(R.CFG_V6)
    meta = []
    for var in ("기준", "v6"):
        (PROMPTS / var).mkdir(parents=True, exist_ok=True)
        (ITEMS / var).mkdir(parents=True, exist_ok=True)
    for tid, q, g in targets():
        skip = {g} if g else None
        for var, ret, k_ev, system in (("기준", base, K_EV_BASE, SYSTEM), ("v6", v6, K_EV_V6, SYSTEM_V6)):
            reason = hold_reason(v6, q) if var == "v6" else ""
            qa, ev = ret.search(q, k_qa=K_QA, k_ev=k_ev, skip_groups=skip)
            prompt, items = build_prompt(q, qa, ev)
            (ITEMS / var / f"{tid}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            p = PROMPTS / var / f"{tid}.txt"
            if reason:
                if p.exists():
                    p.unlink()
            else:
                p.write_text("=== 시스템 지시문 ===\n" + system + "\n\n=== 사용자 입력 ===\n" + prompt, encoding="utf-8")
            meta.append({"번호": tid, "설정": var, "보류": reason, "찾은_질의": ", ".join(f'Q-{r["faqNo"]}' for r, _ in qa),
                         "찾은_근거": ", ".join(e["id"] for e, _ in ev), "근거개수설정": f"{K_QA}/{k_ev}"})
    with open(PROMPTS / "목록.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(meta[0].keys()))
        w.writeheader()
        w.writerows(meta)
    print("프롬프트:", {v: len(list((PROMPTS / v).glob("*.txt"))) for v in ("기준", "v6")},
          "| v6 보류:", [m["번호"] + " " + m["보류"] for m in meta if m["보류"]])


def cmd_collect():
    meta = {(m["번호"], m["설정"]): m for m in R.read_csv(PROMPTS / "목록.csv")}
    qs = {tid: q for tid, q, _ in targets()}
    for var, out_name, ver in (("기준", "drafts_기준.csv", "v5.2(generate.py SYSTEM)"), ("v6", "drafts_v6.csv", INSTRUCTION_VERSION),
                               ("v6r", "drafts_v6r.csv", INSTRUCTION_VERSION + "+재순위"),
                               ("v6j", "drafts_v6j.csv", INSTRUCTION_VERSION + "+재순위+지침")):
        if not (PROMPTS / var).exists():
            continue
        rows = []
        for tid, q in qs.items():
            m = meta.get((tid, var))
            if m is None:          # v6j 처럼 일부 질문만 만든 설정
                continue
            items = json.loads((ITEMS / var / f"{tid}.json").read_text(encoding="utf-8"))
            if m["보류"]:
                d = {"요지": "", "유형": "범위 밖 보류", "답변초안": HOLD_TEXT, "확인필요": [m["보류"]]}
                model = "규칙(모델 호출 없음)"
            else:
                rp = RAW / var / f"{tid}.json"
                if not rp.exists():
                    print("  초안 없음:", var, tid)
                    continue
                txt = rp.read_text(encoding="utf-8").strip()
                try:
                    d = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
                except ValueError:
                    d = {"요지": "", "유형": "", "답변초안": txt, "확인필요": ["JSON 형식 아님: 직접 확인"]}
                model = MODEL_NAME
            res = check(d.get("답변초안", ""), items)
            rows.append({"번호": tid, "질문": q, "요지": d.get("요지", ""), "유형": d.get("유형", ""),
                         "답변초안": d.get("답변초안", ""), "확인필요": "; ".join(d.get("확인필요", []) or []),
                         "찾은_질의": m["찾은_질의"], "찾은_근거": m["찾은_근거"], "모델": model, "지시문버전": ver,
                         "검색설정(질의/근거 개수)": m["근거개수설정"], "보류사유": m["보류"],
                         **{"점검_" + k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in res.items()}})
        with open(HERE / out_name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        for part, pre in (("시나리오 20건", "S"), ("범위 밖 5건", "X")):
            rs = [r for r in rows if r["번호"].startswith(pre)]
            if not rs:
                continue
            cnt = {s: sum(r["점검_자동점검"] == s for r in rs) for s in ("통과", "확인필요", "근거부족", "범위밖")}
            print(f"{var} {part}: {len(rs)}건 {cnt}")


def cmd_dry(q):
    v6 = R.Retriever(R.CFG_V6)
    reason = hold_reason(v6, q)
    if reason:
        print(HOLD_TEXT, "(", reason, ")")
        return
    qa, ev = v6.search(q, k_qa=K_QA, k_ev=K_EV_V6)
    print("=== 시스템 지시문 ===\n" + SYSTEM_V6 + "\n\n=== 사용자 입력 ===\n" + build_prompt(q, qa, ev)[0])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    cmds = {"threshold": cmd_threshold, "scenarios": cmd_scenarios, "prompts": cmd_prompts, "collect": cmd_collect,
            "rerank_make": cmd_rerank_make, "prompts_rerank": cmd_prompts_rerank, "prompts_jichim": cmd_prompts_jichim}
    if a[:1] == ["dry"] and len(a) > 1:
        cmd_dry(" ".join(a[1:]))
    elif a[:1] and a[0] in cmds:
        cmds[a[0]]()
    else:
        print(__doc__)
