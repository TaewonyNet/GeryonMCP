"""검색 조립의 단일 진입점 (공유 노드).

production(MCP `search`)·벤치·테스트가 **모두 이 팩토리**로 동일한 검색기를
얻는다. 조립을 여러 곳에서 손으로 복제하면 drift가 생긴다(0.14.9 사고:
벤치가 GoldSearch(동의어 확장) 층을 빠뜨리고 HybridRetriever를 직접 호출).

production 검색 = GoldSearch(동의어 확장 + 개인화) → HybridRetriever(FTS+벡터 RRF).
"""
from pathlib import Path

from geryon.config import DB_PATH
from geryon.store.repository import SqliteRepository
from geryon.store.vector import VectorStore
from geryon.search.hybrid import HybridRetriever
from geryon.gold.search import GoldSearch


def build_searcher(
    db_path: str | Path | None = None,
    repository: SqliteRepository | None = None,
    vector_store: VectorStore | None = None,
    db_paths: list[str | Path] | None = None,
) -> GoldSearch:
    """production 검색기(GoldSearch→HybridRetriever)를 조립해 반환한다.

    - `db_paths`(여러 DB): federation[25] — 각 DB BM25 후보를 모아 통합 rerank.
    - 그 외: 단일 DB(`repository`/`vector_store` 재사용 또는 `db_path`/기본 `DB_PATH`).
    사용자 사전·개인화(GoldSearch 층)는 DB 무관하게 그대로 동작한다.
    """
    if db_paths:
        repos = [SqliteRepository(Path(p)) for p in db_paths]
        vss = [VectorStore(Path(p)) for p in db_paths]
        return GoldSearch(retriever=HybridRetriever(repositories=repos, vector_stores=vss))
    resolved = Path(db_path) if db_path is not None else Path(DB_PATH)
    repo = repository if repository is not None else SqliteRepository(resolved)
    vs = vector_store if vector_store is not None else VectorStore(resolved)
    return GoldSearch(retriever=HybridRetriever(repository=repo, vector_store=vs))
