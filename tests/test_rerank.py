"""rerank 경로 단위 검증 (14 §7). 실제 모델(1.1GB) 로드 없이 가짜 reranker로 계약 확인."""
import os
import tempfile
from datetime import datetime

import pytest

from geryon.domain.models import Document, SourceType
from geryon.store.repository import SqliteRepository
from geryon.store.vector import VectorStore
from geryon.search.hybrid import HybridRetriever
import geryon.search.hybrid as hybrid_mod
import geryon.search.rerank as rerank_mod


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _doc(i, title):
    return Document(
        doc_id=f"d{i}", source=SourceType.CONFLUENCE, source_id=f"p{i}",
        space_or_repo="S", title=title, body_markdown=f"{title} 본문 내용",
        content_hash=f"h{i}", ingested_at=datetime.utcnow(),
    )


def _retriever(path):
    repo = SqliteRepository(path)
    vs = VectorStore(db_path=path)
    # rerank 경로는 키워드(FTS)만 필요 — upsert 시 트리거가 documents_fts 채움.
    for i, t in enumerate(["배포 가이드 문서", "배포 운영 런북", "배포 회의록"], 1):
        repo.upsert(_doc(i, t))
    return HybridRetriever(repository=repo, vector_store=vs)


def test_rerank_scores_empty_docs_returns_empty():
    assert rerank_mod.rerank_scores("q", []) == []


def test_get_reranker_caches_failure(monkeypatch):
    # 로드 실패가 캐시되어 None을 반환(→ 폴백)하는지.
    # QUANTIZE off로 고정해 캐시 키를 단순화(실모델 로드 방지).
    monkeypatch.setattr(rerank_mod, "RERANK_QUANTIZE", False)
    rerank_mod._RERANKER_CACHE.clear()
    rerank_mod._RERANKER_CACHE["broken-model"] = False
    assert rerank_mod.get_reranker("broken-model") is None


def test_rerank_search_orders_by_reranker(temp_db, monkeypatch):
    ret = _retriever(temp_db)
    # 가짜 reranker: 제목에 '런북' 있으면 최고점 → 그 문서가 1위여야 함
    def fake_scores(query, docs, model_name=None):
        return [10.0 if "런북" in d else 1.0 for d in docs]
    monkeypatch.setattr(hybrid_mod, "rerank_scores", fake_scores)

    hits = ret._rerank_search("배포", k=3, offset=0, filters=None)
    assert hits is not None and len(hits) >= 1
    assert hits[0].title == "배포 운영 런북"
    assert 0.0 <= hits[0].score <= 1.0  # 로짓→[0,1] 정규화


def test_rerank_search_falls_back_when_unavailable(temp_db, monkeypatch):
    ret = _retriever(temp_db)
    monkeypatch.setattr(hybrid_mod, "rerank_scores", lambda q, d, model_name=None: None)
    # reranker 불가 → None 반환(호출측이 하이브리드로 폴백)
    assert ret._rerank_search("배포", k=3, offset=0, filters=None) is None


def test_rerank_search_no_keyword_match_falls_back(temp_db, monkeypatch):
    ret = _retriever(temp_db)
    called = {"n": 0}
    def fake_scores(query, docs, model_name=None):
        called["n"] += 1
        return [1.0] * len(docs)
    monkeypatch.setattr(hybrid_mod, "rerank_scores", fake_scores)
    # 키워드 무매칭 → None(폴백), reranker 호출 안 함
    assert ret._rerank_search("존재하지않는단어xyz", k=3, offset=0, filters=None) is None
    assert called["n"] == 0
