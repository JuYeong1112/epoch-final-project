"""검색 장치 v2: retrieve.py 를 그대로 옮기고, 개선 스위치와 '잠금 점검 모드'를 더했다.

retrieve.py 와 다른 점
  - 모든 스위치를 끈 설정(CFG_BASE)에서는 retrieve.py 와 결과가 같다 (python retrieve_v2.py same 으로 확인).
  - 의미 검색(임베딩, OpenAI 호출) 부분은 뺐다. 이미 효과가 없다고 확인됐고, 외부 API 를 부르지 않기 위해서다.
    retrieve.py 에서도 HYB=0(기본값)이면 이 부분은 쓰이지 않으므로 결과는 같다.
  - 평가용(eval_groups.txt)과 평가예비(eval_reserve.txt) 묶음은 언제나 검색 대상에서 뺀다.

사용 (정확도개선_v6 폴더에서)
  python retrieve_v2.py split                 # 묶음분할.csv 만들기 (추첨 번호 20261118)
  python retrieve_v2.py audit base            # 기준값: 조정용/확인용 정답 조문 3·5·8개 안 비율 + 누락목록.csv
  python retrieve_v2.py audit v6              # 채택한 개선을 모두 켠 설정
  python retrieve_v2.py same                  # 스위치를 모두 끄면 retrieve.py 와 결과가 같은지 확인
  python retrieve_v2.py ask "질문"            # 채택한 설정(v6)으로 근거 찾기
"""
import csv
import random
import re
import sys
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # 저장소 맨 위 폴더
BASE = ROOT / "data"                     # 데이터는 원래 자리에서 읽기만 한다
QNA = BASE / "clean" / "qna_all.csv"
LAW = BASE / "law" / "chunks.csv"
ADM = BASE / "law" / "chunks_admrul.csv"
LOCK_FILES = [ROOT / "eval" / "groups" / "eval_groups.txt"]
EVAL_IDS = ROOT / "eval" / "groups" / "eval_group_ids_v1_1.csv"   # 평가예비 묶음은 이 표의 용도 열에서 읽는다
SPLIT = HERE / "묶음분할.csv"
DICT_FILE = HERE / "용어사전.csv"
SEED = 20261118

K_QA, K_EV = 3, 3
K_NEIGHBOR = 5
G_FAM_MIN = 0.08
OUR_LAWS = ["기초연금법", "국민기초생활 보장법", "긴급복지지원법"]

# ---- 개선 스위치 ------------------------------------------------------------
# dict   : 1-2 시민 표현 -> 법령 용어 사전을 법령 검색에도 적용 (True/False)
# fam    : 1-3 질문의 제도를 판별해 다른 제도 조문 점수에 곱하는 값 (1.0 = 감점 없음)
# title  : 1-4 조문 제목 유사도 가중치 (0 = 쓰지 않음)
# nb_min : 1-5 이 유사도 미만인 비슷한 질의는 인용 조문 가점에서 뺀다 (0 = 모두 씀)
# govname: 1-6 고정 고시를 번호(G-0002) 대신 고시 이름·위치로 찾는다
# --- 2차 실험 스위치 (2026-11-18 추가) ---
# nb_w   : 2-1 비슷한 질의 가점에 곱하는 값 (1.0 = 그대로)
# nb_norm: 2-2 비슷한 질의 하나의 가점을 그 질의가 인용한 조문 수의 이 제곱으로 나눈다 (0 = 나누지 않음)
# purpose: 2-3 조문 제목이 '목적'인 조문 점수에 곱하는 값 (1.0 = 그대로)
# k_nb   : 2-4 가점에 쓰는 비슷한 질의 수 (5 = 그대로)
CFG_BASE = {"dict": False, "fam": 1.0, "title": 0.0, "nb_min": 0.0, "govname": False,
            "nb_w": 1.0, "nb_norm": 0.0, "purpose": 1.0, "k_nb": K_NEIGHBOR,
            "docq": 0.0}   # 4-1: 조문별 시민질문(조문별_시민질문.csv) 유사도 가중치 (0 = 쓰지 않음)
CFG_V6 = dict(CFG_BASE)   # 실험 뒤 채택한 값으로 채운다 (아래 ADOPTED)
ADOPTED = {"dict": True, "fam": 0.2, "title": 0.3, "nb_min": 0.2, "govname": True,   # 1차 판정 (실험기록.csv)
           "nb_w": 0.5, "purpose": 0.5, "k_nb": 10}                                     # 2차 판정 (실험_검색2.py)
