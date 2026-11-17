"""답변 초안 생성: 찾은 근거만으로 초안을 쓰고, 근거 번호를 자동 대조한다.

준비
  pip install openai
  환경변수 OPENAI_API_KEY (키), OPENAI_MODEL (사용할 모델 이름)  <- 직접 정하고 팀에 알린다.
    PowerShell: $env:OPENAI_API_KEY="키" ; $env:OPENAI_MODEL="모델이름"
  ※ 외부 AI에는 개인정보를 가린 글만 보낸다. 질문을 직접 넣을 때도 이름·연락처를 빼고 넣는다.

사용
  python generate.py ask "질문"                  # 질문 1건 -> 초안 (파일 저장 없음, 화면 출력)
  python generate.py dry "질문"                  # AI 호출 없이 AI에게 보낼 글만 출력 (비용 0)
  python generate.py batch --groups eval_groups.txt   # 묶음 목록의 대표 질문마다 초안 -> drafts/drafts.csv
  python generate.py batch --first 5            # (시험) 핵심 질의 앞 5건

기록: drafts/drafts.csv 에 모델 이름, 지시문 버전, 날짜, 찾은 근거 번호가 같이 남는다.
"""
import argparse
import csv
import datetime
import json
import os
import sys
from pathlib import Path

from check_citations import check
from retrieve import Retriever, load_eval_groups, read_csv, QNA

INSTRUCTION_VERSION = "v5.2-2026-11-13"
BASE = Path(__file__).resolve().parent
OUT = BASE / "drafts"
OUT.mkdir(exist_ok=True)

SYSTEM = """당신은 복지 민원 답변 초안을 쓰는 보조자입니다. 최종 결정은 담당 공무원이 합니다.
규칙을 반드시 지키세요.
1. 아래 [참고 자료]에 있는 내용만 사용합니다. 참고 자료에 없는 내용은 쓰지 않습니다. 당신이 아는 지식으로 보충하지 않습니다.
2. 사실을 말하는 문장 하나하나의 끝에 근거 번호를 붙입니다. 여러 문장을 하나의 번호로 묶지 않습니다. 예: ...감액됩니다. [L-0009] 대괄호 안에는 번호만 씁니다([L-0009] 또는 [L-0009, G-0002]). 날짜나 설명을 대괄호 안에 넣지 않습니다. 번호는 참고 자료에 적힌 번호만 씁니다 (L-숫자 법령, G-숫자 고시, Q-숫자 비슷한 질의응답). 첫 문장이나 서문 문장도 사실을 말하면 그 근거 번호를 붙이고, 붙일 근거가 없는 서문은 쓰지 않습니다. "담당자 확인 필요" 문장과 문의·확인 안내 문장에는 근거 번호를 붙이지 않습니다.
3. 금액, 비율, 기간, 날짜는 참고 자료에 적힌 표현 그대로 옮깁니다. 계산하거나 바꿔 쓰거나 반올림하지 않습니다.
4. 법령(L-)·고시(G-)에서 가져온 금액·기준은 해당 근거의 시행일자를 함께 적습니다. 날짜는 "2026년 7월 30일 시행"처럼 읽기 쉽게 씁니다(예: 20260730처럼 숫자만 붙여 쓰지 않습니다). 비슷한 질의응답(Q-)에서 가져온 내용에는 시행일자를 묻거나 "시행일은 담당자 확인 필요"라고 쓰지 않습니다.
5. 참고 자료에 "[표(이미지)" 표시가 있거나 질문의 핵심에 답할 근거가 아예 없을 때만, 해당 부분을 문장 안에 "담당자 확인 필요"라고 씁니다. 이 표현은 답변 전체에서 한 번만 쓰고, 근거가 있는 내용에는 붙이지 않습니다. 금액·기준 표에서 일부 구간(예: 가구원 수, 급여 종류)만 참고 자료에 있고 나머지가 없으면, 있는 구간만 쓰고 없는 구간은 말없이 빼지 말고 한 문장으로 "○○는 담당자 확인 필요"라고 알립니다(이것이 그 한 번입니다). "[확인필요]" 같은 별도 기호는 본문에 쓰지 않고, 확인이 필요한 항목은 JSON의 확인필요 목록에만 넣습니다.
6. 질문한 사람이 개인적으로 대상인지, 얼마를 받는지는 단정하지 않습니다. 일반적인 기준을 설명하고 확인 방법(관할 기관 문의 등)을 안내합니다.
7. 질문이 묻는 것(방법, 금액, 가능 여부, 신청 장소 등)에 직접 답하는 문장만 씁니다. 용어의 정의나 제도의 일반 설명(예: 소득인정액의 정의)은 질문이 그것을 물었을 때만 쓰고, 근거가 있다는 이유만으로 문장을 덧붙이지 않습니다. 참고 자료에 답이 없는 부분은 문장을 채우지 말고 5번대로 처리합니다. 핵심 답을 먼저 쓰고, 보충 설명은 꼭 필요한 한두 문장 이내로 합니다. 다만 질문이 기준·금액·절차 전반을 포괄적으로 묻는 경우에는, 참고 자료에 있는 해당 기준의 금액과 조건을 빠짐없이 씁니다. 비슷한 질의응답(Q-)을 쓸 때 "○○시 질의응답에서는", "질의응답에는", "참고 자료에는" 같은 출처 설명 없이 내용만 씁니다. 특정 지역에만 해당하는 정보(전화번호, 접수처 등)는 쓰지 않습니다.
8. 인사말과 맺음말 없이 본문만 씁니다. 공손한 존댓말로, 간결하게 씁니다.
출력은 JSON 하나만 냅니다.
{"요지": "민원 요지 한 문장", "유형": "문의 종류 한 단어~짧은 구", "답변초안": "근거 번호가 달린 답변 본문", "확인필요": ["확인이 필요한 항목", ...]}"""


