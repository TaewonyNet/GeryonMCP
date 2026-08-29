#!/usr/bin/env python3
"""LLM 기반 골든셋 생성 — 문서에서 **자연어 질문**을 역생성한다(룰기반 보완).

    python scripts/golden_llm.py <db> [--out golden_llm.draft.yml] [-n 40] [--verify]

## 왜 필요한가
`golden_bootstrap.py`(룰기반)는 본문 희소어를 조합해 질의를 만든다. 오프라인·무LLM·결정적이라
좋지만, 결과가 `"진입수 구매고객수 bp"` 같은 **키워드 나열**이라 실사용 질의와 성격이 다르다.
이 편향 때문에 rerank 계열 설정 비교에서 결론이 뒤집힐 수 있다(BENCHMARK_METHODOLOGY 참고).
LLM 은 같은 문서에서 **사람이 실제로 던질 법한 문장형 질문**을 만들 수 있다.

## 대가(반드시 인지할 것)
- 결정적이지 않다(temperature·모델 버전에 따라 결과가 흔들림) → `--seed`·모델 digest 를 기록한다.
- LLM 이 **문서에 없는 내용을 지어낼 수 있다**(할루시네이션) → 아래 자동 검증을 반드시 통과시킨다.
- 오프라인 원칙은 지킨다: 외부 API 가 아니라 **로컬 Ollama** 만 호출한다.

## 자동 검증(생성 즉시 적용, 통과한 케이스만 초안에 남김)
1. **앵커 검증** — 질문의 핵심 명사가 원문에 실제로 등장하는가(할루시네이션 차단).
2. **자기참조 금지** — 질문이 정답 제목을 그대로 베끼면 검색이 아니라 문자열 매칭이 되므로 탈락.
3. **변별력 검증** — 그 질문으로 현재 검색기를 돌려 정답 문서가 후보에 잡히는지 확인.
   여기서 MISS 라고 탈락시키면 "지금 검색기가 맞히는 문제만" 남아 골든셋이 무의미해지므로
   **탈락시키지 않고 `_self_check` 로 표기만** 한다(룰기반 도구와 동일한 방침).
4. **중복 제거** — 같은 문서에서 나온 유사 질문은 하나만 남긴다.
"""
import argparse
import json
import os
import random
import re
import sqlite3
import sys
from pathlib import Path

REPO_SRC = str(Path(__file__).resolve().parent.parent / "src")
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

_SYSTEM = """너는 사내 문서 검색 테스트셋을 만드는 도구다.
주어진 문서를 찾으려는 사람이 검색창에 입력할 법한 자연스러운 한국어 질문을 만든다.

규칙:
- 문서 제목을 그대로 베끼지 마라. 제목을 몰라도 떠올릴 수 있는 질문이어야 한다.
- 문서 본문에 실제로 있는 내용만 근거로 삼아라. 없는 사실을 지어내지 마라.
- 한 문장, 30자 이내의 구체적인 질문으로 써라."""

_USER = """문서 제목: {title}
문서 본문:
{body}

위 문서를 찾기 위한 질문 {n}개를 만들어라."""

# Ollama structured output 스키마 — 형식 파싱 실패를 원천 차단
_SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["questions"],
}


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[가-힣A-Za-z0-9]{2,}", s.lower()))


def fetch_docs(db: str, n: int, min_body: int, seed: int) -> list[dict]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT doc_id, title, body_markdown FROM documents "
        "WHERE length(body_markdown) >= ? AND title IS NOT NULL AND title != ''",
        (min_body,),
    ).fetchall()
    conn.close()
    rng = random.Random(seed)
    rng.shuffle(rows)
    return [{"doc_id": r[0], "title": r[1], "body": r[2]} for r in rows[:n]]


def ask_llm(refiner, title: str, body: str, per_doc: int, body_chars: int) -> list[str]:
    """DocumentRefiner._call(system, user, schema) 재사용 — 스키마 강제라 파싱 실패가 없다."""
    user = _USER.format(n=per_doc, title=title, body=(body or "")[:body_chars])
    try:
        data, _elapsed = refiner._call(_SYSTEM, user, _SCHEMA, retries=2)
    except Exception as e:  # 모델 미기동·타임아웃 등
        print(f"    [LLM 실패] {e}", file=sys.stderr)
        return []
    qs = (data or {}).get("questions") or []
    return [c for c in (clean_question(str(q)) for q in qs) if c]


def clean_question(q: str) -> str:
    """LLM 이 본문의 마크다운 강조를 그대로 물고 오는 경우가 있어 제거(실사용 질의엔 없는 문자)."""
    q = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", q)   # **강조** → 강조
    q = re.sub(r"[`_~]{1,3}", "", q)
    return re.sub(r"\s+", " ", q).strip()


