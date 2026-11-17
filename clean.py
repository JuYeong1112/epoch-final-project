"""수집한 질의응답을 정리한다 (HTML 제거, 개인정보 마스킹, 중복 묶기, 근거 조문 추출).

입력 : raw/items/*.json, raw/index.json  (collect.py 결과)
출력 : clean/qna_all.csv      전체 행 (group_id 포함)  -> 평가 분리(같은 group 은 한쪽에만)에 쓴다
       clean/qna_groups.csv   묶음별 대표 행 + 중복 횟수 -> 유형 분류·평가 담당에게 넘기는 표
       logs/clean_report.txt  처리 통계, 마스킹 흔적

실행: python clean.py
"""
import csv
import html
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).resolve().parent
ITEMS = BASE / "raw" / "items"
INDEX = BASE / "raw" / "index.json"
OUT = BASE / "clean"
LOGS = BASE / "logs"
OUT.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

# 같은 묶음으로 볼 유사도 기준 (데이터를 보고 조정하세요)
Q_SIM = 0.85   # 질문 유사도
A_SIM = 0.90   # 답변 유사도

# 서비스별 관련도 판정에 쓰는 낱말 (필요하면 추가하세요)
SERVICE_TERMS = {
    "기초연금": ["기초연금"],
    "생계급여": ["생계급여", "생계 급여", "국민기초생활", "기초생활보장", "기초생활수급"],
    "긴급복지": ["긴급복지", "긴급 복지", "긴급지원", "위기상황"],
}

