#!/usr/bin/env python3
"""골든 테스트셋 부트스트랩(반자동) — DB 문서에서 본문중심 질의 초안을 역생성.

    python scripts/golden_bootstrap.py <db1[,db2,...]> [--out golden.draft.yml] [-n 40]

아이디어: 문서를 골라 → **제목에 없는 본문 단어** 중 코퍼스 전체에서 희소(변별력 높은)
한 것들로 질의를 만들고 → 정답(expected_title)=그 문서 제목 으로 둔다.
  - 본문중심: 제목 단어를 제외해 "제목엔 없고 본문에만 답" 인 질의를 만든다(passage recall 검증).
  - 거짓라벨 최소화: DF(문서빈도) 낮은 희소어 N개를 묶어 질의가 해당 문서에 고유해지도록.
  - 오프라인·무LLM: 규칙+통계(빈도)만.

반자동: 초안 생성 후 **사람이 검수**해 확정한다(질의가 자연스러운지·정답이 유일한지).
검수 편의 보조필드(`_source_doc_id`·`_terms`)는 golden_eval 이 무시하는 메타이므로 남겨둔다.
옵션 `--self-check <db>` 로 현재 검색기가 정답을 top_k 안에 넣는지 주석만 단다(필터 아님).
"""
import argparse
import collections
import random
import re
import sqlite3
from pathlib import Path

# 변별력 없는 흔한 한글 명사 — 질의어로 부적합(어느 문서에나 등장)
_STOP_KO = {
    "확인", "요청", "작성", "제목", "구분", "내용", "등록", "수정", "삭제", "목록", "정보",
    "관리", "설정", "사용", "처리", "이름", "담당", "상태", "추가", "개선", "완료", "진행",
    "생성", "변경", "대상", "기준", "결과", "오류", "문제", "방법", "관련", "필요", "경우",
    "위해", "통해", "또는", "그리고", "이후", "이전", "각각", "해당", "아래", "다음", "현재",
}
# 흔한 약어·일반 영단어 — 변별력 낮음
_STOP_EN = {
    "the", "and", "for", "with", "this", "that", "from", "http", "https", "www", "com",
    "api", "url", "html", "json", "true", "false", "null", "data", "type", "name", "list",
}
_TOKEN = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9_]{1,}")
# 한국어 조사·어미 — 어절 끝에서 떼어 명사형에 가깝게(긴 것부터 시도)
_JOSA = sorted([
    "으로서", "으로써", "에서는", "에서도", "으로", "에서", "에게", "한테", "부터", "까지",
    "들로", "들을", "들이", "들의", "들은", "라고", "이라", "처럼", "보다", "마다", "조차",
    "을", "를", "이", "가", "은", "는", "에", "의", "와", "과", "도", "만", "들", "로", "께",
], key=len, reverse=True)
# 동사·형용사 활용 어미로 끝나면 명사가 아님 → 질의어 제외
_VERB_END = ("하고", "하기", "하는", "해서", "하여", "합니다", "했다", "한다", "됩니다", "되어",
             "입니다", "이다", "있다", "없다", "였다", "보고", "받고", "되는", "하지", "되고")
_ID_LIKE = re.compile(r"(?=.*[A-Za-z])(?=.*\d)")   # 영문+숫자 혼재(해시·ID 의심)


def _strip_josa(t: str) -> str:
    for j in _JOSA:
        if t.endswith(j) and len(t) - len(j) >= 2:
            return t[: -len(j)]
    return t


def _tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text or ""):
        if t[0].isascii():
            if t.lower() in _STOP_EN:
                continue
            if len(t) >= 8 and _ID_LIKE.search(t):   # 해시·ID 류 제외
                continue
        else:
            if t.endswith(_VERB_END):                # 활용형(동사·형용사) 제외
                continue
            t = _strip_josa(t)
            if len(t) < 2 or t in _STOP_KO:
                continue
        out.append(t)
    return out


def scan_df(db_paths: list[str], body_chars: int) -> tuple[collections.Counter, list[tuple]]:
    """전체 1패스: 단어별 DF(문서빈도) + 문서 레코드(doc_id,title,body) 수집."""
    df: collections.Counter = collections.Counter()
    docs: list[tuple] = []
    for db in db_paths:
        conn = sqlite3.connect(db)
        for doc_id, title, body in conn.execute(
            "SELECT doc_id, title, substr(body_markdown,1,?) FROM documents", (body_chars,)
        ):
            docs.append((doc_id, title or "", body or ""))
            for term in set(_tokens(body or "")):   # set = 문서당 1회(=DF)
                df[term] += 1
        conn.close()
    return df, docs


