#!/usr/bin/env python3
"""범용 골드 평가 — cases.yml + DB → top_k 에 expected_title 부분포함이면 hit. 소스 무관.

    python scripts/golden_eval.py tests/golden/cases_bitbucket.yml <db_path> [--json-out out.json]

--json-out 지정 시 케이스별 결과(query/ok/rank/_terms 등 원본 메타 보존)를 JSON으로도 남긴다.
설정 A/B를 같은 케이스 순서로 두 번 돌려 이 JSON 두 개를 얻으면, ab_significance.py 로
McNemar 검정(짝지은 결과의 차이가 우연인지) 가능 — golden_bootstrap 이 만든 _terms(희소어)
메타도 그대로 실려있어 "키워드 편향" 계층분석에도 재사용된다.
"""
import json
import sys
from pathlib import Path


def run(cases_file: str, db_spec: str, json_out: str | None = None) -> tuple[int, int]:
    """db_spec: 단일 경로 또는 콤마 구분 다중(federation[25])."""
    import yaml
    from geryon.search.factory import build_searcher

    cases = yaml.safe_load(Path(cases_file).read_text(encoding="utf-8"))["cases"]
    paths = [p.strip() for p in db_spec.split(",") if p.strip()]
    searcher = build_searcher(db_paths=paths)  # 단일도 federation 의 특수 경우
    hits = 0
    per_case: list[dict] = []
    print(f"=== {Path(cases_file).name}  ({len(cases)} cases) ===")
    for c in cases:
        q, exp, k = c["query"], c["expected_title"], int(c.get("top_k", 5))
        titles = [h.title for h in searcher.search(q, k=k)]
        rank = next((i + 1 for i, t in enumerate(titles) if exp in (t or "")), None)
        ok = rank is not None
        hits += ok
        print(f"  {'✓' if ok else '✗'} [{q}] → {exp!r} "
              f"({'rank ' + str(rank) if ok else 'MISS'}); top: {[t[:30] for t in titles[:2]]}")
        per_case.append({
            "query": q, "expected_title": exp, "ok": ok, "rank": rank,
            "n_terms": len(c.get("_terms") or []),  # 골든 케이스의 희소어 개수(키워드 편향 분석용)
        })
    pct = hits * 100 // len(cases) if cases else 0
    print(f"  → hit {hits}/{len(cases)} ({pct}%)\n")
    if json_out:
        Path(json_out).write_text(json.dumps(per_case, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  케이스별 결과 → {json_out}")
    return hits, len(cases)


if __name__ == "__main__":
    import os
    args = [a for a in sys.argv[1:] if not a.startswith("--json-out")]
    json_out = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--json-out=")), None)
    cf = args[0]
    db = args[1] if len(args) > 1 else os.getenv("GERYON_DB", "")
    run(cf, db, json_out)