K_EV_V6 = 8   # 1-1 재판정으로 초안에 넘기는 근거 수를 8개로
CFG_V6.update(ADOPTED)


def read_csv(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def load_locked():
    out = set()
    for p in LOCK_FILES:
        if p.exists():
            out |= {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}
    if EVAL_IDS.exists():   # 평가용 + 평가예비 (group_id 와 용도 열만 읽는다)
        out |= {r["group_id"] for r in read_csv(EVAL_IDS) if r.get("용도", "").startswith("평가")}
    return out


def law_family(name):
    if name.startswith("기초연금법"):
        return "기초연금"
    if name.startswith("국민기초생활"):
        return "기초생활"
    if name.startswith("긴급복지지원법"):
        return "긴급복지"
    return None


def gov_family(name):
    if name.startswith("기초연금"):
        return "기초연금"
    if "기준 중위소득" in name or "생계" in name and "의료급여" in name:
        return "기초생활"
    if name.startswith("긴급"):
        return "긴급복지"
    return None


# 1-3 질문의 제도 판별에 쓰는 말 (제도 이름과 그 제도에만 나오는 급여 이름만 쓴다)
FAM_WORDS = {
    "기초연금": ["기초연금", "기초노령연금"],
    "기초생활": ["기초생활", "생계급여", "의료급여", "주거급여", "교육급여", "해산급여", "장제급여", "수급자", "수급권자",
                "기초수급", "자활", "부양의무", "기준 중위소득", "중위소득"],
    "긴급복지": ["긴급복지", "긴급지원", "긴급생계", "긴급의료", "위기상황", "위기사유"],
}


def question_family(q):
    """질문에 나온 제도 이름. 하나만 나오면 그 제도, 없거나 둘 이상이면 None."""
    hit = [f for f, ws in FAM_WORDS.items() if any(w in q for w in ws)]
    return hit[0] if len(hit) == 1 else None


SYN = [("지급기준", "선정기준액"), ("선정기준", "선정기준액"), ("지급 기준", "선정기준액"), ("기준연금", "기준연금액"),
       ("지원금액", "지원 금액"), ("선정 기준", "선정기준액")]


def expand_for_gov(q):
    add = [b for a, b in SYN if a in q and b not in q]
    return q + " " + " ".join(dict.fromkeys(add)) if add else q


def load_dict():
    if not DICT_FILE.exists():
        return []
    return [(r["시민표현"], r["법령용어"]) for r in read_csv(DICT_FILE) if r["시민표현"].strip() and r["법령용어"].strip()]


def expand_with_dict(q, pairs):
    add = [b for a, b in pairs if a in q and b not in q]
    return q + " " + " ".join(dict.fromkeys(add)) if add else q


PIN_NAME = {"긴급복지": ("지원금액", ["금액", "얼마", "인상", "지원금", "생계지원", "생계비", "재산"])}
# 1-6: 번호 대신 (고시 이름에 들어 있는 말, 위치)로 적는다. govname=False 이면 예전처럼 번호를 쓴다.
PIN_BY_NAME = {
    "기초연금": ([("기초연금 지급대상자", "제2조"), ("기초연금 지급대상자", "제3조")],
                ["금액", "얼마", "기준", "지급", "선정", "수급", "받을 수", "인상"]),
    "기초생활": ([("기준 중위소득", "1."), ("기준 중위소득", "2.")],
                ["금액", "얼마", "기준", "선정", "중위소득", "인상", "생계급여"]),
}
PIN_KEY_BY_NAME = [("의료급여", ("기준 중위소득", "4.")),
                   ("보장시설", ("기준 중위소득", "3.")),
                   ("기준 중위소득", ("기준 중위소득", "1.")),
                   ("중위소득", ("기준 중위소득", "1."))]
# retrieve.py 에 적힌 그대로의 번호 (govname=False)
PIN = {"기초연금": (["G-0002", "G-0003"], PIN_BY_NAME["기초연금"][1]),
       "기초생활": (["G-0017", "G-0022"], PIN_BY_NAME["기초생활"][1])}
PIN_KEY = [("의료급여", "G-0024"), ("보장시설", "G-0023"), ("기준 중위소득", "G-0017"), ("중위소득", "G-0017")]


def gold_of(row):
    out = set()
    for s in [x.strip() for x in row["근거조문"].split(";") if x.strip()]:
        m = re.match(r"(.+?)\s*/\s*(제\d+조(?:의\d+)?)", s)
        if m and any(m.group(1).strip().startswith(n) for n in OUR_LAWS):
            out.add((m.group(1).strip(), m.group(2)))
    return out


class Retriever:
    def __init__(self, cfg=None, exclude_groups=None):
        self.cfg = dict(CFG_BASE, **(cfg or {}))
        self.locked = load_locked()
        exclude = self.locked | (exclude_groups or set())   # 잠금 묶음은 항상 뺀다
        self.qa = [r for r in read_csv(QNA) if r["group_id"] not in exclude]
        self.ev = []
        for r in read_csv(LAW):
            self.ev.append({"id": r["chunk_id"], "src": "법령", "name": r["법령명"], "pos": r["조문"],
                            "title": r["조문제목"], "date": r["시행일자"], "text": r["본문"]})
        if ADM.exists():
            for r in read_csv(ADM):
                self.ev.append({"id": r["chunk_id"], "src": "고시", "name": r["고시명"], "pos": r["위치"],
                                "title": "", "date": r["시행일자"], "text": r["본문"]})
        self.idx = {e["id"]: i for i, e in enumerate(self.ev)}
        qa_docs = [norm(r["제목"] + " " + r["질문"]) for r in self.qa]
        ev_docs = [norm(f'{e["name"]} {e["pos"]} {e["title"]} {e["text"]}') for e in self.ev]
        self.v_qa = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
        self.m_qa = self.v_qa.fit_transform(qa_docs)
        self.v_ev = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
        self.m_ev = self.v_ev.fit_transform(ev_docs)
        # 1-4: 조문 제목만 따로 모은 색인 (고시는 제목이 없어 빈 글)
        self.v_ti = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
        self.m_ti = self.v_ti.fit_transform([norm(e["title"]) for e in self.ev])
        self.dict_pairs = load_dict() if self.cfg["dict"] else []
        # 4-1: 조문마다 붙인 시민 말투 질문을 모아 따로 색인 (조문 본문만 보고 만든 것, 질의응답은 보지 않음)
        self.m_dq = None
        if self.cfg["docq"] > 0:
            dq = {}
            for r in read_csv(HERE / "조문별_시민질문.csv"):
                dq.setdefault(r["chunk_id"], []).append(r["시민질문"])
            self.v_dq = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True)
            self.m_dq = self.v_dq.fit_transform([norm(" ".join(dq.get(e["id"], []))) for e in self.ev])

    def _scores(self, vec, mat, q):
        return (mat @ vec.transform([norm(q)]).T).toarray().ravel()

    def _gov(self, key):
        """1-6: (고시 이름에 들어 있는 말, 위치의 앞부분) -> 고시 번호. 못 찾으면 None"""
        name_part, pos_part = key
        for e in self.ev:
            p = e["pos"].strip()
            if e["src"] == "고시" and name_part in e["name"] and (p == pos_part or p.startswith(pos_part + " ")):
                return e["id"]
        return None

    def family_of(self, question):
        return question_family(question)

    def search(self, question, k_qa=K_QA, k_ev=K_EV, skip_groups=None):
        c = self.cfg
        sq = self._scores(self.v_qa, self.m_qa, question)
        if skip_groups:
            for i, r in enumerate(self.qa):
                if r["group_id"] in skip_groups:
                    sq[i] = -1.0
        order = np.argsort(-sq)
        qa = [(self.qa[int(i)], float(sq[i])) for i in order[:k_qa]]
        se = self._scores(self.v_ev, self.m_ev, question)   # 고시 선택 규칙은 이 점수(사전 미적용)를 쓴다
        se_rank = se
        if self.dict_pairs:                                  # 1-2
            se_rank = self._scores(self.v_ev, self.m_ev, expand_with_dict(question, self.dict_pairs))
        if self.m_dq is not None:                            # 4-1
            se_rank = se_rank + c["docq"] * self._scores(self.v_dq, self.m_dq, question)
        if c["title"] > 0:                                   # 1-4
            se_rank = se_rank + c["title"] * self._scores(self.v_ti, self.m_ti, question)
        total = {int(i): float(se_rank[i]) for i in np.argsort(-se_rank)[:50]}
        why = {}
        key = {(e["name"], e["pos"]): i for i, e in enumerate(self.ev)}
        for i in order[:c["k_nb"]]:                          # 2-4
            if float(sq[i]) < c["nb_min"]:                   # 1-5
                continue
            r = self.qa[int(i)]
            golds = gold_of(r)
            bonus = float(sq[i]) * c["nb_w"] / (max(len(golds), 1) ** c["nb_norm"])   # 2-1, 2-2
            for g in golds:
                j = key.get(g)
                if j is not None:
                    total[j] = total.get(j, 0.0) + bonus
                    why.setdefault(j, []).append(r["faqNo"])
        if c["fam"] < 1.0:                                   # 1-3
            fam_q = question_family(question)
            if fam_q:
                for j in total:
                    f = law_family(self.ev[j]["name"]) or gov_family(self.ev[j]["name"])
                    if f and f != fam_q:
                        total[j] *= c["fam"]
        if c["purpose"] < 1.0:                               # 2-3
            for j in total:
                if self.ev[j]["title"].strip() == "목적":
                    total[j] *= c["purpose"]
        ranked = sorted(total.items(), key=lambda x: -x[1])[:k_ev]
        if c["govname"]:
            pin = {f: ([self._gov(k) for k in keys], kws) for f, (keys, kws) in PIN_BY_NAME.items()}
            pin_key = [(w, self._gov(k)) for w, k in PIN_KEY_BY_NAME]
        else:
            pin, pin_key = PIN, PIN_KEY
        if k_ev >= 3:
            have = {self.ev[j]["id"] for j, _ in ranked}
            for kw, gid in pin_key:
                if kw in question and gid in self.idx and gid not in have:
                    ranked = ranked + [(self.idx[gid], float(se[self.idx[gid]]))]
                    have.add(gid)
        if k_ev >= 3 and not any(self.ev[j]["id"].startswith("G-") for j, _ in ranked):
            fam_score = {}
            for j, s_ in ranked:
                f = law_family(self.ev[j]["name"])
                if f:
                    fam_score[f] = fam_score.get(f, 0.0) + s_
            if fam_score:
                fam = max(fam_score, key=fam_score.get)
                ids, kws = pin.get(fam, ([], []))
                if fam in PIN_NAME:
                    nkey, nkws = PIN_NAME[fam]
                    if any(w in question for w in nkws):
                        ids = [e["id"] for e in self.ev if e["id"].startswith("G-") and nkey in e["name"]][:4]
                        kws = nkws
                if ids and any(w in question for w in kws):
                    for gid in ids:
                        if gid in self.idx:
                            ranked = ranked + [(self.idx[gid], float(se[self.idx[gid]]))]
                    ids_added = True
                else:
                    ids_added = False
                gi = [i for i, e in enumerate(self.ev) if e["id"].startswith("G-") and gov_family(e["name"]) == fam and e["pos"] != "제1조"]
                if gi and not ids_added:
                    sg = self._scores(self.v_ev, self.m_ev, expand_for_gov(question))
                    best = max(gi, key=lambda i: sg[i])
                    if sg[best] >= G_FAM_MIN:
                        se = sg
                        ranked = ranked + [(best, float(se[best]))]
        ev = []
        for j, s_ in ranked:
            e = dict(self.ev[j])
            e["cited_by"] = why.get(j, [])
            ev.append((e, s_))
        return qa, ev