def make_query(title: str, body: str, df: collections.Counter,
               n_terms: int, max_df: int) -> tuple[str, list[str]] | None:
    """제목에 없는 본문 단어 중 DF 낮은(희소) 것 n_terms 개로 질의 생성."""
    title_low = {t.lower() for t in _tokens(title)}
    tf = collections.Counter(_tokens(body))
    cands = [
        t for t in tf
        if t.lower() not in title_low      # 제목에 없는 = 본문중심
        and not t.isdigit()
        and df.get(t, 0) <= max_df         # 너무 흔한 단어 제외(변별력)
        and df.get(t, 0) >= 2              # 1회뿐(오타·우연)도 제외
    ]
    if len(cands) < n_terms:
        return None
    # DF 오름차순(희소 우선), 동률이면 본문 TF 높은 순
    cands.sort(key=lambda t: (df.get(t, 0), -tf[t]))
    picked = cands[:n_terms]
    return " ".join(picked), picked


def main() -> None:
    ap = argparse.ArgumentParser(description="본문중심 골든 초안 부트스트랩(반자동)")
    ap.add_argument("dbs", help="DB 경로(콤마로 여러 개)")
    ap.add_argument("--out", default="golden.draft.yml")
    ap.add_argument("-n", "--num", type=int, default=40, help="생성할 케이스 수(기본 40)")
    ap.add_argument("--terms", type=int, default=3, help="질의당 단어 수(기본 3)")
    ap.add_argument("--top-k", type=int, default=5, help="케이스 top_k(기본 5)")
    ap.add_argument("--min-body", type=int, default=200, help="최소 본문 길이(짧은 문서 제외)")
    ap.add_argument("--body-chars", type=int, default=600, help="본문 스캔 길이(기본 600)")
    ap.add_argument("--max-df-ratio", type=float, default=0.05,
                    help="단어 최대 DF 비율(전체 문서의 5%% 초과 등장어는 흔해서 제외)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--self-check", metavar="DB",
                    help="현 검색기가 정답을 top_k 에 넣는지 주석만(필터 아님). 무거움.")
    a = ap.parse_args()

    db_paths = [p.strip() for p in a.dbs.split(",") if p.strip()]
    df, docs = scan_df(db_paths, a.body_chars)
    max_df = max(int(len(docs) * a.max_df_ratio), 3)

    random.seed(a.seed)
    random.shuffle(docs)
    seen_titles: set[str] = set()
    cases: list[dict] = []
    for doc_id, title, body in docs:
        if len(cases) >= a.num:
            break
        if not title or len(body) < a.min_body or title in seen_titles:
            continue
        made = make_query(title, body, df, a.terms, max_df)
        if not made:
            continue
        query, terms = made
        seen_titles.add(title)
        cases.append({
            "query": query,
            "expected_title": title,
            "top_k": a.top_k,
            "notes": "auto-bodycentric (검수 필요)",
            "_source_doc_id": doc_id,
            "_terms": terms,
        })

    if a.self_check:
        import os
        os.environ.setdefault("GERYON_DB", a.self_check)
        from geryon.search.factory import build_searcher
        s = build_searcher(db_paths=[a.self_check])
        for c in cases:
            titles = [h.title or "" for h in s.search(c["query"], k=c["top_k"])]
            rank = next((i + 1 for i, t in enumerate(titles) if c["expected_title"] in t), None)
            c["_self_check"] = f"rank {rank}" if rank else "MISS"

    import yaml
    header = {"_meta": {
        "desc": "본문중심 골든 초안(golden_bootstrap, 반자동). 검수 후 확정.",
        "method": "제목에 없는 본문 희소어로 질의 역생성, 정답=출처 문서 제목",
    }, "cases": cases}
    Path(a.out).write_text(
        yaml.safe_dump(header, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"본문중심 골든 초안 {len(cases)}건 → {a.out}  (문서 {len(docs)} 스캔, max_df={max_df})")
    print("  검수: 질의가 자연스러운지·정답이 유일한지 확인 후 tests/golden/ 로 확정.")
    print(f"  평가: python scripts/golden_eval.py {a.out} \"{','.join(db_paths)}\"")
    if a.self_check:
        miss = sum(1 for c in cases if c.get("_self_check") == "MISS")
        print(f"  self-check: 현 검색기 MISS {miss}/{len(cases)} (검수 참고용, 필터 아님)")


if __name__ == "__main__":
    main()
