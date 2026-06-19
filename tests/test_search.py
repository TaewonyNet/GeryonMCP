import pytest
import tempfile
import os
from datetime import datetime
from geryon.domain.models import Document, SourceType, SearchFilter
from geryon.store.repository import SqliteRepository
from geryon.search.keyword import KeywordRetriever

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)

def test_keyword_retriever_search(temp_db):
    repo = SqliteRepository(temp_db)
    retriever = KeywordRetriever(repository=repo)
    
    # Insert 3 documents
    doc1 = Document(
        doc_id="doc1",
        source=SourceType.CONFLUENCE,
        source_id="p1",
        space_or_repo="DEMO",
        title="최대 할인율 개발 정책",
        body_markdown="할인율을 계산할 때는 최댓값 50% 제한 규칙을 반드시 준수해야 한다.",
        content_hash="h1",
        ingested_at=datetime.utcnow()
    )
    doc2 = Document(
        doc_id="doc2",
        source=SourceType.CONFLUENCE,
        source_id="p2",
        space_or_repo="TE",
        title="Jira 연동 가이드",
        body_markdown="Jira 이슈의 할인율 필드를 싱크하고 업데이트하는 절차 정의.",
        tags=["jira", "sync"],
        content_hash="h2",
        ingested_at=datetime.utcnow()
    )
    doc3 = Document(
        doc_id="doc3",
        source=SourceType.WEB,
        source_id="p3",
        space_or_repo="external",
        title="할인율 계산 라이브러리",
        body_markdown="외부에서 할인율을 계산하기 위한 파이썬 모듈 사용법.",
        tags=["search", "python"],
        content_hash="h3",
        ingested_at=datetime.utcnow()
    )
    
    repo.upsert(doc1)
    repo.upsert(doc2)
    repo.upsert(doc3)
    
    # 1. Search for '할인율': Should return all 3 documents containing '할인율'
    hits = retriever.search("할인율")
    assert len(hits) == 3
    assert any(h.doc_id == "doc1" for h in hits)
    assert any(h.doc_id == "doc2" for h in hits)
    assert any(h.doc_id == "doc3" for h in hits)
    
    # Score checks: The hits should have normalized score (0.0 to 1.0)
    for h in hits:
        assert 0.0 <= h.score <= 1.0
        assert h.snippet != ""
    
    # 2. Search for '정책': Should only return doc1
    hits_policy = retriever.search("정책")
    assert len(hits_policy) == 1
    assert hits_policy[0].doc_id == "doc1"
    
    # 3. No match query
    hits_none = retriever.search("비밀번호")
    assert len(hits_none) == 0

def test_keyword_retriever_filters(temp_db):
    repo = SqliteRepository(temp_db)
    retriever = KeywordRetriever(repository=repo)
    
    doc1 = Document(
        doc_id="doc1",
        source=SourceType.CONFLUENCE,
        source_id="p1",
        space_or_repo="DEMO",
        title="최대 할인율 개발 정책",
        body_markdown="할인율을 계산할 때는 최댓값 50% 제한 규칙을 반드시 준수해야 한다.",
        tags=["pricing"],
        content_hash="h1",
        ingested_at=datetime.utcnow()
    )
    doc2 = Document(
        doc_id="doc2",
        source=SourceType.CONFLUENCE,
        source_id="p2",
        space_or_repo="TE",
        title="Jira 연동 가이드",
        body_markdown="Jira 이슈의 할인율 필드를 싱크하고 업데이트하는 절차 정의.",
        tags=["jira", "sync"],
        content_hash="h2",
        ingested_at=datetime.utcnow()
    )
    
    repo.upsert(doc1)
    repo.upsert(doc2)
    
    # 1. Filter by tag 'jira': should return doc2 only
    filter1 = SearchFilter(tags=["jira"])
    hits1 = retriever.search("할인율", filters=filter1)
    assert len(hits1) == 1
    assert hits1[0].doc_id == "doc2"
    
    # 2. Filter by space 'DEMO': should return doc1 only
    filter2 = SearchFilter(spaces_or_repos=["DEMO"])
    hits2 = retriever.search("할인율", filters=filter2)
    assert len(hits2) == 1
    assert hits2[0].doc_id == "doc1"
    
    # 3. Filter by source 'WEB': should return nothing
    filter3 = SearchFilter(sources=[SourceType.WEB])
    hits3 = retriever.search("할인율", filters=filter3)
    assert len(hits3) == 0

