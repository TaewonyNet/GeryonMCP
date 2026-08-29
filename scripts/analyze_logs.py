#!/usr/bin/env python3
"""검색 행동 로그 분석 — 실사용 신호로 랭킹 품질을 재고, 골든셋을 재구성한다.

    python scripts/analyze_logs.py <db> [--export-golden out.yml] [--min-count 2]

## 왜 필요한가
합성 골든셋은 문서에서 역생성한 질의라 실제 사용자 의도와 분포가 다르다(covariate shift).
`search_log`/`selection_log`(스키마 v8)에 실제 질의와 선택이 쌓이면 다음이 가능해진다.

  * **MRR / 선택 랭크 분포** — 사용자가 1위를 고르는가, 5위까지 내려가는가.
    합성 골든의 hit-rate 와 달리 **실제 만족도에 가까운 지표**다.
  * **무선택 질의(abandonment)** — 검색했는데 아무것도 안 열어본 질의.
    랭킹 실패 후보이자 골든셋에 넣어야 할 "어려운 질의".
  * **실사용 골든셋 export** — (질의, 실제 선택 문서) 쌍은 사람이 만든 정답과 같다.
    `--export-golden` 으로 golden_eval 이 바로 먹는 형식으로 내보낸다.

로그가 비어 있으면 아무 것도 못 한다 — 먼저 MCP 로 실제 검색을 써야 쌓인다.
"""
import argparse
import json
import sqlite3
import statistics
from collections import Counter
from pathlib import Path


def _q(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[tuple]:
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []


def main() -> None:
    ap = argparse.ArgumentParser(description="검색 행동 로그 분석")
    ap.add_argument("db")
    ap.add_argument("--export-golden", default=None,
                    help="실사용 (질의→선택) 쌍을 골든셋 YAML 로 내보낼 경로")
    ap.add_argument("--min-count", type=int, default=1,
                    help="export 시 같은 (질의,문서) 쌍이 최소 몇 번 나와야 채택할지(기본 1)")
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)

    searches = _q(conn, "SELECT id, query, n_results, latency_ms FROM search_log")
    sels = _q(conn, "SELECT search_id, doc_id, rank FROM selection_log")
    if not searches:
        print("검색 로그가 비어 있습니다. MCP 로 실제 검색을 사용한 뒤 다시 실행하세요.")
        print("  (로깅 비활성 상태라면 GERYON_SEARCH_LOG=1 확인)")
        return

    print(f"=== 규모 ===")
    print(f"  검색 {len(searches):,}건 · 선택 {len(sels):,}건")

    lat = [s[3] for s in searches if s[3] is not None]
    if lat:
        lat.sort()
        print(f"  지연 p50 {statistics.median(lat):.0f}ms · p95 {lat[int(len(lat)*0.95)-1]:.0f}ms")

    # 선택 랭크 분포 · MRR — 랭킹 품질의 실사용 지표
    ranks = [r for (_sid, _d, r) in sels if r]
    if ranks:
        mrr = sum(1.0 / r for r in ranks) / len(ranks)
        dist = Counter(ranks)
        print(f"\n=== 선택 랭크(실사용 랭킹 품질) ===")
        print(f"  MRR = {mrr:.3f}  (1.0 = 항상 1위를 선택)")
        for r in sorted(dist)[:10]:
            bar = "█" * min(40, dist[r])
            print(f"    {r:>2}위 {dist[r]:>4} {bar}")
        top1 = dist.get(1, 0) / len(ranks) * 100
        print(f"  1위 선택 비율: {top1:.1f}%")
    else:
        print("\n  (선택 기록이 없어 랭킹 품질 지표를 낼 수 없습니다)")

    # 무선택 질의 — 랭킹 실패 후보
    picked = {s[0] for s in sels if s[0] is not None}
    abandoned = [s for s in searches if s[0] not in picked]
    print(f"\n=== 무선택 질의(abandonment) ===")
    print(f"  {len(abandoned)}/{len(searches)}건 ({len(abandoned)/len(searches)*100:.1f}%)"
          f" — 결과를 하나도 열지 않은 검색")
    zero = [s for s in abandoned if (s[2] or 0) == 0]
    if zero:
        print(f"  그중 결과 0건: {len(zero)}건 — 재현율 문제 후보")
    for _id, q, n, _l in abandoned[:8]:
        print(f"    {q[:50]!r} (결과 {n}건)")

    # 실사용 골든셋 export
    if a.export_golden:
        pairs = Counter()
        qmap = {s[0]: s[1] for s in searches}
        for sid, doc_id, _r in sels:
            if sid in qmap:
                pairs[(qmap[sid], doc_id)] += 1
        titles = {}
        for (_q_, doc_id), _c in pairs.items():
            row = _q(conn, "SELECT title FROM documents WHERE doc_id=?", (doc_id,))
            if row:
                titles[doc_id] = row[0][0]
        cases = [
            {"query": q, "expected_title": titles[d], "top_k": 5,
             "notes": f"from real usage (n={c})", "_source_doc_id": d}
            for (q, d), c in pairs.items() if c >= a.min_count and d in titles
        ]
        import yaml
        Path(a.export_golden).write_text(yaml.safe_dump(
            {"_meta": {"desc": "실사용 로그 기반 골든셋 — 합성이 아니라 실제 (질의→선택) 쌍",
                       "min_count": a.min_count, "n_searches": len(searches)},
             "cases": cases}, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"\n=== 골든셋 export ===")
        print(f"  {len(cases)}건 → {a.export_golden}")
        print("  ⚠ 선택 = 정답 가정에는 **위치 편향**이 있다(상위 노출된 것을 고르기 쉬움).")
        print("     현 랭킹이 만든 편향이 섞이므로, 합성 골든셋과 병행해 쓸 것.")


if __name__ == "__main__":
    main()
