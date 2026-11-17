"""의미 검색용 벡터를 한 번 만들어 emb/ 폴더에 저장한다 (OpenAI 임베딩, 수 센트).

사용:  python embed_build.py          (모델은 EMB_MODEL 환경변수, 기본 text-embedding-3-small)
만드는 것: emb/qa.npy (질의응답 질문), emb/ev.npy (법령+고시 문단), emb/meta.json
※ 질의응답·법령·고시 파일이 바뀌면 다시 실행한다 (바뀐 채로 쓰면 검색이 멈춘다).
이후  python retrieve.py eval  로 섞는 비율(HYB)별 정확도를 비교한다.
"""
import json
import numpy as np

import retrieve as R


def main():
    R.EMB_DIR.mkdir(exist_ok=True)
    ret = R.Retriever()   # 전체 데이터 (평가 묶음 제외 없음)
    qa = R.read_csv(R.QNA)
    qa_docs = [R.norm(r["제목"] + " " + r["질문"]) for r in qa]
    ev_docs = [R.norm(f'{e["name"]} {e["pos"]} {e["title"]} {e["text"]}') for e in ret.ev]
    print(f"질의응답 {len(qa_docs)}건, 근거 문단 {len(ev_docs)}개 -> {R.EMB_MODEL}")
    qv = R.embed_texts(qa_docs)
    ev = R.embed_texts(ev_docs)
    np.save(R.EMB_DIR / "qa.npy", qv)
    np.save(R.EMB_DIR / "ev.npy", ev)
    meta = {"model": R.EMB_MODEL, "qa_ids": [r["faqNo"] for r in qa], "ev_ids": [e["id"] for e in ret.ev]}
    (R.EMB_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print("저장:", R.EMB_DIR)


if __name__ == "__main__":
    main()