PII_PATTERNS = [
    ("전화", re.compile(r"(?<!\d)0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")),
    ("이메일", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("주민번호", re.compile(r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)")),
    ("긴숫자", re.compile(r"(?<!\d)\d{10,}(?!\d)")),
]
TAG = re.compile(r"<[^>]+>")
BR = re.compile(r"<\s*br\s*/?\s*>", re.I)


def clean_text(s: str) -> str:
    s = html.unescape(s or "")
    s = BR.sub("\n", s)
    s = TAG.sub("", s)
    s = html.unescape(s).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def mask(s: str, hits: list) -> str:
    for label, pat in PII_PATTERNS:
        def repl(m, label=label):
            hits.append((label, m.group(0)))
            return f"[{label}]"
        s = pat.sub(repl, s)
    return s


def norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s)


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def similar(a: str, b: str, th: float) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if abs(len(a) - len(b)) > 0.4 * max(len(a), len(b)):
        return False
    return SequenceMatcher(None, a, b).ratio() >= th


def main():
    idx = {}
    if INDEX.exists():
        for r in json.loads(INDEX.read_text(encoding="utf-8")):
            idx[(r["faqNo"], r["dutySctnNm"])] = r

    rows, pii_log = [], []
    for fp in sorted(ITEMS.glob("*.json")):
        d = json.loads(fp.read_text(encoding="utf-8")).get("resultData") or {}
        if not d:
            continue
        hits = []
        q = mask(clean_text(d.get("qstnCntnCl")), hits)
        a = mask(clean_text(d.get("ansCntnCl")), hits)
        t = mask(clean_text(d.get("qnaTitl")), hits)
        laws = d.get("lawList") or []
        law_names = "; ".join(x.get("fullName", "") for x in laws)
        meta = idx.get((d.get("faqNo"), d.get("dutySctnNm")), {})
        row = {
            "faqNo": d.get("faqNo"), "구분": d.get("dutySctnNm"),
            "서비스": ", ".join(meta.get("keywords", [])),
            "제목": t, "질문": q, "답변": a,
            "근거조문": law_names, "근거수": len(laws),
            "기관": d.get("ancName"), "부서": d.get("deptName"), "등록일": d.get("regDate"),
        }
        # 관련도: 제목·질문·근거 조문에 낱말이 있으면 '핵심', 답변에만 있으면 '답변만'
        rel, core = [], []
        for svc, terms in SERVICE_TERMS.items():
            in_core = any(tm in (t + q + law_names) for tm in terms)
            in_ans = any(tm in a for tm in terms)
            if in_core:
                rel.append(svc)
                core.append(svc)
            elif in_ans:
                rel.append(svc + "(답변만)")
        row["관련서비스"] = ", ".join(rel)
        row["핵심관련"] = "Y" if core else "N"
        rows.append(row)
        for label, val in hits:
            pii_log.append((d.get("faqNo"), label, val))

    n = len(rows)
    uf = UF(n)
    nq = [norm(r["질문"]) for r in rows]
    na = [norm(r["답변"]) for r in rows]
    for i in range(n):
        for j in range(i + 1, n):
            if similar(nq[i], nq[j], Q_SIM) or similar(na[i], na[j], A_SIM):
                uf.union(i, j)

    groups = {}
    for i, r in enumerate(rows):
        g = uf.find(i)
        groups.setdefault(g, []).append(i)
    gid_of = {}
    for k, (g, members) in enumerate(sorted(groups.items(), key=lambda kv: kv[1][0]), 1):
        for i in members:
            gid_of[i] = f"G{k:04d}"
    for i, r in enumerate(rows):
        r["group_id"] = gid_of[i]

    cols = ["group_id", "faqNo", "구분", "서비스", "관련서비스", "핵심관련", "제목", "질문", "답변", "근거조문", "근거수", "기관", "부서", "등록일"]
    with open(OUT / "qna_all.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    gcols = ["group_id", "중복횟수", "서비스", "관련서비스", "핵심관련", "대표_faqNo", "제목", "질문", "답변", "근거조문", "기관들", "faqNo들"]
    with open(OUT / "qna_groups.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=gcols)
        w.writeheader()
        for g, members in sorted(groups.items(), key=lambda kv: kv[1][0]):
            rep = max(members, key=lambda i: (rows[i]["근거수"], len(rows[i]["답변"])))
            r = rows[rep]
            svc = sorted({s for i in members for s in rows[i]["서비스"].split(", ") if s})
            w.writerow({
                "group_id": gid_of[members[0]], "중복횟수": len(members), "서비스": ", ".join(svc),
                "관련서비스": ", ".join(sorted({x for i in members for x in rows[i]["관련서비스"].split(", ") if x})),
                "핵심관련": "Y" if any(rows[i]["핵심관련"] == "Y" for i in members) else "N",
                "대표_faqNo": r["faqNo"], "제목": r["제목"], "질문": r["질문"], "답변": r["답변"],
                "근거조문": r["근거조문"],
                "기관들": "; ".join(sorted({rows[i]["기관"] or "" for i in members})),
                "faqNo들": "; ".join(rows[i]["faqNo"] for i in members),
            })

    with_law = sum(1 for r in rows if r["근거수"] > 0)
    rep_lines = [
        f"전체 행: {n}",
        f"묶음 수(중복 제거 후): {len(groups)}",
        f"근거 조문이 있는 행: {with_law}",
        f"핵심 관련 행(제목·질문·근거에 서비스 낱말): {sum(1 for r in rows if r['핵심관련']=='Y')}",
        f"핵심 관련 묶음 수: {sum(1 for g, m in groups.items() if any(rows[i]['핵심관련']=='Y' for i in m))}",
        f"개인정보 패턴 마스킹 횟수: {len(pii_log)}",
        "마스킹 상세 (faqNo, 종류) - 원문 값은 남기지 않음:",
    ] + [f"  {a} {b}" for a, b, c in pii_log]
    (LOGS / "clean_report.txt").write_text("\n".join(rep_lines), encoding="utf-8")
    print("\n".join(rep_lines[:6]))
    print(f"저장: {OUT/'qna_all.csv'}, {OUT/'qna_groups.csv'}")
    print("※ 이름 같은 개인정보는 자동 마스킹이 안 됩니다. 표본을 눈으로 확인하세요.")


if __name__ == "__main__":
    main()
