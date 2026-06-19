#!/usr/bin/env python3
"""범용 골드 평가 — cases.yml + DB → top_k 에 expected_title 부분포함이면 hit. 소스 무관.

    python scripts/golden_eval.py tests/golden/cases_bitbucket.yml <db_path>
"""
import sys
from pathlib import Path


def run(cases_file: str, db_spec: str) -> tuple[int, int]:
    """db_spec: 단일 경로 또는 콤마 구분 다중(federation[25])."""
    import yaml
    from geryon.search.factory import build_searcher

    cases = yaml.safe_load(Path(cases_file).read_text(encoding="utf-8"))["cases"]
    paths = [p.strip() for p in db_spec.split(",") if p.strip()]
    searcher = build_searcher(db_paths=paths)  # 단일도 federation 의 특수 경우
    hits = 0
    print(f"=== {Path(cases_file).name}  ({len(cases)} cases) ===")
    for c in cases:
        q, exp, k = c["query"], c["expected_title"], int(c.get("top_k", 5))
        titles = [h.title for h in searcher.search(q, k=k)]
        rank = next((i + 1 for i, t in enumerate(titles) if exp in (t or "")), None)
        ok = rank is not None
        hits += ok
        print(f"  {'✓' if ok else '✗'} [{q}] → {exp!r} "
              f"({'rank ' + str(rank) if ok else 'MISS'}); top: {[t[:30] for t in titles[:2]]}")
    pct = hits * 100 // len(cases) if cases else 0
    print(f"  → hit {hits}/{len(cases)} ({pct}%)\n")
    return hits, len(cases)


if __name__ == "__main__":
    import os
    cf = sys.argv[1]
    db = sys.argv[2] if len(sys.argv) > 2 else os.getenv("GERYON_DB", "")
    run(cf, db)
