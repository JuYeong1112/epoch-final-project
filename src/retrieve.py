"""검색 장치: 새 질문 -> 비슷한 질의응답 3건 + 근거 문단 3개

방식: 글자 2~4개 조각(char n-gram) TF-IDF + 코사인 유사도. 인터넷·API 키가 필요 없고 결과가 매번 같다.
근거 문단 순위 = (질문과 근거 문단의 직접 유사도) + (비슷한 질의 5건이 인용한 조문에 가점).
  -> 직접 유사도만 쓰면 정답 조문이 3개 안에 드는 비율이 25%, 비슷한 질의의 인용 조문을 더하면 64% (11/11 점검, 핵심 116건)

입력
  clean/qna_all.csv            질의응답 (group_id 포함)
  law/chunks.csv               법령 조문   (L-0001 ...)
  law/chunks_admrul.csv        고시 문단   (G-0001 ...)
  eval_groups.txt  (선택)      평가용 group_id 목록, 한 줄에 하나. 있으면 이 묶음은 '비슷한 질의' 검색 대상에서 뺀다.

사용
  python src/retrieve.py ask "부부가 기초연금을 받으면 금액이 줄어드나요?"
  python src/retrieve.py eval      # 검색 정확도 점검: 핵심 질의의 정답 조문이 근거 3개 안에 들어오는 비율

※ 평가용 목록(eval_groups.txt)이 확정되면 반드시 넣고 초안을 만든다 (데이터 누수 방지).
"""
import csv
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data"
QNA = BASE / "clean" / "qna_all.csv"
LAW = BASE / "law" / "chunks.csv"
ADM = BASE / "law" / "chunks_admrul.csv"
EVAL_FILE = ROOT / "eval" / "groups" / "eval_groups.txt"
EMB_DIR = BASE / "emb"
EMB_MODEL = os.environ.get("EMB_MODEL", "text-embedding-3-small")
HYB = float(os.environ.get("HYB", "0"))   # 0 = 글자 방식만(기존), 0.5 = 의미 검색 절반 섞음

K_QA, K_EV = 3, 3
K_NEIGHBOR = 5   # 근거 가점에 쓰는 비슷한 질의 수
G_FAM_MIN = float(os.environ.get("G_FAM_MIN","0.08"))  # 같은 묶음 안에서 고시 문단을 추가할 최소 직접 유사도
G_MIN = 0.25     # 고시 문단을 한 자리 넣을 최소 직접 유사도 (0.10 에서는 상관없는 고시가 들어와 0.25 로 올림, 아직 충분히 조정하지 않은 값)
OUR_LAWS = ["기초연금법", "국민기초생활 보장법", "긴급복지지원법"]  # 뒤에 시행령·시행규칙이 붙은 이름 포함