def fmt_date(d):
    """20260730 -> 2026년 7월 30일 (8자리 숫자가 아니면 그대로)"""
    d = (d or "").strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}년 {int(d[4:6])}월 {int(d[6:])}일"
    return d


def build_prompt(question, qa, ev):
    parts = ["[민원 질문]\n" + question.strip(), "\n[참고 자료 1: 비슷한 질의응답]"]
    for r, _ in qa:
        ans = r["답변"]
        if len(ans) > 900:
            ans = ans[:900] + " ...(이하 생략)"
        parts.append(f'Q-{r["faqNo"]} ({r["기관"]}, 등록 {r["등록일"]})\n질문: {r["질문"][:400]}\n답변: {ans}')
    parts.append("\n[참고 자료 2: 근거 법령·고시]")
    items = {}
    for e, _ in ev:
        text = e["text"] if len(e["text"]) <= 2500 else e["text"][:2500] + " ...(이하 생략)"
        parts.append(f'{e["id"]} {e["name"]} {e["pos"]} (시행일자 {fmt_date(e["date"])})\n{text}')
        # 시행일자도 근거 글에 포함해서, 초안이 쓴 시행일자가 실제 근거의 것인지 숫자 점검이 확인할 수 있게 한다
        items[e["id"]] = f'{e["name"]} {e["pos"]} 시행일자 {e["date"]} {fmt_date(e["date"])} {e["text"]}'
    for r, _ in qa:
        items[f'Q-{r["faqNo"]}'] = r["질문"] + " " + r["답변"]
    return "\n\n".join(parts), items


def call_model(user_prompt):
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("OPENAI_MODEL", "").strip()
    if not key or not model:
        sys.exit("환경변수 OPENAI_API_KEY 와 OPENAI_MODEL 이 필요합니다.")
    import time
    import openai
    client = openai.OpenAI(api_key=key, max_retries=4, timeout=float(os.environ.get("REQ_TIMEOUT", "120")))   # 120초 넘게 응답이 없으면 끊고 다시 요청   # 429 등은 SDK 가 먼저 알아서 재시도
    temp = os.environ.get("TEMPERATURE", "").strip()      # 예: $env:TEMPERATURE="0" (같은 입력이면 거의 같은 답, 비교·평가에 유리)
    for attempt in range(8):
        try:
            kw = {"temperature": float(temp)} if temp else {}
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_prompt}],
                    response_format={"type": "json_object"},
                    **kw,
                )
            except openai.BadRequestError as e:
                if kw and "temperature" in str(e).lower():
                    print("  이 모델은 temperature 설정을 받지 않아 기본값으로 진행합니다.")
                    os.environ["TEMPERATURE"] = ""
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_prompt}],
                        response_format={"type": "json_object"},
                    )
                else:
                    raise
            return resp.choices[0].message.content, model
        except (openai.APITimeoutError, openai.APIConnectionError) as e:
            print(f"  응답이 없거나 연결이 끊겨 다시 요청합니다 ({attempt + 1}/8): {type(e).__name__}")
            time.sleep(5)
        except openai.RateLimitError as e:
            msg = str(e)
            if "insufficient_quota" in msg or "billing" in msg.lower() and "quota" in msg.lower():
                sys.exit("잔액/한도 문제로 보입니다. Billing 화면을 확인하세요: " + msg[:200])
            wait = 15 * (attempt + 1)
            if attempt == 0:
                print("  [오류 원문]", msg[:600])
                if "per day" in msg or "TPD" in msg or "RPD" in msg or "monthly" in msg.lower() or "budget" in msg.lower():
                    sys.exit("일일/월 한도에 걸린 것으로 보입니다. 기다려도 풀리지 않습니다. Limits/Billing 화면을 확인하세요.")
            print(f"  분당 사용량 한도에 걸려 {wait}초 기다립니다 ({attempt + 1}/8)...")
            time.sleep(wait)
    sys.exit("한도 때문에 계속 실패했습니다. 잠시 뒤 같은 명령을 다시 실행하세요 (완료된 건은 건너뜁니다).")


