"""G-0017 (2026년 기준 중위소득 고시)을 항목별 4개 문단으로 쪼갠다. apply_tables.py 를 먼저 실행한 뒤에 쓴다.

  1. 기준 중위소득            -> G-0017 그대로 (id 유지)
  2. 생계급여의 선정기준 및 최저보장수준  -> 새 id (맨 끝 번호 다음)
  3. 보장시설 생계급여의 선정기준 및 최저보장수준 -> 새 id
  4. 의료급여의 선정기준 및 최저보장수준 -> 새 id

기존 id(G-0001 ~ )는 바뀌지 않는다. 한 번 쪼갠 뒤에는 다시 실행해도 아무 일도 하지 않는다.
※ collect_admrul.py 를 다시 실행하면 사라지므로, 그 뒤에는 apply_tables.py -> split_g0017.py 순서로 다시 실행한다.
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data"
CSV = BASE / "law" / "chunks_admrul.csv"
CID = "G-0017"
CUT = re.compile(r"\s*(?=(?:2\. 생계급여의 선정기준|3\. 보장시설 생계급여의|4\. 의료급여의 선정기준))")
HEAD = re.compile(r"^(\d)\. ([^　\n]{2,40}?)(?=\s*국민기초생활|\s*$)")


def label(part):
    n = part[:1]
    for key, name in (("보장시설", "보장시설 생계급여의 선정기준 및 최저보장수준"), ("의료급여", "의료급여의 선정기준 및 최저보장수준"),
                      ("생계급여", "생계급여의 선정기준 및 최저보장수준"), ("기준 중위소득", "기준 중위소득")):
        if part[3:20].startswith(key):
            return f"{n}. {name}"
    return part[:20]


def main():
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames
        rows = list(rd)
    t = next((r for r in rows if r["chunk_id"] == CID), None)
    if t is None:
        raise SystemExit(f"{CID} 없음")
    if "[표(이미지)" in t["본문"]:
        raise SystemExit("아직 이미지 표시가 남아 있습니다. apply_tables.py 를 먼저 실행하세요.")
    parts = [p.strip() for p in CUT.split(t["본문"]) if p.strip()]
    if len(parts) != 4:
        print(f"이미 쪼갰거나 구조가 다릅니다 (조각 {len(parts)}개). 아무것도 바꾸지 않았습니다.")
        return
    nmax = max(int(r["chunk_id"].split("-")[1]) for r in rows)
    t["본문"], t["위치"] = parts[0], label(parts[0])
    for i, p in enumerate(parts[1:], 1):
        rows.append({**t, "chunk_id": f"G-{nmax + i:04d}", "위치": label(p), "본문": p})
    with open(CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        if r["chunk_id"] == CID or int(r["chunk_id"].split("-")[1]) > nmax:
            print(r["chunk_id"], r["위치"], len(r["본문"]), "자")


if __name__ == "__main__":
    main()
