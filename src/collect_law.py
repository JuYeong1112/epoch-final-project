"""법령 본문 수집 + 조문 단위로 자르기 (법제처 국가법령정보 공동활용 OPEN API)

준비
  환경변수 LAW_OC 에 신청 때 정한 인증키(OC)를 넣는다.
    PowerShell : $env:LAW_OC="인증키"
    macOS/Linux: export LAW_OC="인증키"
  (인증키를 파일 안에 쓰거나 팀 채팅·공유 Drive 에 올리지 않는다.)

실행
  python src/collect_law.py            # 아래 LAWS 전부 수집 -> law/raw, law/chunks.csv
  python src/collect_law.py --only 기초연금법

출력
  law/raw/{법령명}.json   법제처 원본 응답 (공유 Drive 에 올리지 않아도 됨)
  law/chunks.csv          조문 1개 = 1행 (근거 문단). 열:
                          chunk_id, 법령명, 조문, 조문제목, 시행일자, 본문
  logs/law_report.txt     법령별 조문 수, 시행일자, 실패 목록
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

LAWS = [
    "기초연금법", "기초연금법 시행령", "기초연금법 시행규칙",
    "국민기초생활 보장법", "국민기초생활 보장법 시행령", "국민기초생활 보장법 시행규칙",
    "긴급복지지원법", "긴급복지지원법 시행령", "긴급복지지원법 시행규칙",
]
URLS = ["https://www.law.go.kr/DRF/lawService.do", "http://www.law.go.kr/DRF/lawService.do"]

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data"
RAW = BASE / "law" / "raw"
OUT = BASE / "law"
LOGS = ROOT / "logs"
for d in (RAW, OUT, LOGS):
    d.mkdir(parents=True, exist_ok=True)


def as_list(x):
    if x is None or x == "":
        return []
    return x if isinstance(x, list) else [x]


def flat(x):
    """문자열 또는 문자열 리스트(중첩 가능)를 줄바꿈으로 이어 붙인다."""
    if isinstance(x, list):
        return "\n".join(flat(i) for i in x if i not in (None, ""))
    return str(x or "").strip()


def mok_text(m):
    return flat(m.get("목내용")) if isinstance(m, dict) else flat(m)


def ho_text(h):
    if not isinstance(h, dict):
        return flat(h)
    parts = [flat(h.get("호내용"))]
    for m in as_list(h.get("목")):
        parts.append("  " + mok_text(m))
    return "\n".join(p for p in parts if p)


def hang_text(h):
    """항 한 개(또는 호만 있는 항 dict)를 글로 만든다."""
    if not isinstance(h, dict):
        return flat(h)
    parts = []
    if h.get("항내용"):
        parts.append(flat(h.get("항내용")))
    for ho in as_list(h.get("호")):
        parts.append(" " + ho_text(ho))
    return "\n".join(p for p in parts if p)


def article_label(u):
    no = str(u.get("조문번호", "")).strip()
    br = str(u.get("조문가지번호", "")).strip()
    return f"제{no}조" + (f"의{br}" if br and br != "0" else "")


def parse_law(data: dict, law_name: str):
    """법제처 JSON -> (시행일자, [chunk dict])"""
    law = data.get("법령") or {}
    info = law.get("기본정보") or {}
    eff = str(info.get("시행일자", ""))
    units = as_list((law.get("조문") or {}).get("조문단위"))
    chunks = []
    for u in units:
        if u.get("조문여부") != "조문":   # '전문'(장·절 제목)은 건너뜀
            continue
        body = [flat(u.get("조문내용"))]
        for h in as_list(u.get("항")):
            body.append(hang_text(h))
        # 항 없이 호만 있는 경우 (dict 형태) 도 hang_text 가 처리
        text = "\n".join(b for b in body if b).strip()
        chunks.append({
            "법령명": law_name,
            "조문": article_label(u),
            "조문제목": u.get("조문제목", ""),
            "시행일자": u.get("조문시행일자") or eff,
            "본문": text,
        })
    return eff, chunks


def fetch(law_name: str, oc: str) -> dict:
    last = None
    for url in URLS:
        try:
            r = requests.get(url, params={"OC": oc, "target": "law", "LM": law_name, "type": "JSON"}, timeout=40)
            r.raise_for_status()
            data = r.json()
            if "법령" in data:
                return data
            last = RuntimeError("응답에 '법령'이 없음: " + json.dumps(data, ensure_ascii=False)[:300])
        except (requests.RequestException, ValueError) as e:
            last = e
    raise RuntimeError(f"{law_name}: {last}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", nargs="+", help="이 법령명만 수집")
    p.add_argument("--from-file", help="(시험용) 이미 받은 JSON 파일로 파싱만 한다. 법령명은 --only 로 지정")
    a = p.parse_args()

    oc = os.environ.get("LAW_OC", "").strip()
    if not oc and not a.from_file:
        sys.exit("환경변수 LAW_OC 가 없습니다.")

    names = a.only or LAWS
    all_chunks, rep, fails = [], [], []
    for name in names:
        try:
            if a.from_file:
                data = json.loads(Path(a.from_file).read_text(encoding="utf-8"))
            else:
                data = fetch(name, oc)
                (RAW / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
                time.sleep(0.3)
            got_name = ((data.get("법령") or {}).get("기본정보") or {}).get("법령명_한글", "")
            eff, ch = parse_law(data, name)
            if got_name and got_name != name:
                fails.append(f"{name}: 응답 법령명이 다름 -> {got_name} (이름 확인 필요)")
            all_chunks += ch
            rep.append(f"{name}: 응답 법령명={got_name}, 시행일자={eff}, 조문 {len(ch)}개")
            print(rep[-1])
        except Exception as e:  # 한 법령이 실패해도 나머지는 계속
            fails.append(f"{name}: {e}")
            print("  ! 실패:", e)

    for i, c in enumerate(all_chunks, 1):
        c["chunk_id"] = f"L-{i:04d}"
    cols = ["chunk_id", "법령명", "조문", "조문제목", "시행일자", "본문"]
    with open(OUT / "chunks.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(all_chunks)
    lines = rep + (["", "실패/확인 필요:"] + fails if fails else [])
    lines.append(f"\n총 조문 {len(all_chunks)}개 -> {OUT/'chunks.csv'}")
    (LOGS / "law_report.txt").write_text("\n".join(lines), encoding="utf-8")
    print(lines[-1])
    if fails:
        print("실패/확인 필요 항목이 있습니다. logs/law_report.txt 를 보세요.")


if __name__ == "__main__":
    main()
