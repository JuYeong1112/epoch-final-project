"""고시 1건을 기존 law/chunks_admrul.csv 맨 끝에 추가한다. 기존 G-번호와 손으로 입력한 표는 그대로 둔다.

  1) 찾기   python add_admrul.py search "긴급지원 지원금액"
            -> 현행 고시 목록(일련번호, 이름, 시행일자)을 보여준다.
  2) 추가   python add_admrul.py add 일련번호
            -> 본문을 받아 새 G-번호로 끝에 붙인다. 같은 고시명이 이미 있으면 아무것도 하지 않는다.
            백업: law/chunks_admrul.bak3.csv
인증키는 collect_admrul.py 와 같다.  PowerShell:  $env:LAW_OC="인증키"
표가 이미지로만 있으면 "[표(이미지)...]" 표시가 남는다. 이 경우 원문 화면에서 숫자를 옮겨 입력해야 한다.
"""
import csv
import json
import os
import shutil
import sys

import requests

import collect_admrul as C

CSV = C.OUT / "chunks_admrul.csv"


def oc():
    k = os.environ.get("LAW_OC", "").strip()
    if not k:
        sys.exit("환경변수 LAW_OC 가 없습니다.")
    return k


def cmd_search(q):
    last = None
    for url in ["https://www.law.go.kr/DRF/lawSearch.do", "http://www.law.go.kr/DRF/lawSearch.do"]:
        try:
            r = requests.get(url, params={"OC": oc(), "target": "admrul", "type": "JSON", "query": q, "display": 30}, timeout=40)
            r.raise_for_status()
            data = r.json()
            break
        except (requests.RequestException, ValueError) as e:
            last = e
    else:
        sys.exit(f"검색 실패: {last}")
    items = C.find_key(data, "admrul")
    items = items[0] if items else []
    items = items if isinstance(items, list) else [items]
    if not items:
        print("결과 없음. 원본:", json.dumps(data, ensure_ascii=False)[:500])
    for it in items:
        print(it.get("행정규칙일련번호"), "|", it.get("행정규칙명"), "| 시행", it.get("시행일자"), "|", it.get("현행연혁구분"), "|", it.get("발령번호", ""))


def cmd_add(sn):
    data = C.fetch(sn, oc())
    (C.RAW / f"{sn}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    nm, eff, num, kind, chunks, warn = C.parse(data, "", "")
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols, rows = rd.fieldnames, list(rd)
    if any(r["고시명"] == nm for r in rows):
        print(f"'{nm}' 은(는) 이미 들어 있습니다. 아무것도 바꾸지 않았습니다.")
        return
    nmax = max(int(r["chunk_id"].split("-")[1]) for r in rows)
    shutil.copy(CSV, C.OUT / "chunks_admrul.bak3.csv")
    for i, (pos, text) in enumerate(chunks, 1):
        rows.append({"chunk_id": f"G-{nmax + i:04d}", "고시명": nm, "위치": pos, "시행일자": eff, "발령번호": num, "본문": text})
        print(f"G-{nmax + i:04d} | {pos} | {len(text)}자 | {text[:80]!r}")
    with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"{nm} (시행 {eff}) 문단 {len(chunks)}개 추가." + (" 확인필요: " + ", ".join(warn) if warn else ""))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "search":
        cmd_search(" ".join(sys.argv[2:]))
    elif len(sys.argv) == 3 and sys.argv[1] == "add":
        cmd_add(sys.argv[2])
    else:
        print(__doc__)
