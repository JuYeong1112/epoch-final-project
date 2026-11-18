"""초안의 근거 번호 대조 v2 (check_citations.py 의 규칙은 그대로 두고 세 가지를 더했다)

더한 것
  A) 숫자도 조문도 없는 짧은 도입·연결 문장("다음과 같습니다", "...안내드립니다:")은 사실 문장으로 세지 않는다.
  B) 초안 문장에 적힌 연도(예: 2026년)가 그 문장이 인용한 법령·고시의 시행 연도 중 어느 것과도 다르면 '연도경고'.
     연도경고가 있으면 자동점검은 '확인필요'가 된다.
  C) 범위 밖 보류 문구(HOLD_TEXT)로 시작하는 초안은 판정을 '범위밖'으로 한다.

사용: python check_citations_v2.py test    # 작은 시험 문장으로 기존 판정이 깨지지 않았는지 확인
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from check_citations import NUM_RE, ART_RE, UNSURE_WORDS, squash  # 기존 규칙을 그대로 가져다 쓴다
from check_citations import split_sentences as _split_old

# 근거 번호 무늬: 기존 L·G·Q 에 J(보건복지부 사업안내 지침)를 더했다 (2026-11-18)
ID_RE = re.compile(r"\[((?:[LGQJ]-\d+)(?:\s*,\s*[LGQJ]-\d+)*)\]")


def split_sentences(text):
    """기존 문장 나누기 + 근거 번호만 떨어진 조각([J-0003] 등)을 앞 문장에 붙인다"""
    out = []
    for p in _split_old(text):
        if out and ID_RE.fullmatch(p.strip()):
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


HOLD_TEXT = "[범위 밖 보류] 이 질문은 기초연금·기초생활보장(생계급여 등)·긴급복지지원 안내 범위 밖이거나, 답할 근거를 찾지 못해 초안을 쓰지 않았습니다. 담당자 확인 필요."
INTRO_RE = re.compile(r"(다음과 같|아래와 같|안내(해)? ?드립니다|말씀드립니다|설명드립니다|알려 ?드립니다)")
YEAR_RE = re.compile(r"(20\d\d)년")
DATE_IN_ITEM = re.compile(r"시행일자 (\d{4})\d{4}")


def is_intro(body):
    """A) 숫자도 조문도 없고, 40자 이하이며, 도입·연결 표현이 있거나 쌍점(:)으로 끝나는 문장"""
    if re.search(r"\d", body) or ART_RE.search(body):
        return False
    return len(body) <= 40 and (bool(INTRO_RE.search(body)) or body.rstrip().endswith(":"))


def check(draft, items):
    if (draft or "").strip().startswith(HOLD_TEXT[:10]):
        return {"문장수": 0, "근거없는문장수": 0, "근거없는문장비율": None, "없는근거번호": [], "근거에없는숫자": [],
                "근거에없는조문": [], "사용한근거번호": [], "근거없는문장예": [], "확인필요문장수": 0,
                "도입문장수": 0, "연도경고": [], "자동점검": "범위밖"}
    sents = split_sentences(draft)
    used_ids, bad_ids = [], []
    uncited, num_missing, art_missing, year_warn = [], [], [], []
    n_fact = n_unsure = n_intro = 0
    for s in sents:
        ids = [i.strip() for m in ID_RE.findall(s) for i in m.split(",")]
        body = ID_RE.sub("", s).strip()
        used_ids += ids
        for i in ids:
            if i not in items and i not in bad_ids:
                bad_ids.append(i)
        if "담당자 확인 필요" in body:
            n_unsure += 1
        if any(w in body for w in UNSURE_WORDS) or len(body) < 6:
            continue
        if is_intro(body):            # A)
            n_intro += 1
            continue
        n_fact += 1
        if not ids:
            uncited.append(body[:60])
        raw_cited = " ".join(items.get(i, "") for i in ids) if ids else ""
        cited_text = squash(raw_cited)
        cited_nc = re.sub(r"(?<=\d),(?=\d)", "", raw_cited)
        for n in NUM_RE.findall(body):
            sq = squash(n)
            core = re.match(r"\d+(?:\.\d+)?", sq)
            if sq in cited_text:
                continue
            if core and sq.endswith(("원", "%", "퍼센트")) and re.search(r"(?<![\d.])" + re.escape(core.group(0)) + r"(?![\d])", cited_nc):
                continue
            num_missing.append(sq)
        for a in ART_RE.findall(body):
            if a not in cited_text:
                art_missing.append(a)
        # B) 연도 대조: 법령·고시(L-, G-)를 인용했고 문장에 '20xx년'이 있을 때만
        years = set(YEAR_RE.findall(body))
        eff = {y for i in ids if i[:2] in ("L-", "G-") for y in DATE_IN_ITEM.findall(items.get(i, ""))}   # J(지침)는 시행일자가 없어 대조하지 않음
        if years and eff and not (years & eff):
            year_warn.append(f'{"/".join(sorted(years))}년 ≠ 시행 {"/".join(sorted(eff))}년: {body[:40]}')
    ok = not (bad_ids or uncited or num_missing or art_missing or year_warn)
    status = "통과" if ok else "확인필요"
    if ok and n_fact == 0:
        status = "근거부족"
    return {
        "문장수": n_fact, "근거없는문장수": len(uncited),
        "근거없는문장비율": round(len(uncited) / n_fact, 3) if n_fact else None,
        "없는근거번호": bad_ids, "근거에없는숫자": sorted(set(num_missing)),
        "근거에없는조문": sorted(set(art_missing)),
        "사용한근거번호": sorted(set(used_ids)),
        "근거없는문장예": uncited[:3],
        "확인필요문장수": n_unsure,
        "도입문장수": n_intro, "연도경고": year_warn,
        "자동점검": status,
    }


def selftest():
    """기존 판정이 그대로인지 + 새 규칙이 동작하는지. 실패하면 AssertionError."""
    import check_citations as old
    items = {"L-0009": "기초연금법 제8조 시행일자 20260730 2026년 7월 30일 부부 기초연금액의 100분의 20을 감액한다",
             "G-0002": "기초연금 고시 제2조 시행일자 20260730 2026년 7월 30일 선정기준액 단독가구 2,470,000원",
             "Q-1": "기초연금은 주소지 읍면동에서 신청합니다"}
    same_cases = [   # 새 규칙이 건드리지 않는 문장: 옛 판정과 같아야 한다
        ("부부가 모두 받으면 각각 감액됩니다. [L-0009]", "통과"),
        ("부부가 모두 받으면 각각 감액됩니다.", "확인필요"),                          # 근거 번호 없음
        ("단독가구 선정기준액은 2,470,000원입니다. [G-0002]", "통과"),
        ("단독가구 선정기준액은 2,570,000원입니다. [G-0002]", "확인필요"),            # 근거에 없는 숫자
        ("제9조에 따라 감액됩니다. [L-0009]", "확인필요"),                            # 근거에 없는 조문
        ("감액됩니다. [L-9999]", "확인필요"),                                         # 없는 근거 번호
        ("주소지 읍면동에서 신청합니다. [Q-1]", "통과"),
        ("구체적인 금액은 담당자 확인 필요입니다.", "근거부족"),
    ]
    for d, want in same_cases:
        a, b = old.check(d, items)["자동점검"], check(d, items)["자동점검"]
        assert a == want and b == want, (d, want, a, b)
    # A) 도입 문장: 옛 규칙은 '근거 없는 문장'으로 걸고, 새 규칙은 사실 문장에서 뺀다
    d = "문의하신 내용은 다음과 같습니다. 부부가 모두 받으면 각각 감액됩니다. [L-0009]"
    assert old.check(d, items)["자동점검"] == "확인필요" and check(d, items)["자동점검"] == "통과"
    # A) 숫자가 들어간 짧은 문장은 도입 문장으로 보지 않는다
    assert check("2026년 기준은 다음과 같습니다.", items)["자동점검"] == "확인필요"
    # B) 연도 불일치
    r = check("2025년 단독가구 선정기준액은 2,470,000원입니다. [G-0002]", items)
    assert r["자동점검"] == "확인필요" and r["연도경고"], r
    assert check("2026년 단독가구 선정기준액은 2,470,000원입니다. [G-0002]", items)["자동점검"] == "통과"
    # B) 비슷한 질의(Q-)만 인용한 문장은 연도 대조를 하지 않는다
    assert check("2025년에도 주소지 읍면동에서 신청합니다. [Q-1]", items)["연도경고"] == []
    # C) 범위 밖
    assert check(HOLD_TEXT, items)["자동점검"] == "범위밖"
    # D) 지침 번호(J-): 인용으로 인정되고, 번호만 따로 떨어져도 앞 문장에 붙는다
    items2 = dict(items, **{"J-0001": "2026년 기초연금 사업안내 PDF 31쪽 매월 25일 기초연금 정기지급"})
    assert check("기초연금은 매월 25일에 지급합니다. [J-0001]", items2)["자동점검"] == "통과"
    assert check("기초연금은 매월 25일에 지급합니다.\n[J-0001]", items2)["자동점검"] == "통과"
    assert check("기초연금은 매월 25일에 지급합니다. [J-0009]", items2)["없는근거번호"] == ["J-0009"]
    print(f"시험 통과: 기존 판정 {len(same_cases)}건 동일, 새 규칙 9건 동작")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:2] == ["test"]:
        selftest()
    else:
        print(__doc__)
