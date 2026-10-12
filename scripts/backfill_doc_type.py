#!/usr/bin/env python3
"""기존 문서의 `doc_type` 을 재수집 없이 DB 안에서 다시 계산한다.

■ 왜 필요한가

`documents.doc_type` 컬럼은 v11 에서 신설됐다(이전엔 저장 경로가 없어 분류기가
동작해도 전부 버려졌다). 그래서 **v11 이전에 색인된 문서는 전부 doc_type=NULL** 이고,
`doc_type` 은 `content_hash` 계산에 안 들어가 평소 증분 싱크로는 채워지지 않는다
(날짜 백필 때와 같은 이유 — `backfill_dates.py` 참고).

`geryon ingest --full` 로 재색인해도 되지만, Bronze 원문을 다시 읽어 HTML→마크다운
변환까지 전부 다시 돈다(실측: git 수천 건에 수 분 — confluence 수만 건이면 그보다
훨씬 오래 걸린다). `doc_type` 은 DB에 이미 있는 `title`·`hierarchy`·`tags`(+ git 은
`source_id` 의 경로)만으로 다시 계산되므로, 재수집 없이 **UPDATE 만으로** 끝난다.

■ 분류 로직(운영 코드와 동일 함수를 그대로 쓴다 — 공식을 두 벌로 만들지 않는다)

- Confluence·Jira 등 비-git 소스: `classify_doc_type(title, hierarchy, tags)`.
  매칭 없으면 그대로 NULL(분류기가 원래도 못 분류하는 제목 — 버그 아님).
- Git 커밋(`hierarchy` 끝이 "commits"): `classify_commit_type(title)` 매칭이면 그
  유형, 아니면 "commit"(접두사 없는 저장소는 예전과 동일).
- Git 파일: `source_id`(`{repo}:{경로}`)의 확장자로 문서/코드 판별(`git_repo.py` 와
  같은 `_DOC_EXT`/`_CODE_EXT`). 문서 파일은 `classify_doc_type(...) or "doc"`,
  코드 파일은 "code".

■ 사용법

    python scripts/backfill_doc_type.py                  # dry-run(기본) — 세기만 한다
    python scripts/backfill_doc_type.py --apply           # 실제 반영
    python scripts/backfill_doc_type.py --apply --force   # NULL 뿐 아니라 전체 재분류
                                                           # (분류 규칙을 고친 뒤 쓴다)

**기본이 dry-run 이다.** 운영 인덱스를 건드리는 도구라 `--apply` 를 명시해야만 쓴다.
`--force` 없이는 이미 doc_type 이 있는 문서는 건드리지 않는다(멱등 — 두 번 돌려도 안전).
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
from geryon.domain.models import SourceType  # noqa: E402
from geryon.index.classify import classify_doc_type, classify_commit_type  # noqa: E402
from geryon.connectors.git_repo import _DOC_EXT, _CODE_EXT  # noqa: E402

_GIT_SOURCES = {SourceType.GITHUB.value, SourceType.GITLAB.value, SourceType.BITBUCKET.value}


def _classify_row(
    source: str, source_id: str, title: str, hierarchy: list[str], tags: list[str]
) -> tuple[str | None, bool]:
    """(새 doc_type, 확장자를 알아봤는가). 두 번째 값이 False 면 집계에서 "알 수 없는
    확장자"로 따로 센다(분류 규칙 공백과 "애초에 이 파일을 모른다"를 구분하기 위함)."""
    if source not in _GIT_SOURCES:
        return classify_doc_type(title, hierarchy, tags), True

    if hierarchy and hierarchy[-1] == "commits":
        return classify_commit_type(title) or "commit", True

    # 파일: source_id = "{repo}:{상대경로}" (git_repo.py 의 포맷을 그대로 신뢰)
    rel = source_id.split(":", 1)[1] if ":" in source_id else ""
    ext = Path(rel).suffix.lower()
    if ext in _DOC_EXT:
        return classify_doc_type(title, hierarchy, tags) or "doc", True
    if ext in _CODE_EXT:
        return "code", True
    return None, False  # 새 Bronze 설정 등으로 들어온, _DOC_EXT/_CODE_EXT 밖의 확장자


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DB_PATH), help=f"인덱스 DB (기본: {DB_PATH})")
    ap.add_argument("--apply", action="store_true",
                    help="실제로 UPDATE 한다. 없으면 dry-run(세기만 함)")
    ap.add_argument("--force", action="store_true",
                    help="이미 doc_type 이 있는 문서도 다시 분류한다(규칙 변경 후 전체 재적용)")
    a = ap.parse_args()

    db = Path(a.db)
    if not db.exists():
        print(f"DB 없음: {db}", file=sys.stderr)
        return 2

    mode = "APPLY" if a.apply else "DRY-RUN"
    print(f"■ {db}  [{mode}]{' [FORCE]' if a.force else ''}\n")

    conn = sqlite3.connect(str(db))
    where = "1=1" if a.force else "doc_type IS NULL"
    rows = conn.execute(
        f"SELECT doc_id, source, source_id, title, hierarchy, tags, doc_type "
        f"FROM documents WHERE {where}"
    ).fetchall()

    changed: list[tuple[str, str]] = []  # (doc_type, doc_id) — executemany 순서, UPDATE 는 PK(doc_id) 기준
    unmatched: Counter = Counter()   # 분류 규칙에 안 걸림(버그 아님)
    noop: Counter = Counter()        # force 인데 결과가 기존 값과 같음
    unknown_ext: Counter = Counter()  # git 인데 확장자가 _DOC_EXT/_CODE_EXT 밖

    for doc_id, source, source_id, title, hier_j, tags_j, cur in rows:
        try:
            hierarchy = json.loads(hier_j) if hier_j else []
            tags = json.loads(tags_j) if tags_j else []
        except Exception:
            hierarchy, tags = [], []
        new, known = _classify_row(source, source_id, title or "", hierarchy, tags)
        if not known:
            unknown_ext[source] += 1
            continue
        if new is None:
            unmatched[source] += 1
            continue
        if new == cur:
            noop[source] += 1
            continue
        changed.append((new, doc_id))

    src_of = {doc_id: source for doc_id, source, *_ in rows}
    by_src_changed: Counter = Counter(src_of[doc_id] for _new, doc_id in changed)

    print("■ 재분류 가능")
    for src, n in by_src_changed.most_common():
        print(f"   {src:12s} {n:>7,}건  재분류됨")
    for src, n in unmatched.most_common():
        print(f"   {src:12s} {n:>7,}건  분류 규칙에 안 걸림(정상 — NULL 유지)")
    for src, n in unknown_ext.most_common():
        print(f"   {src:12s} {n:>7,}건  ⚠ 알 수 없는 확장자 — 건드리지 않음")
    if a.force:
        for src, n in noop.most_common():
            print(f"   {src:12s} {n:>7,}건  이미 같은 값 — 변경 없음")
    if not changed:
        print("   (재분류할 문서 없음)")

    if not a.apply:
        if changed:
            print(f"\n→ dry-run. {len(changed):,}건을 고칠 수 있습니다. 실행하려면 --apply 를 붙이세요.")
        return 0

    if changed:
        # ⚠️ doc_id(PK) 로 UPDATE — source_id 는 PK 가 아니라 전체 스캔이 된다
        #    (static_score 재계산에서 드러난 실측 O(N²) 함정, backfill_dates.py 참고).
        conn.executemany("UPDATE documents SET doc_type = ? WHERE doc_id = ?", changed)
        conn.commit()
        print(f"\n✓ {len(changed):,}건 재분류 완료")

    left = conn.execute("SELECT source, COUNT(*) FROM documents WHERE doc_type IS NULL GROUP BY 1 ORDER BY 2 DESC").fetchall()
    print("\n■ 적용 후 남은 doc_type NULL")
    for src, n in left:
        print(f"   {src:12s} {n:>7,}건")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
