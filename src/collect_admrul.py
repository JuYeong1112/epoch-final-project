"""고시(행정규칙) 본문 수집 + 근거 문단으로 자르기 (법제처 국가법령정보 공동활용 OPEN API)

collect_law.py 와 같은 인증키(OC)를 쓴다.
    PowerShell : $env:LAW_OC="인증키"

실행
  python src/collect_admrul.py

출력
  law/raw_admrul/{일련번호}.json   원본 응답
  law/chunks_admrul.csv            근거 문단 (G-0001 ...). 열:
                                   chunk_id, 고시명, 위치, 시행일자, 발령번호, 본문
  logs/admrul_report.txt           고시별 글자 수, 문단 수, 확인 필요 사항

※ 아래 TARGETS 의 일련번호는 목록 조회(lawSearch.do?target=admrul) 결과에서 가져온 값이다.
"""
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

# (표시용 이름, 행정규칙 일련번호, 비고)  - 목록 조회 결과(현행)에서 확인한 것
TARGETS = [
    ("기초연금 지급대상자 선정기준액, 기준연금액 및 소득인정액 산정 세부기준에 관한 고시", "2100000283276", "시행 2026-07-30"),
    ("2026년 기준 중위소득 및 생계·의료급여 선정기준과 최저보장수준", "2100000262584", "시행 2026-01-01"),
    ("긴급복지지원 금융재산 기준", "2100000269956", "시행 2026-01-01"),
    ("긴급복지지원 위기사유에 관한 고시", "2100000280438", "시행 2026-06-02"),
]
URLS = ["https://www.law.go.kr/DRF/lawService.do", "http://www.law.go.kr/DRF/lawService.do"]
MAX_CHARS = 900   # 문단 하나의 대략적인 최대 길이

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data"
RAW = BASE / "law" / "raw_admrul"
OUT = BASE / "law"
LOGS = ROOT / "logs"
for d in (RAW, OUT, LOGS):
    d.mkdir(parents=True, exist_ok=True)


def texts(x):
    """JSON 안의 글자를 순서대로 모은다 (문자열, 리스트, 딕셔너리 모두 처리)."""
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, list):
        out = []
        for i in x:
            out += texts(i)
        return out
    if isinstance(x, dict):
        out = []
        for v in x.values():
            out += texts(v)
        return out
    return [str(x)]


def find_key(x, key):
    """중첩 구조에서 key 를 가진 값을 모두 찾아 리스트로 돌려준다."""
    found = []
    if isinstance(x, dict):
        for k, v in x.items():
            if k == key:
                found.append(v)
            found += find_key(v, key)
    elif isinstance(x, list):
        for i in x:
            found += find_key(i, key)
    return found


IMG = re.compile(r"<img[^>]*>\s*(</img>)?", re.I)
IMG_MARK = "[표(이미지): 원문 표의 수치가 글자로 제공되지 않음, 원문 확인 필요]"


def to_lines(x):
    lines = []
    for t in texts(x):
        t = IMG.sub(IMG_MARK, str(t))
        for ln in t.replace("\r", "").split("\n"):
            ln = ln.rstrip()
            if ln.strip():
                lines.append(ln)
    return lines


ART = re.compile(r"^\s*제\d+조(의\d+)?\s*\(")


def split_lines(lines, label):
    """줄 목록을 문단으로 자른다. '제N조(' 로 시작하는 줄이 있으면 그 단위로 먼저 자른다."""
    blocks, cur = [], []
    has_art = any(ART.match(l) for l in lines)
    for ln in lines:
        if has_art and ART.match(ln) and cur:
            blocks.append(cur)
            cur = []
        cur.append(ln)
    if cur:
        blocks.append(cur)
    out = []
    for b in blocks:
        head = ART.match(b[0]).group(0).strip("( ").strip() if ART.match(b[0]) else None
        part, size, n = [], 0, 1
        for ln in b:
            if size + len(ln) > MAX_CHARS and part:
                out.append((head or label, n, "\n".join(part)))
                part, size, n = [], 0, n + 1
            part.append(ln)
            size += len(ln)
        if part:
            out.append((head or label, n, "\n".join(part)))
    return out


