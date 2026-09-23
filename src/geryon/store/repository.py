import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone
from collections.abc import Iterator
from typing import cast

from geryon.domain.models import Document, SourceType, Attachment, SyncState
from geryon.config import DB_PATH
from geryon.store.db import init_db

class MultiRepository:
    """federation[25]: 여러 SqliteRepository 를 묶어 doc 조회를 순회한다.

    검색(BM25+rerank)은 HybridRetriever 가 각 repository 를 직접 다루므로,
    여기서는 server 의 보조 도구(get_document 등)가 doc_id 로 doc 을 찾을 때 순회만 한다.
    get_connection/그 외는 대표(첫) DB 로 위임(browse·list_sources 는 대표 DB 기준 — 한계 명시).
    """
    def __init__(self, repositories: list["SqliteRepository"]) -> None:
        self.repositories = repositories
        self._primary = repositories[0]

    def get(self, doc_id: str) -> Document | None:
        for r in self.repositories:
            d = r.get(doc_id)
            if d is not None:
                return d
        return None

    def get_connection(self):  # noqa: ANN201 — browse/list_sources 는 대표 DB
        return self._primary.get_connection()

    def __getattr__(self, name: str):  # 그 외 메서드는 대표 DB 로 위임
        return getattr(self._primary, name)


class Repository:
    def upsert(self, doc: Document, force: bool = False) -> str:
        """content_hash 비교 → "skipped"(동일) / "inserted"(신규) / "updated"(덮어쓰기)."""
        raise NotImplementedError

    def get(self, doc_id: str) -> Document | None:
        raise NotImplementedError

    def iter_all(self) -> Iterator[Document]:
        raise NotImplementedError

    def delete(self, doc_id: str) -> None:
        raise NotImplementedError

    def get_sync_state(self, source: SourceType) -> SyncState | None:
        raise NotImplementedError

    def upsert_sync_state(self, state: SyncState) -> None:
        raise NotImplementedError

    def add_seen_doc(self, source: SourceType, doc_id: str) -> None:
        raise NotImplementedError

    def clear_seen_docs(self, source: SourceType) -> None:
        raise NotImplementedError

    def get_seen_docs(self, source: SourceType) -> list[str]:
        raise NotImplementedError

    def get_doc_ids_by_source(self, source: SourceType) -> list[str]:
        raise NotImplementedError

