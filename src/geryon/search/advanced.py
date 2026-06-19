"""상세검색(fielded/advanced search) — 각 메타데이터를 독립 필드로 검색.

통합 FTS(`query`)와 달리, 메타데이터별 컬럼 축을 따로 지정해 AND로 결합한다.
- title / body : 조사 정규화 FTS(documents_fts_norm)의 ntitle / nbody 컬럼 MATCH
- author       : 부분매칭(LIKE) — 'Deactivated' 접미사·'홍길동B' 변형을 'name'으로 잡음
- space        : 정확매칭
- tags/categories : 정규화 테이블 EXISTS(다중값 OR)
- date_from/to : created_at/updated_at 범위
지정한 필드만 조건에 들어가며, 본문/제목 FTS가 있으면 bm25 정렬, 없으면 최신순.
query 없이 메타만으로도 검색 가능(예: 작성자가 X인 모든 문서)."""

from __future__ import annotations
import re

from geryon.domain.models import SearchHit, SourceType
from geryon.index.korean import strip_josa
from geryon.index.meta_norm import normalize_author
from geryon.store.repository import SqliteRepository


def _fts_expr(text: str) -> str:
    """자유어를 조사 정규화 OR FTS 식으로. 매칭 토큰 없으면 빈 문자열."""
    toks = [s for s in (strip_josa(t) for t in re.sub(r"[^\w\s]", " ", text).split()) if len(s) >= 2]
    return " OR ".join(f'"{t}"' for t in toks)


def advanced_search(
    repository: SqliteRepository,
    *,
    title: str | None = None,
    body: str | None = None,
    author: str | None = None,
    space: str | None = None,
    tags: list[str] | None = None,
    categories: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    k: int = 10,
    offset: int = 0,
) -> list[SearchHit]:
    """메타데이터 필드별 상세검색. 지정한 필드만 AND로 결합한다."""
    conn = repository.get_connection()

    # 제목/본문 FTS 컬럼 조건 (documents_fts_norm: col1=ntitle, col2=nbody)
    fts_conds: list[str] = []
    fts_params: list[object] = []
    title_expr = _fts_expr(title) if title else ""
    body_expr = _fts_expr(body) if body else ""
    if title_expr:
        fts_conds.append("documents_fts_norm.ntitle MATCH ?")
        fts_params.append(title_expr)
    if body_expr:
        fts_conds.append("documents_fts_norm.nbody MATCH ?")
        fts_params.append(body_expr)
    use_fts = bool(fts_conds)

    where: list[str] = ["1=1"]
    params: list[object] = []
    if author:
        where.append("d.author LIKE ?")
        params.append(f"%{author}%")
    if space:
        where.append("d.space_or_repo = ?")
        params.append(space)
    if tags:
        ph = ", ".join("?" for _ in tags)
        where.append(f"EXISTS (SELECT 1 FROM document_tags t WHERE t.doc_id=d.doc_id AND t.tag IN ({ph}))")
        params.extend(tags)
    if categories:
        ph = ", ".join("?" for _ in categories)
        where.append(f"EXISTS (SELECT 1 FROM document_categories c WHERE c.doc_id=d.doc_id AND c.category IN ({ph}))")
        params.extend(categories)
    if date_from:
        where.append("COALESCE(d.updated_at, d.created_at) >= ?")
        params.append(date_from)
    if date_to:
        where.append("COALESCE(d.updated_at, d.created_at) <= ?")
        params.append(date_to)

    # 어떤 필드도 지정 안 하면 빈 결과(무차별 전체 반환 방지)
    if not use_fts and where == ["1=1"]:
        return []

    cols = "d.doc_id, d.title, d.url, d.source, d.space_or_repo, substr(d.body_markdown,1,200), d.author, d.created_at, d.updated_at"
    if use_fts:
        sql = (
            f"SELECT {cols}, bm25(documents_fts_norm, 0, 5.0, 1.0) AS rank "
            "FROM documents_fts_norm JOIN documents d ON documents_fts_norm.doc_id = d.doc_id "
            f"WHERE {' AND '.join(fts_conds)} AND {' AND '.join(where)} "
            "ORDER BY rank ASC LIMIT ? OFFSET ?"
        )
        all_params = fts_params + params + [k, offset]
    else:
        sql = (
            f"SELECT {cols} FROM documents d "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY COALESCE(d.updated_at, d.created_at) DESC LIMIT ? OFFSET ?"
        )
        all_params = params + [k, offset]

    hits: list[SearchHit] = []
    for row in conn.execute(sql, all_params).fetchall():
        hits.append(
            SearchHit(
                doc_id=row[0], title=row[1], url=row[2],
                source=SourceType(row[3]), space_or_repo=row[4],
                snippet=row[5] or "", score=1.0,
                author=normalize_author(row[6]),  # 읽기 시점 노이즈 정규화(표시값)
            )
        )
    return hits
