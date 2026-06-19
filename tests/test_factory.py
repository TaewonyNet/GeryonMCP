"""검색 팩토리 계약 테스트 (공유 노드).

production 검색 경로가 GoldSearch(동의어 확장) → HybridRetriever 조립임을 고정한다.
이 테스트가 깨지면 server·bench·tests 가 서로 다른 검색 경로를 탈 위험(0.14.9 사고).
"""
from geryon.search.factory import build_searcher
from geryon.gold.search import GoldSearch
from geryon.search.hybrid import HybridRetriever


def test_build_searcher_assembles_gold_over_hybrid(tmp_path):
    db = tmp_path / "g.db"
    searcher = build_searcher(db)
    # 최상위는 GoldSearch(동의어 확장+개인화 층)여야 한다 — Hybrid 직접이 아님.
    assert isinstance(searcher, GoldSearch)
    # 그 안의 Silver 검색기는 HybridRetriever.
    assert isinstance(searcher.retriever, HybridRetriever)


def test_build_searcher_reuses_given_stores(tmp_path):
    from geryon.store.repository import SqliteRepository
    from geryon.store.vector import VectorStore

    db = tmp_path / "g.db"
    repo = SqliteRepository(db)
    vs = VectorStore(db)
    searcher = build_searcher(repository=repo, vector_store=vs)
    # 주입한 스토어를 그대로 재사용해야 한다(이중 생성 방지).
    assert searcher.retriever.repository is repo
    assert searcher.retriever.vector_store is vs


def test_server_uses_factory_path():
    """MCP 서버가 팩토리 경로(GoldSearch)를 쓰는지 정적 확인."""
    import inspect
    from geryon.mcp import server

    src = inspect.getsource(server.create_mcp_server)
    assert "build_searcher" in src, "server는 build_searcher로 검색기를 조립해야 한다"