class SqliteRepository(Repository):
    db_path: Path

    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = init_db(self.db_path)
        return self._conn

    def upsert(self, doc: Document, force: bool = False) -> str:
        conn = self.get_connection()
        cursor = conn.cursor()

        # 1. 존재 여부 + content_hash 비교 → inserted / updated / skipped 판정
        _ = cursor.execute("SELECT content_hash FROM documents WHERE doc_id = ?;", (doc.doc_id,))
        row = cursor.fetchone()
        existed = row is not None
        if not force and existed and row[0] == doc.content_hash:
            return "skipped"

        # Start a transaction to ensure atomic updates across documents, attachments, and document_tags
        try:
            # 2. Insert or replace standard columns
            created_at_str = doc.created_at.isoformat() if doc.created_at else None
            updated_at_str = doc.updated_at.isoformat() if doc.updated_at else None
            ingested_at_str = doc.ingested_at.isoformat() if doc.ingested_at else None

            tags_json = json.dumps(doc.tags)
            hierarchy_json = json.dumps(doc.hierarchy)
            raw_meta_json = json.dumps(doc.raw_meta)

            # static_score는 별도 사전계산으로 관리 — upsert 시 기존 값을 보존
            _ = cursor.execute("SELECT static_score FROM documents WHERE doc_id = ?;", (doc.doc_id,))
            existing_row = cursor.fetchone()
            preserved_static_score = float(existing_row[0]) if existing_row and existing_row[0] is not None else 0.0

            _ = cursor.execute(
                """
                INSERT OR REPLACE INTO documents (
                    doc_id, source, source_id, space_or_repo, url, title,
                    body_markdown, summary, tags, hierarchy, author,
                    created_at, updated_at, content_hash, ingested_at, raw_meta, static_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    doc.doc_id,
                    doc.source.value,
                    doc.source_id,
                    doc.space_or_repo,
                    doc.url,
                    doc.title,
                    doc.body_markdown,
                    doc.summary,
                    tags_json,
                    hierarchy_json,
                    doc.author,
                    created_at_str,
                    updated_at_str,
                    doc.content_hash,
                    ingested_at_str,
                    raw_meta_json,
                    preserved_static_score,
                )
            )

            # 3. Handle attachments (clean & re-insert)
            _ = cursor.execute("DELETE FROM attachments WHERE doc_id = ?;", (doc.doc_id,))
            for att in doc.attachments:
                _ = cursor.execute(
                    """
                    INSERT INTO attachments (doc_id, filename, media_type, local_path, url)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (doc.doc_id, att.filename, att.media_type, att.local_path, att.url)
                )

            # 4. Handle document_tags (clean & re-insert)
            _ = cursor.execute("DELETE FROM document_tags WHERE doc_id = ?;", (doc.doc_id,))
            for tag in doc.tags:
                _ = cursor.execute(
                    """
                    INSERT INTO document_tags (doc_id, tag)
                    VALUES (?, ?);
                    """,
                    (doc.doc_id, tag)
                )

            # 5. Handle document_categories (clean & re-insert) — Silver 소관
            _ = cursor.execute("DELETE FROM document_categories WHERE doc_id = ?;", (doc.doc_id,))
            for cat in getattr(doc, "category", None) or []:
                _ = cursor.execute(
                    "INSERT OR IGNORE INTO document_categories (doc_id, category) VALUES (?, ?);",
                    (doc.doc_id, cat)
                )

            # 6. 조사 정규화 FTS(documents_fts_norm) 수동 갱신 — 트리거 불가(정규화 함수 필요), v7
            from geryon.index.korean import normalize_korean
            _ = cursor.execute("DELETE FROM documents_fts_norm WHERE doc_id = ?;", (doc.doc_id,))
            _ = cursor.execute(
                "INSERT INTO documents_fts_norm (doc_id, ntitle, nbody) VALUES (?, ?, ?);",
                (doc.doc_id, normalize_korean(doc.title), normalize_korean(doc.body_markdown)),
            )

            conn.commit()
            return "updated" if existed else "inserted"
        except Exception as e:
            conn.rollback()
            raise e

    def get(self, doc_id: str) -> Document | None:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 1. Fetch main document
        _ = cursor.execute(
            """
            SELECT doc_id, source, source_id, space_or_repo, url, title,
                   body_markdown, summary, tags, hierarchy, author,
                   created_at, updated_at, content_hash, ingested_at, raw_meta
            FROM documents WHERE doc_id = ?;
            """,
            (doc_id,)
        )
        doc_row = cursor.fetchone()
        if not doc_row:
            return None

        # 2. Fetch attachments
        _ = cursor.execute(
            """
            SELECT filename, media_type, local_path, url
            FROM attachments WHERE doc_id = ?;
            """,
            (doc_id,)
        )
        att_rows = cursor.fetchall()
        attachments = [
            Attachment(
                filename=cast(str, row[0]),
                media_type=cast(str | None, row[1]),
                local_path=cast(str | None, row[2]),
                url=cast(str | None, row[3])
            )
            for row in att_rows
        ]

        # 3. Parse fields
        tags = cast(list[str], json.loads(cast(str, doc_row[8])))
        hierarchy = cast(list[str], json.loads(cast(str, doc_row[9])))
        raw_meta = cast(dict[str, object], json.loads(cast(str, doc_row[15])))

        # category — 정규화 테이블에서 조회
        _ = cursor.execute("SELECT category FROM document_categories WHERE doc_id = ?;", (doc_id,))
        category = [cast(str, r[0]) for r in cursor.fetchall()]

        created_at = datetime.fromisoformat(cast(str, doc_row[11])) if doc_row[11] else None
        updated_at = datetime.fromisoformat(cast(str, doc_row[12])) if doc_row[12] else None
        ingested_at = datetime.fromisoformat(cast(str, doc_row[14])) if doc_row[14] else datetime.now(timezone.utc).replace(tzinfo=None)

        return Document(
            doc_id=cast(str, doc_row[0]),
            source=SourceType(cast(str, doc_row[1])),
            source_id=cast(str, doc_row[2]),
            space_or_repo=cast(str | None, doc_row[3]),
            url=cast(str | None, doc_row[4]),
            title=cast(str, doc_row[5]),
            body_markdown=cast(str, doc_row[6]),
            summary=cast(str | None, doc_row[7]),
            tags=tags,
            category=category,
            hierarchy=hierarchy,
            author=cast(str | None, doc_row[10]),
            created_at=created_at,
            updated_at=updated_at,
            attachments=attachments,
            raw_meta=raw_meta,
            content_hash=cast(str, doc_row[13]),
            ingested_at=ingested_at
        )

    def iter_all(self) -> Iterator[Document]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        _ = cursor.execute("SELECT doc_id FROM documents;")
        doc_ids = [cast(str, row[0]) for row in cursor.fetchall()]
        
        for doc_id in doc_ids:
            doc = self.get(doc_id)
            if doc:
                yield doc

    def delete(self, doc_id: str) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Delete attachments and document_tags first
            _ = cursor.execute("DELETE FROM attachments WHERE doc_id = ?;", (doc_id,))
            _ = cursor.execute("DELETE FROM document_tags WHERE doc_id = ?;", (doc_id,))
            _ = cursor.execute("DELETE FROM documents WHERE doc_id = ?;", (doc_id,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e

    def latest_doc_updated_at(self, source: SourceType) -> datetime | None:
        """그 소스 문서들의 최신 source-side 수정시각(MAX updated_at).
        DB만 공유받아도 이 워터마크 이후만 재수집(--since-db)할 수 있게 한다(Bronze 불필요)."""
        conn = self.get_connection()
        cur = conn.cursor()
        _ = cur.execute("SELECT MAX(updated_at) FROM documents WHERE source = ?;", (source.value,))
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        try:
            return datetime.fromisoformat(row[0])
        except Exception:
            return None

    def get_sync_state(self, source: SourceType) -> SyncState | None:
        conn = self.get_connection()
        cursor = conn.cursor()
        _ = cursor.execute(
            "SELECT last_synced_at, cursor, doc_count FROM sync_state WHERE source = ?;",
            (source.value,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        
        last_synced_at_str, cursor_str, doc_count = row
        last_synced_at = datetime.fromisoformat(last_synced_at_str) if last_synced_at_str else None
        
        _ = cursor.execute("SELECT doc_id FROM sync_seen_docs WHERE source = ?;", (source.value,))
        seen_rows = cursor.fetchall()
        last_seen_doc_ids = [cast(str, r[0]) for r in seen_rows]
        
        return SyncState(
            source=source,
            last_synced_at=last_synced_at,
            cursor=cursor_str,
            last_seen_doc_ids=last_seen_doc_ids,
            doc_count=doc_count
        )

    def upsert_sync_state(self, state: SyncState) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        last_synced_at_str = state.last_synced_at.isoformat() if state.last_synced_at else None
        
        try:
            _ = cursor.execute(
                """
                INSERT OR REPLACE INTO sync_state (source, last_synced_at, cursor, doc_count)
                VALUES (?, ?, ?, ?);
                """,
                (state.source.value, last_synced_at_str, state.cursor, state.doc_count)
            )
            
            _ = cursor.execute("DELETE FROM sync_seen_docs WHERE source = ?;", (state.source.value,))
            for doc_id in state.last_seen_doc_ids:
                _ = cursor.execute(
                    "INSERT OR IGNORE INTO sync_seen_docs (source, doc_id) VALUES (?, ?);",
                    (state.source.value, doc_id)
                )
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e

    def add_seen_doc(self, source: SourceType, doc_id: str) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            _ = cursor.execute(
                "INSERT OR IGNORE INTO sync_seen_docs (source, doc_id) VALUES (?, ?);",
                (source.value, doc_id)
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e

    def clear_seen_docs(self, source: SourceType) -> None:
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            _ = cursor.execute("DELETE FROM sync_seen_docs WHERE source = ?;", (source.value,))
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e

    def get_seen_docs(self, source: SourceType) -> list[str]:
        conn = self.get_connection()
        cursor = conn.cursor()
        _ = cursor.execute("SELECT doc_id FROM sync_seen_docs WHERE source = ?;", (source.value,))
        return [cast(str, r[0]) for r in cursor.fetchall()]

    def get_doc_ids_by_source(self, source: SourceType) -> list[str]:
        conn = self.get_connection()
        cursor = conn.cursor()
        _ = cursor.execute("SELECT doc_id FROM documents WHERE source = ?;", (source.value,))
        return [cast(str, r[0]) for r in cursor.fetchall()]

    def get_static_score(self, doc_id: str) -> float:
        """문서의 static_score를 반환."""
        conn = self.get_connection()
        cursor = conn.cursor()
        _ = cursor.execute("SELECT static_score FROM documents WHERE doc_id = ?;", (doc_id,))
        row = cursor.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def update_static_scores(self, scores: dict[str, float]) -> int:
        """source_id → static_score 배치 업데이트 (인덱싱-시 사전계산용)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        n = 0
        try:
            for source_id, score in scores.items():
                _ = cursor.execute(
                    "UPDATE documents SET static_score = ? WHERE source_id = ?;",
                    (score, source_id)
                )
                n += cursor.rowcount
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        return n

    def upsert_page_links(self, links: list[tuple[str, str]],
                          owner_src_ids: set[str] | None = None) -> int:
        """(src_page_id, dst_page_id) 링크 배치 삽입.

        Args:
            links: 이번에 색인한 문서들에서 추출한 링크.
            owner_src_ids: **이번 실행이 소유한 src_page_id 집합.** 주면 그 범위만
                지우고 다시 넣는다. 안 주면 `links` 에 등장하는 src 만 지운다.

        ⚠️ 2026-09-20 데이터 손실 버그 수정.

        이전 구현은 `DELETE FROM page_links` 로 **테이블 전체**를 지우고 이번
        실행분만 넣었다. 그런데 ingest 는 **원천별로** 돈다 — `--source jira`
        전체 색인이 confluence 가 쌓아 둔 링크를 통째로 날렸다.

        실측: 수만 개였던 `page_links` 가 jira `--full` 한 번 뒤
        **0개**가 됐다. `static_score` 의 backlink 성분(가중치 0.4, 셋 중 최대)이
        전 문서에서 죽었는데 아무 경고도 없었다.

        삭제 범위를 «이번 실행이 소유한 src» 로 좁혀 다른 원천의 링크를 보존한다.
        링크가 사라진 문서도 정리되도록, 이번에 나온 링크의 src 만이 아니라
        호출부가 넘긴 소유 집합 전체를 지운다.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        scope = set(owner_src_ids) if owner_src_ids is not None else {s for s, _ in links}
        try:
            if scope:
                cursor.executemany(
                    "DELETE FROM page_links WHERE src_page_id = ?;",
                    [(s,) for s in scope],
                )
            cursor.executemany(
                "INSERT OR IGNORE INTO page_links (src_page_id, dst_page_id) VALUES (?, ?);",
                links
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        return len(links)

    def backlink_counts(self) -> dict[str, int]:
        """저장된 `page_links` **전체**에서 수신 링크 수를 역집계한다.

        ⚠️ 2026-09-20 신설. 이전에는 호출부가 «이번 실행에서 추출한 링크»만으로
        집계했다. 그때는 `upsert_page_links` 가 매번 테이블을 비웠으므로 둘이
        같았지만, 삭제 범위를 원천별로 좁힌 뒤로는 **같지 않다** — 이번 실행분만
        세면 다른 원천이 건 backlink 를 못 본다. 저장된 그래프를 보는 게 맞다.
        """
        conn = self.get_connection()
        counts: dict[str, int] = {}
        for (dst,) in conn.execute("SELECT dst_page_id FROM page_links;"):
            key = str(dst)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def get_related_by_links(self, source_id: str, k: int = 10) -> list[str]:
        """page_links에서 1-hop 형제 doc_ids 반환."""
        conn = self.get_connection()
        cursor = conn.cursor()
        # outgoing links from source_id
        _ = cursor.execute(
            """
            SELECT d.doc_id FROM page_links pl
            JOIN documents d ON pl.dst_page_id = d.source_id
            WHERE pl.src_page_id = ?
            LIMIT ?;
            """,
            (source_id, k)
        )
        outgoing = [cast(str, r[0]) for r in cursor.fetchall()]
        # backlinks to source_id
        _ = cursor.execute(
            """
            SELECT d.doc_id FROM page_links pl
            JOIN documents d ON pl.src_page_id = d.source_id
            WHERE pl.dst_page_id = ?
            LIMIT ?;
            """,
            (source_id, k)
        )
        backlinks = [cast(str, r[0]) for r in cursor.fetchall()]
        # deduplicate, prefer outgoing order
        seen: set[str] = set()
        result: list[str] = []
        for doc_id in outgoing + backlinks:
            if doc_id not in seen:
                seen.add(doc_id)
                result.append(doc_id)
        return result[:k]

    def reapply_categories(self, rules: list) -> int:
        """규칙 변경 시 category만 경량 재적용 — 본문/FTS/벡터/content_hash 불변."""
        from geryon.index.category import categories_for
        conn = self.get_connection()
        cursor = conn.cursor()
        n = 0
        for doc in self.iter_all():
            cats = categories_for(doc, rules)
            _ = cursor.execute("DELETE FROM document_categories WHERE doc_id = ?;", (doc.doc_id,))
            for c in cats:
                _ = cursor.execute(
                    "INSERT OR IGNORE INTO document_categories (doc_id, category) VALUES (?, ?);",
                    (doc.doc_id, c)
                )
            n += 1
        conn.commit()
        return n
