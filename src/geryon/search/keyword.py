import math
import re
from datetime import datetime
from typing import cast
from typing_extensions import override

from geryon.domain.models import SearchHit, SearchFilter, SourceType
from geryon.search.retriever import Retriever, calculate_recency_decay
from geryon.store.repository import SqliteRepository
from geryon.config import RECENCY_HALF_LIFE_DAYS, RECENCY_BOOST_CEILING

def clean_fts_query(query: str) -> str:
    """Clean and escape the query to prevent SQLite FTS5 syntax errors."""
    cleaned = re.sub(r"[^\w\s]", " ", query)
    terms = cleaned.split()
    if not terms:
        return ""
    return " AND ".join(f'"{t}"' for t in terms)

def sigmoid(x: float) -> float:
    """Convert negative bm25 score to standard positive score between 0.0 and 1.0."""
    try:
        if x > 100:
            return 0.0
        elif x < -100:
            return 1.0
        return 1.0 / (1.0 + math.exp(x))
    except OverflowError:
        return 0.0 if x > 0 else 1.0

class KeywordRetriever(Retriever):
    """Retriever implementation using SQLite FTS5 BM25 keyword search, with LIKE fallback for short terms."""

    repository: SqliteRepository

    def __init__(self, repository: SqliteRepository | None = None) -> None:
        """Initialize the KeywordRetriever with an optional SqliteRepository."""
        if repository is None:
            self.repository = SqliteRepository()
        else:
            self.repository = repository

    @override
    def search(
        self,
        query: str,
        k: int = 10,
        offset: int = 0,
        filters: SearchFilter | None = None
    ) -> list[SearchHit]:
        """Perform search on the documents. Uses FTS5 BM25 for terms >= 3 chars, fallback to LIKE for shorter terms.
        
        Complies with the Search Relevance Gate. If the query is empty or has no matches, returns [] explicitly.
        """
        if not query or not query.strip():
            return []

        cleaned = re.sub(r"[^\w\s]", " ", query)
        terms = cleaned.split()
        if not terms:
            return []

        # 한국어 조사 정규화(v7): 쿼리 토큰의 어절 끝 조사를 떼어 색인(정규화 FTS)과 토큰을 일치시킨다.
        # "마트를"→"마트". porter unicode61은 2글자도 색인하므로 ≥2자 토큰만 FTS, 1글자만 남으면 LIKE 폴백.
        from geryon.index.korean import strip_josa
        long_terms = [s for s in (strip_josa(t) for t in terms) if len(s) >= 2]
        has_long_term = len(long_terms) > 0

        if not has_long_term:
            # Fallback to LIKE query
            conn = self.repository.get_connection()
            cursor = conn.cursor()

            query_parts = [
                """
                SELECT 
                    d.doc_id,
                    d.title,
                    d.url,
                    d.source,
                    d.space_or_repo,
                    d.body_markdown,
                    d.created_at,
                    d.updated_at
                FROM documents d
                WHERE 1=1
                """
            ]
            params: list[object] = []

            for t in terms:
                query_parts.append("AND (d.title LIKE ? OR d.body_markdown LIKE ?)")
                params.extend([f"%{t}%", f"%{t}%"])

            if filters:
                if filters.sources:
                    placeholders = ", ".join("?" for _ in filters.sources)
                    query_parts.append(f"AND d.source IN ({placeholders})")
                    params.extend(s.value if hasattr(s, "value") else str(s) for s in filters.sources)

                if filters.spaces_or_repos:
                    placeholders = ", ".join("?" for _ in filters.spaces_or_repos)
                    query_parts.append(f"AND d.space_or_repo IN ({placeholders})")
                    params.extend(filters.spaces_or_repos)

                if filters.tags:
                    placeholders = ", ".join("?" for _ in filters.tags)
                    query_parts.append(
                        f"AND EXISTS (SELECT 1 FROM document_tags WHERE document_tags.doc_id = d.doc_id AND document_tags.tag IN ({placeholders}))"
                    )
                    params.extend(filters.tags)

                if filters.categories:
                    placeholders = ", ".join("?" for _ in filters.categories)
                    query_parts.append(
                        f"AND EXISTS (SELECT 1 FROM document_categories WHERE document_categories.doc_id = d.doc_id AND document_categories.category IN ({placeholders}))"
                    )
                    params.extend(filters.categories)

                if filters.authors:
                    placeholders = ", ".join("?" for _ in filters.authors)
                    query_parts.append(f"AND d.author IN ({placeholders})")
                    params.extend(filters.authors)

                if filters.date_from:
                    query_parts.append("AND COALESCE(d.updated_at, d.created_at) >= ?")
                    params.append(filters.date_from.isoformat())

                if filters.date_to:
                    query_parts.append("AND COALESCE(d.updated_at, d.created_at) <= ?")
                    params.append(filters.date_to.isoformat())

            query_parts.append("LIMIT ? OFFSET ?")
            params.extend([k, offset])

            sql = "\n".join(query_parts)
            _ = cursor.execute(sql, params)
            rows: list[tuple[object, ...]] = cursor.fetchall()

            hits: list[SearchHit] = []
            for row in rows:
                doc_id = cast(str, row[0])
                title = cast(str, row[1])
                url = cast(str | None, row[2])
                source = SourceType(cast(str, row[3]))
                space_or_repo = cast(str | None, row[4])
                body_markdown = cast(str, row[5])
                created_at_str = cast(str | None, row[6])
                updated_at_str = cast(str | None, row[7])

                created_at = datetime.fromisoformat(created_at_str) if created_at_str else None
                updated_at = datetime.fromisoformat(updated_at_str) if updated_at_str else None

                # Generate a simple highlighted snippet in Python
                snippet_text = ""
                for t in terms:
                    idx = body_markdown.lower().find(t.lower())
                    if idx != -1:
                        start = max(0, idx - 20)
                        end = min(len(body_markdown), idx + len(t) + 40)
                        snippet_text = body_markdown[start:end]
                        # Highlight term
                        pattern = re.compile(re.escape(t), re.IGNORECASE)
                        snippet_text = pattern.sub(lambda m: f"<b>{m.group(0)}</b>", snippet_text)
                        if start > 0:
                            snippet_text = "..." + snippet_text
                        if end < len(body_markdown):
                            snippet_text = snippet_text + "..."
                        break
                if not snippet_text:
                    snippet_text = body_markdown[:64] + "..." if len(body_markdown) > 64 else body_markdown

                base_score = 0.5
                decay_factor = calculate_recency_decay(updated_at or created_at, RECENCY_HALF_LIFE_DAYS)
                score = base_score * (1.0 + RECENCY_BOOST_CEILING * decay_factor)

                hits.append(
                    SearchHit(
                        doc_id=doc_id,
                        title=title,
                        url=url,
                        source=source,
                        space_or_repo=space_or_repo,
                        snippet=snippet_text,
                        score=score
                    )
                )
            return hits

        # FTS5 BM25 search — 조사 정규화 FTS(documents_fts_norm, v7) 대상.
        # 컬럼: doc_id(col0), ntitle(col1), nbody(col2). bm25(w0,w1,w2): title 5x 가중.
        # snippet은 정규화본이 아닌 원본 d.body_markdown에서 생성(가독성).
        TITLE_WEIGHT = 5.0
        # OR 결합: AND는 "문서·목록" 같은 부수어까지 강제 매칭해 정답을 탈락시킴. BM25가 매칭강도로 랭킹.
        fts_query = " OR ".join(f'"{t}"' for t in long_terms)
        conn = self.repository.get_connection()
        cursor = conn.cursor()

        query_parts = [
            f"""
            SELECT
                d.doc_id,
                d.title,
                d.url,
                d.source,
                d.space_or_repo,
                substr(d.body_markdown, 1, 200) AS snippet_text,
                snippet(documents_fts_norm, 2, '', ' … ', '…', 12) AS match_passage,
                bm25(documents_fts_norm, 0, {TITLE_WEIGHT}, 1.0) AS bm25_score,
                d.created_at,
                d.updated_at
            FROM documents_fts_norm
            JOIN documents d ON documents_fts_norm.doc_id = d.doc_id
            WHERE documents_fts_norm MATCH ?
            """
        ]
        params = [fts_query]

        if filters:
            if filters.sources:
                placeholders = ", ".join("?" for _ in filters.sources)
                query_parts.append(f"AND d.source IN ({placeholders})")
                params.extend(s.value if hasattr(s, "value") else str(s) for s in filters.sources)

            if filters.spaces_or_repos:
                placeholders = ", ".join("?" for _ in filters.spaces_or_repos)
                query_parts.append(f"AND d.space_or_repo IN ({placeholders})")
                params.extend(filters.spaces_or_repos)

            if filters.tags:
                placeholders = ", ".join("?" for _ in filters.tags)
                query_parts.append(
                    f"AND EXISTS (SELECT 1 FROM document_tags WHERE document_tags.doc_id = d.doc_id AND document_tags.tag IN ({placeholders}))"
                )
                params.extend(filters.tags)

            if filters.categories:
                placeholders = ", ".join("?" for _ in filters.categories)
                query_parts.append(
                    f"AND EXISTS (SELECT 1 FROM document_categories WHERE document_categories.doc_id = d.doc_id AND document_categories.category IN ({placeholders}))"
                )
                params.extend(filters.categories)

            if filters.authors:
                placeholders = ", ".join("?" for _ in filters.authors)
                query_parts.append(f"AND d.author IN ({placeholders})")
                params.extend(filters.authors)

            if filters.date_from:
                query_parts.append("AND COALESCE(d.updated_at, d.created_at) >= ?")
                params.append(filters.date_from.isoformat())

            if filters.date_to:
                query_parts.append("AND COALESCE(d.updated_at, d.created_at) <= ?")
                params.append(filters.date_to.isoformat())

        query_parts.append("ORDER BY bm25_score ASC")
        query_parts.append("LIMIT ? OFFSET ?")
        params.extend([k, offset])

        sql = "\n".join(query_parts)
        _ = cursor.execute(sql, params)
        rows = cursor.fetchall()

        hits = []
        for row in rows:
            doc_id = cast(str, row[0])
            title = cast(str, row[1])
            url = cast(str | None, row[2])
            source = SourceType(cast(str, row[3]))
            space_or_repo = cast(str | None, row[4])
            snippet_text = cast(str, row[5])
            match_passage = cast(str, row[6]) or ""
            bm25_score = cast(float, row[7])
            created_at_str = cast(str | None, row[8])
            updated_at_str = cast(str | None, row[9])

            created_at = datetime.fromisoformat(created_at_str) if created_at_str else None
            updated_at = datetime.fromisoformat(updated_at_str) if updated_at_str else None

            # Convert negative bm25 score to [0.0, 1.0] range
            base_score = sigmoid(bm25_score)
            decay_factor = calculate_recency_decay(updated_at or created_at, RECENCY_HALF_LIFE_DAYS)
            score = base_score * (1.0 + RECENCY_BOOST_CEILING * decay_factor)
            # 쿼리 모든 장어 항목이 title에 포함되면 추가 부스트
            # (BM25가 body 발생빈도에 지배될 때 title 정합 문서를 상위로 보정)
            title_lower = title.lower()
            if all(t.lower() in title_lower for t in long_terms):
                score *= 1.5  # title 완전 포함 부스트

            hits.append(
                SearchHit(
                    doc_id=doc_id,
                    title=title,
                    url=url,
                    source=source,
                    space_or_repo=space_or_repo,
                    snippet=snippet_text,
                    score=score,
                    passage=match_passage,
                )
            )

        return hits