def validate(question: str, title: str, body: str, min_anchor: int = 1) -> tuple[bool, str]:
    """앵커·자기참조·길이 검증. (통과여부, 사유)"""
    q = question.strip()
    if not (4 <= len(q) <= 60):
        return False, "길이 이탈"
    qt, tt, bt = _tokens(q), _tokens(title), _tokens(body)
    if not qt:
        return False, "토큰 없음"
    # 자기참조: 질문 토큰이 제목에 거의 다 포함되면 문자열 매칭 문제로 전락
    if tt and len(qt & tt) / len(qt) >= 0.8:
        return False, "제목 복사"
    # 앵커: 질문의 핵심어가 본문에 실제로 존재해야 함(할루시네이션 차단)
    anchors = qt & bt
    if len(anchors) < min_anchor:
        return False, "본문 근거 없음(할루시네이션 의심)"
    return True, "ok"


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM 기반 골든셋 생성(자연어 질의)")
    ap.add_argument("db")
    ap.add_argument("--out", default="golden_llm.draft.yml")
    ap.add_argument("-n", "--num-docs", type=int, default=20, help="대상 문서 수(기본 20)")
    ap.add_argument("--per-doc", type=int, default=2, help="문서당 질문 수(기본 2)")
    ap.add_argument("--min-body", type=int, default=300)
    ap.add_argument("--body-chars", type=int, default=1500, help="LLM 에 넣을 본문 길이")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--verify", action="store_true",
                    help="현 검색기로 정답이 top_k 에 드는지 확인해 _self_check 표기(탈락 아님)")
    a = ap.parse_args()

    from geryon.analyze.llm_extract import LLMConfig, DocumentRefiner

    cfg = LLMConfig.from_env()
    refiner = DocumentRefiner(cfg)
    print(f"LLM: {cfg.model} @ {cfg.base_url}")

    docs = fetch_docs(a.db, a.num_docs, a.min_body, a.seed)
    print(f"대상 문서 {len(docs)}건 · 문서당 {a.per_doc}질문 생성\n")

    cases: list[dict] = []
    rejected: dict[str, int] = {}
    for i, d in enumerate(docs, 1):
        qs = ask_llm(refiner, d["title"], d["body"], a.per_doc, a.body_chars)
        kept_for_doc: list[set] = []
        for q in qs:
            ok, why = validate(q, d["title"], d["body"])
            if not ok:
                rejected[why] = rejected.get(why, 0) + 1
                continue
            qt = _tokens(q)
            if any(len(qt & prev) / max(1, len(qt | prev)) > 0.6 for prev in kept_for_doc):
                rejected["중복"] = rejected.get("중복", 0) + 1
                continue
            kept_for_doc.append(qt)
            cases.append({
                "query": q, "expected_title": d["title"], "top_k": a.top_k,
                "notes": "auto-llm (검수 필요)",
                "_source_doc_id": d["doc_id"], "_generator": f"llm:{cfg.model}",
            })
        print(f"  [{i}/{len(docs)}] {d['title'][:34]:34s} → 채택 {len(kept_for_doc)}/{len(qs)}")

    if a.verify and cases:
        print("\n현 검색기로 self-check 중(표기만, 탈락 아님)...")
        from geryon.search.factory import build_searcher
        searcher = build_searcher(db_paths=[a.db])
        miss = 0
        for c in cases:
            titles = [h.title for h in searcher.search(c["query"], k=c["top_k"])]
            rank = next((i + 1 for i, t in enumerate(titles) if c["expected_title"] in (t or "")), None)
            c["_self_check"] = f"rank {rank}" if rank else "MISS"
            miss += rank is None
        print(f"  MISS {miss}/{len(cases)} (검수 참고용)")

    import yaml
    Path(a.out).write_text(yaml.safe_dump(
        {"_meta": {"desc": "LLM 생성 골든 초안 — 검수 후 확정",
                   "generator": f"golden_llm.py / {cfg.model}",
                   "seed": a.seed,
                   "validation": "앵커(본문근거)·자기참조금지·중복제거 통과분만 수록"},
         "cases": cases}, allow_unicode=True, sort_keys=False), encoding="utf-8")

    print(f"\n골든 초안 {len(cases)}건 → {a.out}")
    if rejected:
        print(f"  자동 탈락: {rejected}")
    print("  ⚠ LLM 생성물은 비결정적이다. 커밋 전 사람 검수 필수(질문이 자연스러운지·정답이 유일한지).")
    print(f"  평가: python scripts/golden_eval.py {a.out} {a.db}")


if __name__ == "__main__":
    main()