def fetch(sn: str, oc: str) -> dict:
    last = None
    for url in URLS:
        try:
            r = requests.get(url, params={"OC": oc, "target": "admrul", "ID": sn, "type": "JSON"}, timeout=40)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
    raise RuntimeError(f"ID {sn}: {last}")


def parse(data: dict, name: str, note: str):
    root = next(iter(data.values())) if len(data) == 1 and isinstance(next(iter(data.values())), dict) else data
    nm = (find_key(root, "행정규칙명") or [name])[0]
    eff = (find_key(root, "시행일자") or [""])[0]
    num = (find_key(root, "발령번호") or [""])[0]
    kind = (find_key(root, "조문형식여부") or [""])[0]
    cur = (find_key(root, "현행여부") or [""])[0]
    body_lines = to_lines(find_key(root, "조문내용"))
    chunks = []
    for label, n, text in split_lines(body_lines, "본문"):
        chunks.append((label if n == 1 else f"{label} ({n})", text))
    # 별표: 번호·제목을 앞에 붙여서 따로 자른다
    for b in find_key(root, "별표단위") + ([] if find_key(root, "별표단위") else find_key(root, "별표")):
        for item in (b if isinstance(b, list) else [b]):
            if not isinstance(item, dict):
                continue
            no = texts(item.get("별표번호"))
            title = texts(item.get("별표제목"))
            lab = "별표" + (no[0] if no else "") + (" " + title[0] if title else "")
            for l2, n, text in split_lines(to_lines(item.get("별표내용")), lab):
                chunks.append((l2 if n == 1 else f"{l2} ({n})", text))
    warn = []
    if cur and cur != "Y":
        warn.append(f"현행여부={cur}")
    n_img = sum(t.count(IMG_MARK) for _, t in chunks)
    if n_img:
        warn.append(f"표 이미지 {n_img}곳 (수치가 글자로 없음 -> 수작업 입력 필요)")
    if not chunks:
        warn.append("본문 문단이 하나도 없음 (응답 구조 확인 필요)")
    return nm, eff, num, kind, chunks, warn


def main():
    oc = os.environ.get("LAW_OC", "").strip()
    if not oc:
        sys.exit("환경변수 LAW_OC 가 없습니다.")
    rows, rep = [], []
    for name, sn, note in TARGETS:
        try:
            data = fetch(sn, oc)
            (RAW / f"{sn}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            time.sleep(0.3)
            nm, eff, num, kind, chunks, warn = parse(data, name, note)
            for pos, text in chunks:
                rows.append({"고시명": nm, "위치": pos, "시행일자": eff, "발령번호": num, "본문": text})
            total = sum(len(t) for _, t in chunks)
            rep.append(f"{nm} | 일련번호 {sn} | 시행일자 {eff} | 발령번호 {num} | 조문형식여부 {kind} | 문단 {len(chunks)}개 | 글자 {total}"
                       + (" | 확인필요: " + ", ".join(warn) if warn else ""))
            print(rep[-1])
        except Exception as e:
            rep.append(f"{name} | 일련번호 {sn} | 실패: {e}")
            print("  ! 실패:", e)
    for i, r in enumerate(rows, 1):
        r["chunk_id"] = f"G-{i:04d}"
    cols = ["chunk_id", "고시명", "위치", "시행일자", "발령번호", "본문"]
    with open(OUT / "chunks_admrul.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    rep.append(f"\n총 문단 {len(rows)}개 -> {OUT / 'chunks_admrul.csv'}")
    (LOGS / "admrul_report.txt").write_text("\n".join(rep), encoding="utf-8")
    print(rep[-1])


if __name__ == "__main__":
    main()
