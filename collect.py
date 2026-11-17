"""민원정책 질의응답 수집 스크립트 (국민권익위원회, 공공데이터포털 오픈API)

사용 전 준비
  1) 환경변수 DATA_GO_KR_KEY 에 인증키를 넣는다 (Encoding/Decoding 어느 쪽이든 동작).
  2) 아래 LIST_URL, ITEM_URL 을 미리보기 새 탭의 주소창에서 복사한 주소로 채운다.
     (물음표 앞까지만, serviceKey 등 변수는 빼고 붙여넣기)

사용 예
  # 1) 건수만 세어 보기 (호출 몇 번만 씀)
  python collect.py count --keywords 기초연금 생계급여 긴급복지 --types 1 2 3 --search-types 1 2

  # 2) 수집 (목록 -> 항목 본문). 이미 받은 건은 건너뜀
  python collect.py collect --keywords 기초연금 생계급여 긴급복지 --types 3 --search-types 2
"""
import argparse
import csv
import json
import math
import os
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests

# ----- 여기만 채우세요 -------------------------------------------------
LIST_URL = "https://apis.data.go.kr/1140100/CivilPolicyQnaService/PolicyQnaList"   # .../PolicyQnaList
ITEM_URL = "https://apis.data.go.kr/1140100/CivilPolicyQnaService/PolicyQnaItem"   # .../PolicyQnaItem
# -----------------------------------------------------------------------

BASE = Path(__file__).resolve().parent
RAW = BASE / "raw"
ITEMS = RAW / "items"
LISTS = RAW / "lists"
LOGS = BASE / "logs"
for d in (ITEMS, LISTS, LOGS):
    d.mkdir(parents=True, exist_ok=True)

SLEEP = 0.2          # 호출 사이 대기(초)
MAX_PAGES = 300      # 무한 반복 방지
CALLS = {"n": 0}     # 이번 실행에서 보낸 호출 수


def get_key() -> str:
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not key:
        sys.exit("환경변수 DATA_GO_KR_KEY 가 없습니다.")
    return unquote(key)  # Encoding 키를 넣어도 안전


def check_urls():
    for name, val in (("LIST_URL", LIST_URL), ("ITEM_URL", ITEM_URL)):
        if val.startswith("PASTE_"):
            sys.exit(f"{name} 을 채워 주세요 (파일 맨 위).")


def call(url: str, params: dict) -> dict:
    params = dict(params)
    params["serviceKey"] = get_key()
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=30)
            CALLS["n"] += 1
            time.sleep(SLEEP)
            r.raise_for_status()
            try:
                data = r.json()
            except ValueError:
                # JSON 이 아니면 (키 오류 등 XML 오류 응답) 내용을 그대로 보여 준다
                raise RuntimeError("JSON 이 아닌 응답: " + r.text[:300])
            return data
        except requests.RequestException as e:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def check_ok(data: dict, what: str) -> bool:
    """S00 이면 True, E03(조건에 맞는 데이터 없음)이면 False, 그 밖은 오류로 중단."""
    code = data.get("resultCode")
    if code == "S00":
        return True
    if code == "E03":
        return False
    raise RuntimeError(f"{what}: resultCode={code} message={data.get('resultMessage')}")


def list_params(keyword, typ, stype, d_from, d_to, page, per_page):
    return {
        "firstIndex": page,
        "recordCountPerPage": per_page,
        "type": typ,
        "keyword": keyword,
        "searchType": stype,
        "regFrom": d_from,
        "regTo": d_to,
    }


def cmd_count(a):
    check_urls()
    out = LOGS / "counts.csv"
    new = not out.exists()
    with open(out, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["keyword", "type", "searchType", "from", "to", "total", "latest_regDate"])
        for kw in a.keywords:
            for t in a.types:
                for s in a.search_types:
                    data = call(LIST_URL, list_params(kw, t, s, a.date_from, a.date_to, 1, 10))
                    ok = check_ok(data, f"count {kw} t={t} s={s}")
                    total = int(data.get("resultCount", 0)) if ok else 0
                    latest = ""
                    rl = data.get("resultList") or []
                    if rl:
                        latest = max(x.get("regDate", "") for x in rl)
                    row = [kw, t, s, a.date_from, a.date_to, total, latest]
                    w.writerow(row)
                    print(row)
    print(f"저장: {out}  (이번 실행 호출 수: {CALLS['n']})")