def make_draft(ret, question, skip_groups=None, caller=call_model):
    k_qa = int(os.environ.get("QA_K", "3"))
    k_ev = int(os.environ.get("EVIDENCE_K", "3"))
    qa, ev = ret.search(question, k_qa=k_qa, k_ev=k_ev, skip_groups=skip_groups)
    prompt, items = build_prompt(question, qa, ev)
    raw, model = caller(prompt)
    try:
        d = json.loads(raw)
    except ValueError:
        d = {"요지": "", "유형": "", "답변초안": raw, "확인필요": ["JSON 형식 아님: 직접 확인"]}
    draft = d.get("답변초안", "")
    res = check(draft, items)
    return {
        "질문": question, "요지": d.get("요지", ""), "유형": d.get("유형", ""),
        "답변초안": draft, "확인필요": "; ".join(d.get("확인필요", []) or []),
        "찾은_질의": ", ".join(f'Q-{r["faqNo"]}' for r, _ in qa),
        "찾은_근거": ", ".join(e["id"] for e, _ in ev),
        "모델": model, "지시문버전": INSTRUCTION_VERSION, "검색설정(질의/근거 개수)": f"{k_qa}/{k_ev}", "온도설정": os.environ.get("TEMPERATURE", "") or "기본값", "생성일": datetime.date.today().isoformat(),
        **{"점검_" + k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in res.items()},
    }


def cmd_ask(q):
    ret = Retriever(exclude_groups=load_eval_groups())
    row = make_draft(ret, q)
    print("요지:", row["요지"], "| 유형:", row["유형"])
    print("\n" + row["답변초안"])
    print("\n확인필요:", row["확인필요"] or "없음")
    print("찾은 근거:", row["찾은_근거"], "| 찾은 질의:", row["찾은_질의"])
    print("자동점검:", row["점검_자동점검"], "| 근거 없는 문장:", row["점검_근거없는문장수"], "/", row["점검_문장수"],
          "| 없는 근거번호:", row["점검_없는근거번호"], "| 근거에 없는 숫자:", row["점검_근거에없는숫자"], "| 근거에 없는 조문:", row["점검_근거에없는조문"])


def cmd_dry(q):
    ret = Retriever(exclude_groups=load_eval_groups())
    qa, ev = ret.search(q)
    prompt, _ = build_prompt(q, qa, ev)
    print("=== 시스템 지시문 ===\n" + SYSTEM + "\n\n=== 사용자 입력 ===\n" + prompt)


def cmd_batch(a):
    rows = read_csv(QNA)
    groups = None
    if a.groups:
        groups = {ln.strip() for ln in Path(a.groups).read_text(encoding="utf-8").splitlines() if ln.strip()}
        targets = []
        seen = set()
        for r in rows:  # 묶음마다 첫 행을 대표 질문으로
            if r["group_id"] in groups and r["group_id"] not in seen:
                seen.add(r["group_id"])
                targets.append(r)
    else:
        # 시험용(--first): 평가용 묶음(eval_groups.txt)은 건너뛴다. 평가용 문항으로 지시문을 고치면 평가 점수가 부풀려진다.
        ev_groups = load_eval_groups()
        targets = [r for r in rows if r["핵심관련"] == "Y" and r["group_id"] not in ev_groups][: a.first or 5]
        if ev_groups:
            print(f"평가용 묶음 {len(ev_groups)}개는 시험 대상에서 제외합니다.")
    ret = Retriever(exclude_groups=load_eval_groups() | (groups or set()))
    out = OUT / "drafts.csv"
    done = set()
    if out.exists():
        done = {r["group_id"] for r in read_csv(out)}
    new = not out.exists()
    f = open(out, "a", newline="", encoding="utf-8-sig")
    w = None
    for i, r in enumerate(targets, 1):
        if r["group_id"] in done:
            continue
        q = (r["제목"] + "\n" + r["질문"]) if r["제목"].strip() != r["질문"].strip() else r["질문"]
        print(f"  {i}/{len(targets)} {r['faqNo']} 요청 중...", flush=True)
        row = make_draft(ret, q, skip_groups={r["group_id"]})
        row = {"group_id": r["group_id"], "faqNo": r["faqNo"], "핵심관련": r["핵심관련"], **row,
               "공개답변(비교용)": r["답변"], "정답조문(비교용)": r["근거조문"]}
        if w is None:
            w = csv.DictWriter(f, fieldnames=list(row.keys()))
            if new:
                w.writeheader()
        w.writerow(row)
        f.flush()
        print(f"{i}/{len(targets)} {r['faqNo']} 점검={row['점검_자동점검']}")
    f.close()
    print("저장:", out)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ask").add_argument("q")
    sub.add_parser("dry").add_argument("q")
    b = sub.add_parser("batch")
    b.add_argument("--groups")
    b.add_argument("--first", type=int)
    a = p.parse_args()
    if a.cmd == "ask":
        cmd_ask(a.q)
    elif a.cmd == "dry":
        cmd_dry(a.q)
    else:
        cmd_batch(a)


if __name__ == "__main__":
    main()
