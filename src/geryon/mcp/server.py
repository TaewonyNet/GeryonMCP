import json
from pathlib import Path
from typing import Any
from mcp.server.fastmcp import FastMCP

from geryon.config import DB_PATHS
from geryon.store.repository import SqliteRepository, MultiRepository
from geryon.store.vector import VectorStore
from geryon.store.tree import TreeStore
from geryon.search.factory import build_searcher
from geryon.search.query import run_search, hit_to_dict
from geryon.connectors.confluence import ConfluenceConnector
from geryon.pipeline.ingest import IngestionPipeline


def create_mcp_server(db_path: str | Path | None = None) -> FastMCP:
    """Helper function to create and configure the FastMCP server for GeryonMCP.

    db_path 미지정 시 `GERYON_DB`(콤마면 여러 DB). 여러 DB면 federation[25]:
    검색은 각 DB 후보를 모아 통합 rerank, doc 조회는 MultiRepository 가 순회.
    """
    app = FastMCP("GeryonMCP")
    # serverInfo.version 을 앱 버전으로 보고(미설정 시 mcp SDK 버전으로 폴백됨).
    # FastMCP 내부 구조 변화에 대비해 방어적으로 시도.
    try:
        from importlib.metadata import version as _pkg_version
        app._mcp_server.version = _pkg_version("geryonmcp")
    except Exception:
        pass

    if db_path is not None:
        paths = [Path(db_path)]
    else:
        paths = [Path(p) for p in DB_PATHS]  # GERYON_DB(콤마 다중 가능)

    if len(paths) > 1:
        repos = [SqliteRepository(p) for p in paths]
        repository = MultiRepository(repos)
        vector_store = VectorStore(paths[0])   # 대표(보조 도구용)
        tree_store = TreeStore(paths[0])
        gold_search = build_searcher(db_paths=paths)  # federation retriever
    else:
        repository = SqliteRepository(paths[0])
        vector_store = VectorStore(paths[0])
        tree_store = TreeStore(paths[0])
        gold_search = build_searcher(repository=repository, vector_store=vector_store)
    silver_retriever = gold_search.retriever

    # rerank 워밍업: 콜드 로드(~11s)를 첫 검색이 아닌 기동 시점으로 옮긴다(PoC 14 §7).
    # 실패해도 검색은 하이브리드로 fallback 하므로 비차단.
    from geryon.config import RERANK_ENABLED
    if RERANK_ENABLED:
        from geryon.search.rerank import get_reranker
        get_reranker()

    @app.tool()
    def search(
        query: str,
        k: int = 10,
        offset: int = 0,
        sources: list[str] | None = None,
        spaces: list[str] | None = None,
        tags: list[str] | None = None,
        categories: list[str] | None = None,
        authors: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Execute semantic personalized/hybrid search using gold_search.search."""
        # 검색·직렬화는 공유 서비스(geryon.search.query)로 — CLI `geryon search` 와 동일 경로.
        response_data = run_search(
            gold_search, query, k=k, offset=offset, user_id=user_id,
            sources=sources, spaces=spaces, tags=tags, categories=categories,
            authors=authors, date_from=date_from, date_to=date_to,
        )
        return json.dumps(response_data, ensure_ascii=False)

    @app.tool()
    def advanced_search(
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
    ) -> str:
        """메타데이터 필드별 상세검색. 각 필드를 독립 지정해 AND로 결합한다.

        통합 검색(`search`)과 달리 제목/본문/작성자/공간/태그/분류/날짜를 분리 지정한다.
        - title  : 제목에 포함된 단어(조사 무관)
        - body   : 본문에 포함된 단어
        - author : 작성자 부분매칭("홍길동"→"홍길동B (Deactivated)"도 매칭)
        - space  : 공간/저장소 정확매칭
        - tags / categories : 다중값(해당 항목 중 하나라도)
        - date_from / date_to : ISO 날짜(updated_at 우선) 범위
        지정한 필드만 조건에 들어가며, 본문·제목이 없어도 메타만으로 검색된다
        (예: 작성자가 X인 모든 문서). 정렬은 본문/제목 매칭 시 관련도, 아니면 최신순."""
        from geryon.search.advanced import advanced_search as _adv
        hits = _adv(
            repository, title=title, body=body, author=author, space=space,
            tags=tags, categories=categories, date_from=date_from, date_to=date_to,
            k=k, offset=offset,
        )
        hits_list = [hit_to_dict(h, include_author=True) for h in hits]
        return json.dumps({"hits": hits_list}, ensure_ascii=False)

    @app.tool()
    def get_related(doc_id: str, k: int = 10) -> str:
        """Retrieve documents related to the given doc_id. If missing, raise ValueError with 'not_found'."""
        try:
            hits = silver_retriever.get_related(doc_id, k)
        except ValueError as e:
            if "not_found" in str(e).lower():
                raise ValueError("not_found: Document not found")
            raise e

        hits_list = [hit_to_dict(hit) for hit in hits]
        return json.dumps(hits_list, ensure_ascii=False)

    @app.tool()
    def get_document(doc_id: str, compress: bool = False, max_tokens: int | None = None) -> str:
        """Retrieve the full Document by doc_id. compress/max_tokens로 본문 출력 압축(기본 OFF)."""
        doc = repository.get(doc_id)
        if doc is None:
            raise ValueError("not_found: Document not found")

        # 출력 압축(옵트인): body_markdown만 규칙 기반 압축(무손실 기본)
        if compress or max_tokens is not None:
            from geryon.search.compress import compress as _compress
            doc.body_markdown = _compress(doc.body_markdown, max_tokens=max_tokens)

        # Handle both Pydantic v1/v2 json serialization safely
        if hasattr(doc, "model_dump_json"):
            return doc.model_dump_json()
        else:
            return doc.json()

    @app.tool()
    def browse(
        source: str | None = None,
        space: str | None = None,
        prefix: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Retrieve a listing of document titles and their doc_ids with filters."""
        conn = repository.get_connection()
        cursor = conn.cursor()

        query = "SELECT title, doc_id, source, space_or_repo FROM documents WHERE 1=1"
        params: list[Any] = []

        if source is not None:
            query += " AND LOWER(source) = ?"
            params.append(source.lower())

        if space is not None:
            query += " AND space_or_repo = ?"
            params.append(space)

        if prefix is not None:
            query += " AND title LIKE ?"
            params.append(prefix + "%")

        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(query, params)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            results.append(
                {
                    "title": row[0],
                    "doc_id": row[1],
                    "source": row[2],
                    "space_or_repo": row[3],
                }
            )

        return json.dumps(results, ensure_ascii=False)

    @app.tool()
    def list_sources() -> str:
        """Aggregate document counts across all sources and spaces/repos."""
        conn = repository.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT source, space_or_repo, count(*) as doc_count FROM documents GROUP BY source, space_or_repo"
        )
        rows = cursor.fetchall()

        results = []
        for row in rows:
            results.append(
                {
                    "source": row[0],
                    "space_or_repo": row[1],
                    "doc_count": row[2],
                }
            )

        return json.dumps(results, ensure_ascii=False)

    @app.tool()
    def reindex(source: str | None = None, full: bool = False) -> str:
        """Trigger the IngestionPipeline for a source."""
        if source is not None and source.lower() != "confluence":
            raise ValueError(f"Unsupported source: {source}")

        connector = ConfluenceConnector()
        pipeline = IngestionPipeline(repository=repository, vector_store=vector_store, tree_store=tree_store)
        stats = pipeline.run(connector, full_reindex=full)

        return json.dumps(stats, ensure_ascii=False)

    @app.resource("geryon://documents/{doc_id}")
    def get_document_resource(doc_id: str) -> str:
        """Retrieve document body markdown by doc_id as an MCP Resource. Raises ValueError with 'not_found' if missing."""
        doc = repository.get(doc_id)
        if doc is None:
            raise ValueError("not_found: Document not found")
        return doc.body_markdown

    @app.prompt()
    def document_qa(query: str, document_text: str) -> str:
        """Standard RAG prompt template for question answering over a document."""
        return (
            "You are a helpful assistant. Answer the user's question using the provided document text as context.\n"
            "If the answer cannot be found in the document, say 'I cannot find the answer in the document.'\n\n"
            f"Document Text:\n{document_text}\n\n"
            f"Question: {query}\n"
            "Answer:"
        )

    @app.prompt()
    def summarizer(document_text: str) -> str:
        """Standard RAG prompt template for summarizing a document."""
        return (
            "Please provide a concise and clear summary of the following document. "
            "Highlight the key points and main takeaways.\n\n"
            f"Document Text:\n{document_text}\n\n"
            "Summary:"
        )

    # Attach get_tool helper for testing compatibility
    def get_tool(name: str) -> Any:
        tool_obj = app._tool_manager.get_tool(name)
        if tool_obj is not None:
            return tool_obj.fn
        return None

    setattr(app, "get_tool", get_tool)

    return app