# ---- 묶음 분할 -------------------------------------------------------------
def cmd_split():
    locked = load_locked()
    rows = read_csv(QNA)
    groups = sorted({r["group_id"] for r in rows if r["핵심관련"] == "Y" and r["group_id"] not in locked})
    random.Random(SEED).shuffle(groups)
    half = len(groups) // 2
    part = {g: ("조정용" if i < half else "확인용") for i, g in enumerate(groups)}
    svc = {}
    for r in rows:
        svc.setdefault(r["group_id"], r["서비스"])
    with open(SPLIT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["group_id", "구분", "섞은순서", "서비스"])
        for i, g in enumerate(groups):
            w.writerow([g, part[g], i + 1, svc[g]])
    print(f"잠금 {len(locked)}묶음 제외, 핵심 {len(groups)}묶음 -> 조정용 {half} / 확인용 {len(groups) - half}  저장: {SPLIT.name}")


def load_split():
    return {r["group_id"]: r["구분"] for r in read_csv(SPLIT)}


# ---- 잠금 점검 모드 --------------------------------------------------------
def audit(cfg, write_miss=False, quiet=False):
    """조정용·확인용 각각: 정답 조문이 근거 3·5·8개 안에 든 질의 수. 자기 묶음은 검색에서 뺀다."""
    split = load_split()
    ret = Retriever(cfg)
    res = {p: {"n": 0, 3: 0, 5: 0, 8: 0} for p in ("조정용", "확인용")}
    miss = []
    for r in read_csv(QNA):
        p = split.get(r["group_id"])
        if p is None:   # 잠금 묶음·비핵심 묶음은 점검하지 않는다
            continue
        gold = gold_of(r)
        if not gold:
            continue
        _, ev = ret.search(r["제목"] + " " + r["질문"], k_ev=8, skip_groups={r["group_id"]})
        got = [(e["name"], e["pos"]) for e, _ in ev]
        res[p]["n"] += 1
        for k in (3, 5, 8):
            res[p][k] += any(g in got[:k] for g in gold)
        if not any(g in got[:5] for g in gold):
            miss.append({"구분": p, "group_id": r["group_id"], "faqNo": r["faqNo"], "서비스": r["서비스"],
                         "질문": norm(r["제목"] + " / " + r["질문"]),
                         "정답조문": "; ".join(f"{a} {b}" for a, b in sorted(gold)),
                         "찾은조문(5개)": "; ".join(f'{e["id"]} {e["name"]} {e["pos"]}' for e, _ in ev[:5])})
    if write_miss:
        miss.sort(key=lambda m: m["구분"] != "조정용")   # 조정용을 위에
        with open(HERE / "누락목록.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(miss[0].keys()))
            w.writeheader()
            w.writerows(miss)
    if not quiet:
        for p, v in res.items():
            n = v["n"]
            print(f"{p}: 정답 조문 있는 질의 {n}건 | 3개 안 {v[3]}/{n}={v[3] / n:.1%} | 5개 안 {v[5]}/{n}={v[5] / n:.1%} | 8개 안 {v[8]}/{n}={v[8] / n:.1%}")
        print(f"5개 안 누락: 조정용 {sum(m['구분'] == '조정용' for m in miss)}건, 확인용 {sum(m['구분'] == '확인용' for m in miss)}건")
    return res


def cmd_same():
    """스위치를 모두 끈 v2 가 retrieve.py 와 같은 근거를 같은 순서로 내는지, 잠금이 아닌 모든 질의로 확인"""
    sys.path.insert(0, str(ROOT / "src"))
    import retrieve as old
    locked = load_locked()
    a = old.Retriever(exclude_groups=locked)
    b = Retriever(CFG_BASE)
    diff = n = 0
    for r in read_csv(QNA):
        if r["group_id"] in locked:
            continue
        q = r["제목"] + " " + r["질문"]
        for k in (3, 5, 8):
            qa1, ev1 = a.search(q, k_ev=k, skip_groups={r["group_id"]})
            qa2, ev2 = b.search(q, k_ev=k, skip_groups={r["group_id"]})
            n += 1
            if [x["faqNo"] for x, _ in qa1] != [x["faqNo"] for x, _ in qa2] or [e["id"] for e, _ in ev1] != [e["id"] for e, _ in ev2]:
                diff += 1
    print(f"비교 {n}회(질의 x 근거 개수 3·5·8) 중 다른 결과 {diff}회")
    return diff


def cmd_ask(q):
    ret = Retriever(CFG_V6)
    qa, ev = ret.search(q, k_ev=5)
    print("제도 판별:", question_family(q) or "판별 못 함")
    for r, s in qa:
        print(f'[{s:.2f}] faqNo {r["faqNo"]} | {r["제목"][:50]}')
    for e, s in ev:
        print(f'[{s:.2f}] {e["id"]} {e["name"]} {e["pos"]} ({e["date"]}) | {e["text"][:60].replace(chr(10), " ")}')


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if a[:1] == ["split"]:
        cmd_split()
    elif a[:1] == ["audit"]:
        audit(CFG_V6 if a[1:2] == ["v6"] else CFG_BASE, write_miss=a[1:2] != ["v6"])
    elif a[:1] == ["same"]:
        cmd_same()
    elif a[:1] == ["ask"] and len(a) > 1:
        cmd_ask(" ".join(a[1:]))
    else:
        print(__doc__)
