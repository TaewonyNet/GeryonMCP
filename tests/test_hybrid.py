import pytest
import tempfile
import os
from datetime import datetime
from geryon.domain.models import Document, SourceType, SearchFilter
from geryon.store.repository import SqliteRepository
from geryon.store.vector import VectorStore
from geryon.pipeline.ingest import IngestionPipeline
from geryon.search.hybrid import HybridRetriever

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)

def test_hybrid_retriever_rrf(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)
    
    # 1. Ingest documents to populate FTS and Vector indices
    # doc1 is strong on keyword "할인율", body also has "제한"
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
    # doc2 is strong on semantic term "이슈 연동"
    doc2 = Document(
        doc_id="doc2",
        source=SourceType.CONFLUENCE,
        source_id="p2",
        space_or_repo="TE",
        title="Jira 연동 가이드",
        body_markdown="Jira 이슈의 필드를 싱크하고 업데이트하는 절차 정의.",
        content_hash="h2",
        ingested_at=datetime.utcnow()
    )
    
    class MockConnector:
        source_type = SourceType.CONFLUENCE
        def iter_raw(self, since=None):
            yield RawRecord(
                source=doc1.source,
                source_id=doc1.source_id,
                raw_body=doc1.body_markdown,
                raw_format="html",
                title=doc1.title,
                url="http://example.com/p1",
                space_or_repo=doc1.space_or_repo
            )
            yield RawRecord(
                source=doc2.source,
                source_id=doc2.source_id,
                raw_body=doc2.body_markdown,
                raw_format="html",
                title=doc2.title,
                url="http://example.com/p2",
                space_or_repo=doc2.space_or_repo
            )
        def healthcheck(self):
            return True
            
    from geryon.domain.models import RawRecord
    pipeline.run(MockConnector())
    
    # 2. Search semantically for "지라와 연동하여 동기화"
    # FTS5 Trigram MATCH will fail or rank poorly for "지라와 연동하여 동기화",
    # but semantic Vector Store will easily match "Jira 연동 가이드"!
    # Hybrid search should return doc2 as the top match!
    retriever = HybridRetriever(repository=repo, vector_store=vstore, relevance_threshold=0.9)
    hits = retriever.search("지라와 연동하여 동기화", k=5)
    
    assert len(hits) > 0
    # doc2 (Jira 연동 가이드) should be first because of semantic similarity
    assert hits[0].doc_id == "e3ccbc2ce5a563d5101ea7a9e43d619e6cc2aeeb" # sha1("confluence:p2")
    
    # Search for "할인율" - should return both
    hits_discount = retriever.search("할인율")
    assert len(hits_discount) == 2

def test_hybrid_retriever_filters(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)
    
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
        body_markdown="Jira 이슈의 필드를 싱크하고 업데이트하는 절차 정의.",
        tags=["sync"],
        content_hash="h2",
        ingested_at=datetime.utcnow()
    )
    
    from geryon.domain.models import RawRecord
    class MockConnector:
        source_type = SourceType.CONFLUENCE
        def iter_raw(self, since=None):
            yield RawRecord(
                source=doc1.source,
                source_id=doc1.source_id,
                raw_body=doc1.body_markdown,
                raw_format="html",
                title=doc1.title,
                url="http://example.com/p1",
                space_or_repo=doc1.space_or_repo,
                metadata={"tags": ["pricing"]}
            )
            yield RawRecord(
                source=doc2.source,
                source_id=doc2.source_id,
                raw_body=doc2.body_markdown,
                raw_format="html",
                title=doc2.title,
                url="http://example.com/p2",
                space_or_repo=doc2.space_or_repo,
                metadata={"tags": ["sync"]}
            )
        def healthcheck(self):
            return True
            
    pipeline.run(MockConnector())
    
    retriever = HybridRetriever(repository=repo, vector_store=vstore, relevance_threshold=0.9)
    
    # Filter by space "DEMO" -> should only return doc1
    filter1 = SearchFilter(spaces_or_repos=["DEMO"])
    hits1 = retriever.search("할인율", filters=filter1)
    assert len(hits1) == 1
    assert hits1[0].doc_id == "535ccfa1888c3eda6fe04f18dc4a9610ec1b7b47" # sha1("confluence:p1")

def test_hybrid_relevance_gate(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)
    
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
    
    from geryon.domain.models import RawRecord
    class MockConnector:
        source_type = SourceType.CONFLUENCE
        def iter_raw(self, since=None):
            yield RawRecord(
                source=doc1.source,
                source_id=doc1.source_id,
                raw_body=doc1.body_markdown,
                raw_format="html",
                title=doc1.title,
                url="http://example.com/p1",
                space_or_repo=doc1.space_or_repo
            )
        def healthcheck(self):
            return True
            
    pipeline.run(MockConnector())
    
    retriever = HybridRetriever(repository=repo, vector_store=vstore, relevance_threshold=0.2)
    
    # "비밀번호 변경 방법" is completely irrelevant to "할인율".
    # Keyword search will yield 0 hits.
    # Semantic search might return doc1 as the closest, but its cosine distance will be > 0.6 (or > 0.2 here).
    # The relevance gate should exclude doc1 and return [] (GT#3).
    hits = retriever.search("비밀번호 변경 방법", k=5)
    assert len(hits) == 0
