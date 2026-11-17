"""초안의 근거 번호 대조 (형식 점검)

점검하는 것 (사람 검토를 대신하지 않는다):
  1) 초안이 단 근거 번호([L-0009], [G-0002], [Q-6889973])가 이번에 찾은 근거 안에 실제로 있는가
  2) 사실을 말하는 문장마다 근거 번호가 달려 있는가 (없는 문장 = 근거 없는 진술 후보)
  3) 초안에 나온 금액·비율·기간 숫자가 인용한 근거 글에 그대로 있는가
  4) 초안에 나온 '제N조'가 인용한 근거에 있는가
점검하지 못하는 것: 문장의 뜻이 근거와 맞는지 (사람이 읽어야 한다)
"""
import re

ID_RE = re.compile(r"\[((?:[LGQ]-\d+)(?:\s*,\s*[LGQ]-\d+)*)\]")
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:만\s*\d*\s*천?\s*\d*\s*백?\s*)?(?:원|%|퍼센트|세|년|개월|일|명|배)")
ART_RE = re.compile(r"제\d+조(?:의\d+)?")
UNSURE_WORDS = ("담당자 확인 필요", "확인이 필요", "확인하시기 바랍니다", "문의하시기 바랍니다", "문의하여 주시기 바랍니다",
               "확인해 주시기 바랍니다", "문의해 주시기 바랍니다", "확인해 주세요", "문의해 주세요")


def squash(s):
    return re.sub(r"[\s,]", "", s or "")


def split_sentences(text):
    out = []
    for line in (text or "").replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        # 근거 번호 대괄호 바로 뒤, 또는 마침표 뒤에서 자른다
        parts = re.split(r"(?<=\])\s+|(?<=[^\d\s][.!?])\s+", line)  # 숫자 뒤 마침표(2021. 10. 1.)에서는 자르지 않음
        for p in parts:
            p = p.strip()
            if not p:
                continue
            # 근거 번호만 따로 떨어진 조각은 앞 문장에 붙인다 ("...합니다. [L-0009]")
            if out and ID_RE.fullmatch(p):
                out[-1] = out[-1] + " " + p
            else:
                out.append(p)
    return out


def check(draft, items):
    """draft: 초안 글, items: {근거번호: 근거 글(법령명 조문 본문 ...)}  -> 결과 딕셔너리"""
    sents = split_sentences(draft)
    used_ids, bad_ids = [], []
    uncited, num_missing, art_missing = [], [], []
    n_fact = 0
    n_unsure = 0
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
            continue  # 확인·문의 안내 문장과 아주 짧은 조각은 사실 문장으로 세지 않음
        n_fact += 1
        if not ids:
            uncited.append(body[:60])
        raw_cited = " ".join(items.get(i, "") for i in ids) if ids else ""
        cited_text = squash(raw_cited)
        # 쉼표만 지우고 띄어쓰기·줄바꿈은 남긴 글: 표에서 옆 칸 숫자와 붙어 버리는 것을 막는다
        cited_nc = re.sub(r"(?<=\d),(?=\d)", "", raw_cited)
        for n in NUM_RE.findall(body):
            sq = squash(n)
            core = re.match(r"\d+(?:\.\d+)?", sq)
            # 단위까지 그대로 있거나, 숫자 부분이 근거에 그대로 있으면(표에는 단위가 없는 경우) 통과
            if sq in cited_text:
                continue
            if core and sq.endswith(("원", "%", "퍼센트")) and re.search(r"(?<![\d.])" + re.escape(core.group(0)) + r"(?![\d])", cited_nc):
                continue
            num_missing.append(sq)
        for a in ART_RE.findall(body):
            if a not in cited_text:
                art_missing.append(a)
    ok = not (bad_ids or uncited or num_missing or art_missing)
    status = "통과" if ok else "확인필요"
    if ok and n_fact == 0:
        status = "근거부족"   # 사실 문장이 하나도 없음(전부 '담당자 확인 필요'): 형식은 맞지만 쓸모 있는 초안은 아님
    return {
        "문장수": n_fact, "근거없는문장수": len(uncited),
        "근거없는문장비율": round(len(uncited) / n_fact, 3) if n_fact else None,
        "없는근거번호": bad_ids, "근거에없는숫자": sorted(set(num_missing)),
        "근거에없는조문": sorted(set(art_missing)),
        "사용한근거번호": sorted(set(used_ids)),
        "근거없는문장예": uncited[:3],
        "확인필요문장수": n_unsure,
        "자동점검": status,
    }
