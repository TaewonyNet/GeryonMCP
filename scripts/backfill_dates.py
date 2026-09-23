#!/usr/bin/env python3
"""`raw_meta` 에 살아 있는 날짜를 `created_at`/`updated_at` 컬럼으로 백필한다.

■ 왜 필요한가

`normalize.normalizer.parse_iso8601` 이 콜론 없는 UTC 오프셋(`+0900`)을 읽지
못해, 원천이 준 날짜를 전부 버리고 NULL 을 넣었다. 파서는 고쳤지만(2026-09-20)
**이미 색인된 DB 는 그대로**다. 전체 재색인은 실물 기준 2시간 40분이 걸리는데,
`raw_meta` 에 원본 문자열이 그대로 남아 있으므로 **재수집·재임베딩 없이 UPDATE
만으로** 복구된다.

실측 피해(2026-09-20, 수만 문서 규모):

    jira        created/updated 100% NULL  → raw_meta 보유율 100%  ✅ 복구 가능
    bitbucket   created         100% NULL  → raw_meta 보유율   0%  ❌ 불가
    confluence  created        극소수 NULL  → raw_meta 보유율   0%  ❌ 불가

bitbucket 은 git 커넥터가 `created` 를 **애초에 metadata 에 담지 않는다**.
컬럼만 빈 게 아니라 수집 단계에서 없는 것이라 백필 대상이 아니다(커넥터 수정
사안). 그래서 이 스크립트는 **복구 가능한 것만** 건드리고 나머지는 보고만 한다.

■ static_score 도 같이 틀어져 있다

`quality_signals._recency_score` 는 날짜가 없으면 중립값 0.3 을 쓴다. 그 결과
**날짜를 못 읽은 문서가 오히려 랭킹에서 유리**했다(날짜 있음 평균 0.1534 <
날짜 없음 평균 0.1754). `--recalc-static` 으로 다시 계산한다.

■ 사용법

    python scripts/backfill_dates.py                  # dry-run(기본) — 세기만 한다
    python scripts/backfill_dates.py --apply          # 날짜 백필
    python scripts/backfill_dates.py --apply --recalc-static

**기본이 dry-run 이다.** 운영 인덱스를 건드리는 도구라 `--apply` 를 명시해야만
쓴다. 실행 전 DB 를 복사해 두는 것을 권한다.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geryon.config import DB_PATH  # noqa: E402
from geryon.normalize.normalizer import parse_iso8601  # noqa: E402

#: raw_meta 안에서 날짜로 인정할 키. 커넥터가 쓰는 이름을 그대로 둔다.
_KEYS = (("created_at", "created"), ("updated_at", "updated"))


def _pick(meta: dict, names: tuple[str, ...]) -> str | None:
    for n in names:
        v = meta.get(n)
        if isinstance(v, str) and v.strip():
            return v
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DB_PATH), help=f"인덱스 DB (기본: {DB_PATH})")
    ap.add_argument("--apply", action="store_true",
                    help="실제로 UPDATE 한다. 없으면 dry-run(세기만 함)")
    ap.add_argument("--recalc-static", action="store_true",
                    help="백필 후 static_score 를 다시 계산한다(--apply 필요)")
    a = ap.parse_args()

    db = Path(a.db)
    if not db.exists():
        print(f"DB 없음: {db}", file=sys.stderr)
        return 2

    mode = "APPLY" if a.apply else "DRY-RUN"
    print(f"■ {db}  [{mode}]\n")

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT doc_id, source, raw_meta, created_at, updated_at FROM documents "
        "WHERE (created_at IS NULL OR updated_at IS NULL) AND raw_meta IS NOT NULL"
    ).fetchall()

    per_src: Counter = Counter()
    fixable: list[tuple[str, str | None, str | None]] = []
    no_date: Counter = Counter()
    unparsed: Counter = Counter()
    noop: Counter = Counter()

    for doc_id, source, raw_meta, cur_c, cur_u in rows:
        try:
            meta = json.loads(raw_meta)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        c_raw = _pick(meta, _KEYS[0])
        u_raw = _pick(meta, _KEYS[1])
        if not c_raw and not u_raw:
            no_date[source] += 1
            continue
        c_dt = parse_iso8601(c_raw) if c_raw else None
        u_dt = parse_iso8601(u_raw) if u_raw else None
        if c_dt is None and u_dt is None:
            # 문자열은 있는데 고친 파서로도 못 읽는다 — 새로운 형식일 수 있다
            unparsed[source] += 1
            continue
        # ⚠️ «지금 비어 있는» 컬럼을 채울 수 있을 때만 복구 대상이다.
        #    raw_meta 에 updated 가 있어도 컬럼이 이미 차 있으면 UPDATE 는 no-op 이고,
        #    그걸 "복구 가능"으로 세면 숫자가 거짓말을 한다.
        #    (실제로 bitbucket 전건이 이렇게 잡혔다 — created 는 raw_meta 에도
        #     없는데 updated 가 이미 채워져 있어서 통과했다.)
        will_fix_c = cur_c is None and c_dt is not None
        will_fix_u = cur_u is None and u_dt is not None
        if not (will_fix_c or will_fix_u):
            noop[source] += 1
            continue
        per_src[source] += 1
        fixable.append((doc_id,
                        c_dt.isoformat() if will_fix_c else None,
                        u_dt.isoformat() if will_fix_u else None))

    print("■ 날짜가 빈 문서 중 raw_meta 로 복구 가능한 것")
    for src, n in per_src.most_common():
        print(f"   {src:12s} {n:>7,}건  복구 가능")
    for src, n in no_date.most_common():
        print(f"   {src:12s} {n:>7,}건  raw_meta 에 날짜 없음 — 커넥터 사안(백필 불가)")
    for src, n in unparsed.most_common():
        print(f"   {src:12s} {n:>7,}건  ⚠ 문자열은 있으나 파싱 실패 — 형식 확인 필요")
    for src, n in noop.most_common():
        print(f"   {src:12s} {n:>7,}건  이미 채워진 컬럼만 해당 — 고칠 것 없음")
    if not fixable:
        print("   (복구 가능한 문서 없음)")

    if not a.apply:
        if fixable:
            print(f"\n→ dry-run. {len(fixable):,}건을 고칠 수 있습니다. "
                  f"실행하려면 --apply 를 붙이세요.")
        return 0

    if fixable:
        # created_at/updated_at 을 «비어 있을 때만» 채운다 — 기존 값은 덮지 않는다.
        conn.executemany(
            "UPDATE documents SET created_at = COALESCE(created_at, ?), "
            "updated_at = COALESCE(updated_at, ?) WHERE doc_id = ?",
            [(c, u, d) for d, c, u in fixable],
        )
        conn.commit()
        print(f"\n✓ {len(fixable):,}건 백필 완료")

    # ⚠️ 백필할 게 없어도 재계산은 돈다. 두 작업은 독립이다 —
    #    앞서 백필만 성공하고 재계산이 실패한 적이 있어(pydantic 검증),
    #    "고칠 날짜가 없으니 재계산도 건너뛴다"가 되면 복구할 방법이 없어진다.
    if a.recalc_static:
        _recalc_static(conn)
    elif fixable:
        print("  ⚠ static_score 는 옛 날짜 기준입니다. --recalc-static 으로 다시 계산하세요.")

    left = conn.execute(
        "SELECT source, COUNT(*) FROM documents WHERE created_at IS NULL "
        "GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall()
    print("\n■ 백필 후 남은 created_at NULL")
    for src, n in left:
        print(f"   {src:12s} {n:>7,}건")
    conn.close()
    return 0


def _recalc_static(conn: sqlite3.Connection) -> None:
    """static_score 재계산. 날짜가 바뀌었으므로 recency 성분이 달라진다.

    `pipeline/ingest.py` 의 full 경로와 **같은 함수**를 쓴다 — 여기서 공식을
    다시 구현하면 두 벌이 되어 갈라진다.
    """
    from geryon.pipeline.quality_signals import compute_static_scores

    backlinks: Counter = Counter()
    try:
        for (dst,) in conn.execute("SELECT dst_page_id FROM page_links"):
            backlinks[str(dst)] += 1
    except sqlite3.Error:
        pass  # page_links 없으면 backlink 성분 0

    # ⚠️ 여기서 `Document`(pydantic) 를 만들지 않는다.
    #
    #    처음엔 만들었는데 두 번 데였다:
    #      1회차 — `source`(enum)·`content_hash`·`ingested_at` 이 필수라 검증에서 튕김
    #      2회차 — 실제 값을 채워 통과시켰더니 수만 건 검증이 15분을 넘겨 SIGTERM
    #
    #    `compute_static_scores` 가 doc 에서 읽는 것은 **네 속성뿐**이다
    #    (`source_id` · `created_at` · `updated_at` · `body_markdown` — 확인함).
    #    그래서 덕 타이핑으로 충분하고, pydantic 검증은 순수 낭비였다.
    #    공식은 여전히 `compute_static_scores` 한 곳에만 있다 — 두 벌이 되지 않는다.
    class _Doc:
        __slots__ = ("source_id", "created_at", "updated_at", "body_markdown")

        def __init__(self, sid, cre, upd, body):
            self.source_id = sid
            self.created_at = cre
            self.updated_at = upd
            self.body_markdown = body

    docs = []
    by_sid: dict[str, list[str]] = {}
    for doc_id, sid, cre, upd, body in conn.execute(
        "SELECT doc_id, source_id, created_at, updated_at, COALESCE(body_markdown,'') "
        "FROM documents"
    ):
        docs.append(_Doc(str(sid), parse_iso8601(cre), parse_iso8601(upd), body))
        by_sid.setdefault(str(sid), []).append(str(doc_id))

    scores = compute_static_scores(docs, dict(backlinks))

    # ⚠️ **`source_id` 로 UPDATE 하지 않는다.**
    #
    #    `documents` 의 제약은 `PRIMARY KEY(doc_id)` 와 `UNIQUE(source, source_id)` 다.
    #    `WHERE source_id = ?` 는 복합 색인의 «선두 컬럼»이 아니라서 색인을 못 타고
    #    **전체 스캔**이 된다(EXPLAIN 확인: `SCAN documents`).
    #    문서 수 N 에 대해 UPDATE 를 N 번 돌리면 O(N²) — 실측 수만 건에서
    #    15분 timeout 을 두 번 넘겼다.
    #
    #    `doc_id` 는 PRIMARY KEY 라 색인 탐색이 된다(`SEARCH ... USING INDEX`).
    #    그래서 source_id → doc_id 로 옮겨 적고 PK 로 갱신한다.
    #
    #    ⚠️ 같은 결함이 `store/repository.py::update_static_scores` 에도 있다
    #       (운영 ingest 가 매번 이 경로를 탄다). 별도 보고했다.
    rows = [(v, d) for k, v in scores.items() for d in by_sid.get(k, ())]
    conn.executemany("UPDATE documents SET static_score = ? WHERE doc_id = ?", rows)
    conn.commit()
    print(f"✓ static_score 재계산 {len(rows):,}건 (source_id {len(scores):,}개)")


if __name__ == "__main__":
    raise SystemExit(main())