def read_csv(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


NOSPACE = os.environ.get("RETR_NOSPACE", "") == "1"   # 1 이면 공백을 모두 지우고 비교 ("선정 기준" = "선정기준")


def norm(s):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s.replace(" ", "") if NOSPACE else s


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


# 고시 문단을 고를 때만 쓰는 동의어 보충: 시민 질문의 말과 고시 조문 제목의 말이 달라서 생기는 누락을 줄인다.
SYN = [("지급기준", "선정기준액"), ("선정기준", "선정기준액"), ("지급 기준", "선정기준액"), ("기준연금", "기준연금액"),
       ("지원금액", "지원 금액"), ("선정 기준", "선정기준액")]


def expand_for_gov(q):
    add = [b for a, b in SYN if a in q and b not in q]
    return q + " " + " ".join(dict.fromkeys(add)) if add else q


# 연도별 기준 금액 고시: 금액·기준을 묻는 질문이면 해당 제도의 이 문단을 항상 후보에 넣는다 (짧은 조문이라 검색에서 밀리기 때문).
PIN_NAME = {   # 고시 이름에 이 글자가 들어 있는 문단을 모두(최대 4개) 고정: 긴급지원 가구별 지원금액 고시
    "긴급복지": ("지원금액", ["금액", "얼마", "인상", "지원금", "생계지원", "생계비", "재산"]),
}
PIN = {
    "기초연금": (["G-0002", "G-0003"], ["금액", "얼마", "기준", "지급", "선정", "수급", "받을 수", "인상"]),
    "기초생활": (["G-0017", "G-0022"], ["금액", "얼마", "기준", "선정", "중위소득", "인상", "생계급여"]),
}


# 질문에 특정 급여 이름이 있으면 그 급여의 고시 문단을 추가로 고정한다.
PIN_KEY = [("의료급여", "G-0024"), ("보장시설", "G-0023"), ("기준 중위소득", "G-0017"), ("중위소득", "G-0017")]


def load_eval_groups():
    if EVAL_FILE.exists():
        return {ln.strip() for ln in EVAL_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()}
    return set()


def embed_texts(texts, batch=64):
    """문장 목록 -> 벡터(행 단위로 길이 1). EMB_FAKE=1 이면 코드 시험용 가짜 벡터(실제 의미 없음)."""
    if os.environ.get("EMB_FAKE") == "1":
        from sklearn.feature_extraction.text import HashingVectorizer
        m = HashingVectorizer(analyzer="char_wb", ngram_range=(2, 3), n_features=512, norm="l2", alternate_sign=False)
        return m.transform(texts).toarray().astype("float32")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        sys.exit("환경변수 OPENAI_API_KEY 가 필요합니다.")
    import openai
    client = openai.OpenAI(api_key=key, max_retries=4, timeout=120.0)
    out = []
    for i in range(0, len(texts), batch):
        part = [t.replace("\n", " ")[:3500] or " " for t in texts[i:i + batch]]
        resp = client.embeddings.create(model=EMB_MODEL, input=part)
        out += [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]
        print(f"  임베딩 {min(i + batch, len(texts))}/{len(texts)}", flush=True)
    a = np.array(out, dtype="float32")
    return a / np.maximum(np.linalg.norm(a, axis=1, keepdims=True), 1e-9)


def _qcache_path():
    return EMB_DIR / "qcache.json"


def embed_query(text):
    """질문 1개의 벡터. 한 번 만든 것은 emb/qcache.json 에 저장해 같은 값을 다시 쓴다(재현성)."""
    EMB_DIR.mkdir(exist_ok=True)
    cache = json.loads(_qcache_path().read_text(encoding="utf-8")) if _qcache_path().exists() else {}
    k = EMB_MODEL + "|" + text
    if k not in cache:
        cache[k] = embed_texts([text])[0].tolist()
        _qcache_path().write_text(json.dumps(cache), encoding="utf-8")
    return np.array(cache[k], dtype="float32")


class Retriever:
    def __init__(self, exclude_groups=None, exclude_faq=None):
        exclude_groups = exclude_groups or set()
        exclude_faq = exclude_faq or set()
        qa = read_csv(QNA)
        keep = [i for i, r in enumerate(qa) if r["group_id"] not in exclude_groups and r["faqNo"] not in exclude_faq]
        self.qa = [qa[i] for i in keep]
        self.qa_all_ids = [r["faqNo"] for r in qa]
        # 질문이 제목과 같은 행은 질문 문장만 있고 정보가 적지만 예시로는 쓸 수 있다.
        self.ev = []
        for r in read_csv(LAW):
            self.ev.append({"id": r["chunk_id"], "src": "법령", "name": r["법령명"], "pos": r["조문"],
                            "title": r["조문제목"], "date": r["시행일자"], "text": r["본문"]})
        if ADM.exists():
            for r in read_csv(ADM):
                self.ev.append({"id": r["chunk_id"], "src": "고시", "name": r["고시명"], "pos": r["위치"],
                                "title": "", "date": r["시행일자"], "text": r["본문"]})
        qa_docs = [norm(r["제목"] + " " + r["질문"]) for r in self.qa]
        ev_docs = [norm(f'{e["name"]} {e["pos"]} {e["title"]} {e["text"]}') for e in self.ev]
        an = "char" if NOSPACE else "char_wb"
        self.v_qa = TfidfVectorizer(analyzer=an, ngram_range=(2, 4), sublinear_tf=True)
        self.m_qa = self.v_qa.fit_transform(qa_docs)
        self.v_ev = TfidfVectorizer(analyzer=an, ngram_range=(2, 4), sublinear_tf=True)
        self.m_ev = self.v_ev.fit_transform(ev_docs)
        self.hyb = HYB
        self.qa_emb = self.ev_emb = None
        self._load_emb(keep)

    def _load_emb(self, keep):
        """emb/ 폴더의 벡터를 불러온다. 문서 목록이 달라졌으면(데이터 변경) 쓰지 않는다."""
        meta_p = EMB_DIR / "meta.json"
        if not (meta_p.exists() and (EMB_DIR / "qa.npy").exists() and (EMB_DIR / "ev.npy").exists()):
            if self.hyb > 0:
                sys.exit("HYB > 0 인데 emb/ 벡터가 없습니다. 먼저 python src/embed_build.py 를 실행하세요.")
            return
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        ev_ids = [e["id"] for e in self.ev]
        if meta.get("qa_ids") != self.qa_all_ids or meta.get("ev_ids") != ev_ids:
            msg = "emb/ 벡터가 현재 데이터와 맞지 않습니다(질의응답·법령·고시가 바뀜). python src/embed_build.py 를 다시 실행하세요."
            if self.hyb > 0:
                sys.exit(msg)
            print("  [참고]", msg)
            return
        qa_all = np.load(EMB_DIR / "qa.npy")
        self.qa_emb = qa_all[keep]
        self.ev_emb = np.load(EMB_DIR / "ev.npy")
        self.emb_model = meta.get("model", "")

    @staticmethod
    def _emb_scores(mat, qv):
        c = np.clip(mat @ qv, 0, None)
        return c / max(float(c.max()), 1e-9)   # 가장 비슷한 것을 1 로 맞춘다

    def _scores(self, vec, mat, q):
        return (mat @ vec.transform([norm(q)]).T).toarray().ravel()

    def search(self, question, k_qa=K_QA, k_ev=K_EV, skip_groups=None, qvec=None):
        """skip_groups: 이번 검색에서만 뺄 group_id (점검용)"""
        sq = self._scores(self.v_qa, self.m_qa, question)
        se_tf = None
        h = self.hyb if self.qa_emb is not None else 0.0
        if h > 0:
            qv = qvec if qvec is not None else embed_query(question)
            sq = (1 - h) * sq + h * self._emb_scores(self.qa_emb, qv)
        if skip_groups:
            for i, r in enumerate(self.qa):
                if r["group_id"] in skip_groups:
                    sq[i] = -1.0
        order = np.argsort(-sq)
        qa = [(self.qa[int(i)], float(sq[i])) for i in order[:k_qa]]
        # 근거 문단: 직접 유사도 + 비슷한 질의가 인용한 조문 가점
        se = self._scores(self.v_ev, self.m_ev, question)   # 글자 방식 점수 (고시 선택 규칙은 이 점수를 쓴다)
        se_rank = (1 - h) * se + h * self._emb_scores(self.ev_emb, qv) if h > 0 else se
        total = {int(i): float(se_rank[i]) for i in np.argsort(-se_rank)[:50]}
        why = {}
        key = {(e["name"], e["pos"]): i for i, e in enumerate(self.ev)}
        for i in order[:K_NEIGHBOR]:
            r = self.qa[int(i)]
            for g in gold_of(r):
                j = key.get(g)
                if j is not None:
                    total[j] = total.get(j, 0.0) + float(sq[i])
                    why.setdefault(j, []).append(r["faqNo"])
        ranked = sorted(total.items(), key=lambda x: -x[1])[:k_ev]
        # 고시 문단 추가: 질의응답은 법령 조문만 인용하므로 고시는 가점이 없어 밀린다.
        # 근거 목록에 고시가 없으면, 상위 법령이 속한 '고시 묶음'(기초연금/기초생활보장/긴급복지) 안에서
        # 질문과 직접 유사도가 가장 높은 고시 문단 1개를 근거 맨 끝에 '추가'한다 (기존 근거는 밀어내지 않는다).
        if k_ev >= 3:
            idx0 = {e["id"]: i for i, e in enumerate(self.ev)}
            have = {self.ev[j]["id"] for j, _ in ranked}
            for kw, gid in PIN_KEY:
                if kw in question and gid in idx0 and gid not in have:
                    ranked = ranked + [(idx0[gid], float(se[idx0[gid]]))]
                    have.add(gid)
        if k_ev >= 3 and not any(self.ev[j]["id"].startswith("G-") for j, _ in ranked):
            fam_score = {}
            for j, s_ in ranked:
                f = law_family(self.ev[j]["name"])
                if f:
                    fam_score[f] = fam_score.get(f, 0.0) + s_
            if fam_score:
                fam = max(fam_score, key=fam_score.get)
                ids, kws = PIN.get(fam, ([], []))
                if fam in PIN_NAME:
                    nkey, nkws = PIN_NAME[fam]
                    if any(w in question for w in nkws):
                        ids = [e["id"] for e in self.ev if e["id"].startswith("G-") and nkey in e["name"]][:4]
                        kws = nkws
                if ids and any(w in question for w in kws):
                    idx = {e["id"]: i for i, e in enumerate(self.ev)}
                    for gid in ids:
                        if gid in idx:
                            ranked = ranked + [(idx[gid], float(se[idx[gid]]))]
                    ids_added = True
                else:
                    ids_added = False
                gi = [i for i, e in enumerate(self.ev) if e["id"].startswith("G-") and gov_family(e["name"]) == fam and e["pos"] != "제1조"]  # 제1조(목적)은 정보가 없어 제외
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


def cmd_ask(q):
    import os
    ret = Retriever(exclude_groups=load_eval_groups())
    qa, ev = ret.search(q, k_ev=int(os.environ.get("EVIDENCE_K", "3")))   # EVIDENCE_K=5 로 근거 5개까지 확인
    print("== 비슷한 질의응답 ==")
    for r, s in qa:
        print(f'[{s:.2f}] faqNo {r["faqNo"]} | {r["제목"][:50]}')
    print("== 근거 문단 ==")
    for e, s in ev:
        tag = f' (비슷한 질의 {",".join(e["cited_by"])} 가 인용)' if e["cited_by"] else ""
        print(f'[{s:.2f}] {e["id"]} {e["name"]} {e["pos"]} ({e["date"]}){tag} | {e["text"][:60].replace(chr(10), " ")}')


def gold_of(row):
    out = set()
    for s in [x.strip() for x in row["근거조문"].split(";") if x.strip()]:
        m = re.match(r"(.+?)\s*/\s*(제\d+조(?:의\d+)?)", s)
        if m and any(m.group(1).strip().startswith(n) for n in OUR_LAWS):
            out.add((m.group(1).strip(), m.group(2)))
    return out


def cmd_eval():
    """각 핵심 질의를 질문으로 넣고, 자기 자신(같은 묶음)은 검색 대상에서 빼고 근거를 찾는다.
    emb/ 벡터가 있으면 글자 방식과 의미 검색을 섞는 비율(HYB)을 여러 값으로 비교한다."""
    allq = read_csv(QNA)
    ret = Retriever()
    grid = [0.0]
    if ret.qa_emb is not None:
        grid = [0.0, 0.2, 0.35, 0.5, 0.7]
        if os.environ.get("HYB"):
            grid = [float(os.environ["HYB"])]
    for h in grid:
        ret.hyb = h
        tot = hit3 = hit5 = 0
        miss = []
        for idx, r in enumerate(allq):
            if r["핵심관련"] != "Y":
                continue
            gold = gold_of(r)
            if not gold:
                continue
            qv = ret.qa_emb[idx] if (h > 0 and ret.qa_emb is not None) else None
            _, ev = ret.search(r["제목"] + " " + r["질문"], k_ev=5, skip_groups={r["group_id"]}, qvec=qv)
            got = [(e["name"], e["pos"]) for e, _ in ev]
            tot += 1
            h3 = any(g in got[:3] for g in gold)
            h5 = any(g in got[:5] for g in gold)
            hit3 += h3
            hit5 += h5
            if not h5:
                miss.append((r["faqNo"], r["제목"][:40], sorted(gold)[:2], got[:3]))
        print(f"[HYB={h}] 정답 조문이 있는 핵심 질의 {tot}건 | 근거 3개 안: {hit3}/{tot} = {hit3 / tot:.0%} | 근거 5개 안: {hit5}/{tot} = {hit5 / tot:.0%}")
    if len(grid) == 1:
        print("5개 안에도 없는 예 (최대 8건):")
        for m in miss[:8]:
            print("  ", m)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "ask":
        cmd_ask(" ".join(sys.argv[2:]))
    elif len(sys.argv) >= 2 and sys.argv[1] == "eval":
        cmd_eval()
    else:
        print(__doc__)