def fetch_list(kw, typ, stype, a):
    """한 조건의 목록 전체를 페이지 넘겨 가며 받는다."""
    got = {}
    page = 1
    total = None
    while page <= MAX_PAGES:
        data = call(LIST_URL, list_params(kw, typ, stype, a.date_from, a.date_to, page, a.per_page))
        if not check_ok(data, f"list {kw} t={typ} s={stype} p={page}"):
            if total is None:
                total = 0
            break
        if total is None:
            total = int(data.get("resultCount", 0))
        items = data.get("resultList") or []
        (LISTS / f"list_{kw}_t{typ}_s{stype}_p{page}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        if not items:
            break
        for it in items:
            got[(it["faqNo"], it["dutySctnNm"])] = it
        if len(got) >= total:
            break
        page += 1
    print(f"  목록 {kw} type={typ} searchType={stype}: 서버 total={total}, 받은 건수={len(got)}")
    if total is not None and len(got) < total:
        print("  ! 받은 건수가 total 보다 적습니다. per-page 최대값 제한이나 날짜 범위를 확인하세요.")
    return got


def cmd_collect(a):
    check_urls()
    index = {}  # (faqNo, duty) -> row
    for kw in a.keywords:
        for t in a.types:
            for s in a.search_types:
                got = fetch_list(kw, t, s, a)
                for key, it in got.items():
                    row = index.setdefault(key, {
                        "faqNo": it["faqNo"], "dutySctnNm": it["dutySctnNm"],
                        "title": it.get("title", ""), "ancName": it.get("ancName", ""),
                        "regDate": it.get("regDate", ""), "keywords": set()})
                    row["keywords"].add(kw)

    # 인덱스 저장 (서비스 태그를 나중에 쓰기 위해 누적 병합)
    idx_path = RAW / "index.json"
    old = {}
    if idx_path.exists():
        for r in json.loads(idx_path.read_text(encoding="utf-8")):
            old[(r["faqNo"], r["dutySctnNm"])] = r
    for key, r in index.items():
        if key in old:
            r["keywords"] |= set(old[key]["keywords"])
    for key, r in old.items():
        if key not in index:
            index[key] = {**r, "keywords": set(r["keywords"])}
    idx_path.write_text(json.dumps(
        [{**r, "keywords": sorted(r["keywords"])} for r in index.values()],
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"인덱스 {len(index)}건 -> {idx_path}")

    todo = [r for r in index.values() if not (ITEMS / f"{r['faqNo']}_{r['dutySctnNm']}.json").exists()]
    print(f"본문 조회 대상 {len(todo)}건 (이미 받은 {len(index) - len(todo)}건은 건너뜀)")
    if a.limit:
        todo = todo[: a.limit]
        print(f"--limit {a.limit} 적용")
    for i, r in enumerate(todo, 1):
        data = call(ITEM_URL, {"faqNo": r["faqNo"], "dutySctnNm": r["dutySctnNm"]})
        try:
            if not check_ok(data, f"item {r['faqNo']}"):
                print(f"  ! 본문 없음(E03) 건너뜀: {r['faqNo']}")
                continue
        except RuntimeError as e:
            print("  ! 건너뜀:", e)
            continue
        (ITEMS / f"{r['faqNo']}_{r['dutySctnNm']}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        if i % 10 == 0 or i == len(todo):
            print(f"  본문 {i}/{len(todo)}")
    print(f"완료. 이번 실행 호출 수: {CALLS['n']}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("count", "collect"):
        sp = sub.add_parser(name)
        sp.add_argument("--keywords", nargs="+", required=True)
        sp.add_argument("--types", nargs="+", default=["1"])
        sp.add_argument("--search-types", nargs="+", default=["1"])
        sp.add_argument("--from", dest="date_from", default="20240101")
        sp.add_argument("--to", dest="date_to", default="20261110")
        sp.add_argument("--per-page", type=int, default=10)
        if name == "collect":
            sp.add_argument("--limit", type=int, default=0, help="본문 조회 상한(시험용)")
    a = p.parse_args()
    {"count": cmd_count, "collect": cmd_collect}[a.cmd](a)


if __name__ == "__main__":
    main()