def test_keyword_retriever_pagination_and_filters(temp_db):
    repo = SqliteRepository(temp_db)
    retriever = KeywordRetriever(repository=repo)
    
    # GIVEN: 3 documents with different authors, created_at, and updated_at
    doc1 = Document(
        doc_id="doc1",
        source=SourceType.CONFLUENCE,
        source_id="p1",
        space_or_repo="DEMO",
        title="최대 할인율 개발 정책",
        body_markdown="할인율을 계산할 때는 최댓값 50% 제한 규칙을 반드시 준수해야 한다.",
        author="John Doe",
        created_at=datetime.fromisoformat("2026-05-01T10:00:00"),
        updated_at=datetime.fromisoformat("2026-05-01T12:00:00"),
        content_hash="h1",
        ingested_at=datetime.utcnow()
    )
    doc2 = Document(
        doc_id="doc2",
        source=SourceType.CONFLUENCE,
        source_id="p2",
        space_or_repo="TE",
        title="Jira 연동 가이드",
        body_markdown="Jira 이슈의 할인율 필드를 싱크하고 업데이트하는 절차 정의.",
        author="Alice Smith",
        created_at=datetime.fromisoformat("2026-05-15T10:00:00"),
        updated_at=datetime.fromisoformat("2026-05-15T12:00:00"),
        content_hash="h2",
        ingested_at=datetime.utcnow()
    )
    doc3 = Document(
        doc_id="doc3",
        source=SourceType.WEB,
        source_id="p3",
        space_or_repo="external",
        title="할인율 계산 라이브러리",
        body_markdown="외부에서 할인율을 계산하기 위한 파이썬 모듈 사용법.",
        author="Bob Johnson",
        created_at=datetime.fromisoformat("2026-05-20T10:00:00"),
        updated_at=datetime.fromisoformat("2026-05-20T12:00:00"),
        content_hash="h3",
        ingested_at=datetime.utcnow()
    )
    
    repo.upsert(doc1)
    repo.upsert(doc2)
    repo.upsert(doc3)
    
    # WHEN: testing pagination (k=1, offset=0/1/2) sorted by bm25/FTS order (or ID for like fallback)
    # Since '할인율' fallback (LIKE) queries:
    hits_p1 = retriever.search("할인율", k=1, offset=0)
    hits_p2 = retriever.search("할인율", k=1, offset=1)
    hits_p3 = retriever.search("할인율", k=1, offset=2)
    
    # THEN: we should retrieve 1 document per page, and they must be different
    assert len(hits_p1) == 1
    assert len(hits_p2) == 1
    assert len(hits_p3) == 1
    assert hits_p1[0].doc_id != hits_p2[0].doc_id
    assert hits_p2[0].doc_id != hits_p3[0].doc_id
    
    # WHEN: filtering by author "Alice Smith"
    filter_author = SearchFilter(authors=["Alice Smith"])
    hits_author = retriever.search("할인율", filters=filter_author)
    # THEN: should return only doc2
    assert len(hits_author) == 1
    assert hits_author[0].doc_id == "doc2"
    
    # WHEN: filtering by date_from
    filter_date_from = SearchFilter(date_from=datetime.fromisoformat("2026-05-10T00:00:00"))
    hits_date_from = retriever.search("할인율", filters=filter_date_from)
    # THEN: should return doc2 and doc3
    assert len(hits_date_from) == 2
    assert any(h.doc_id == "doc2" for h in hits_date_from)
    assert any(h.doc_id == "doc3" for h in hits_date_from)
    
    # WHEN: filtering by date_to
    filter_date_to = SearchFilter(date_to=datetime.fromisoformat("2026-05-16T00:00:00"))
    hits_date_to = retriever.search("할인율", filters=filter_date_to)
    # THEN: should return doc1 and doc2
    assert len(hits_date_to) == 2
    assert any(h.doc_id == "doc1" for h in hits_date_to)
    assert any(h.doc_id == "doc2" for h in hits_date_to)


def test_aggregate_max_by_doc():
    """같은 문서의 여러 청크는 최고 점수로 집계되고, 점수 내림차순 정렬."""
    from geryon.search.hybrid import _aggregate_max_by_doc
    raw = [
        {"doc_id": "D", "chunk_id": "D#0", "text": "a", "score": 0.4},
        {"doc_id": "D", "chunk_id": "D#1", "text": "b", "score": 0.9},
        {"doc_id": "E", "chunk_id": "E#0", "text": "c", "score": 0.6},
    ]
    out = _aggregate_max_by_doc(raw)
    assert [h["doc_id"] for h in out] == ["D", "E"]  # D(0.9) > E(0.6)
    assert out[0]["score"] == 0.9                     # max 청크 채택(first-only 아님)
