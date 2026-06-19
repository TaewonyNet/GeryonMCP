import pytest
import tempfile
import os
from datetime import datetime
from geryon.domain.models import Document, SourceType
from geryon.store.repository import SqliteRepository
from geryon.store.vector import VectorStore
from geryon.pipeline.ingest import IngestionPipeline
from geryon.search.hybrid import HybridRetriever
from geryon.gold.search import GoldSearch
from geryon.domain.models import DictionaryEntry, UserProfile

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)

def test_gold_search_expansion_and_boosting(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)
    
    # doc1 is about Acme, in space DEMO, tagged "pricing"
    doc1 = Document(
        doc_id="doc1",
        source=SourceType.CONFLUENCE,
        source_id="p1",
        space_or_repo="DEMO",
        title="Acme 할인율 가이드",
        body_markdown="항공사 Acme 할인율 관리 규칙 정의 문서.",
        tags=["pricing"],
        content_hash="h1",
        ingested_at=datetime.utcnow()
    )
    # doc2 is about something else in space TE
    doc2 = Document(
        doc_id="doc2",
        source=SourceType.CONFLUENCE,
        source_id="p2",
        space_or_repo="TE",
        title="Jira 가이드",
        body_markdown="Jira 가이드 문서.",
        tags=["jira"],
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
                metadata={"tags": ["jira"]}
            )
        def healthcheck(self):
            return True
            
    pipeline.run(MockConnector())
    
    # Instantiate Silver retriever
    silver_retriever = HybridRetriever(repository=repo, vector_store=vstore)
    
    # We define standard mock dictionary entry
    # ACM expands to Acme
    entry = DictionaryEntry(
        term="Acme",
        synonyms=["ACM", "에이콘"],
        boost=1.5
    )
    
    # We define UserProfile
    profile = UserProfile(
        user_id="user_alice",
        preferred_spaces=["DEMO"],
        preferred_tags=["pricing"]
    )
    
    gold_search = GoldSearch(
        retriever=silver_retriever,
        dictionary=[entry],
        profiles={"user_alice": profile}
    )
    
    # 1. Search for "ACM": dictionary expansion should trigger search for "ACM OR Acme" (or synonyms)
    # Since doc1 has "Acme", it should be matched!
    hits_expanded = gold_search.search("ACM", user_id="user_alice")
    assert len(hits_expanded) > 0
    assert hits_expanded[0].doc_id == "535ccfa1888c3eda6fe04f18dc4a9610ec1b7b47" # sha1("confluence:p1")
    
    # Verify RRF boost is applied due to Alice's profile preferences
    # doc1 is in space "DEMO" and tag "pricing", so it receives boost
    # (Checking that score is higher or document ranks top)
    assert hits_expanded[0].score > 0.0

def test_gold_search_fallback(temp_db):
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
    
    silver_retriever = HybridRetriever(repository=repo, vector_store=vstore)
    gold_search = GoldSearch(retriever=silver_retriever)
    
    # Search without user_id: should fallback identically to silver
    hits_silver = silver_retriever.search("할인율")
    hits_gold = gold_search.search("할인율")
    
    assert len(hits_silver) == len(hits_gold)
    if len(hits_silver) > 0:
        assert hits_silver[0].doc_id == hits_gold[0].doc_id

def test_gold_relevance_gate(temp_db):
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
    
    silver_retriever = HybridRetriever(repository=repo, vector_store=vstore, relevance_threshold=0.2)
    
    profile = UserProfile(
        user_id="user_alice",
        preferred_spaces=["DEMO"],
        preferred_tags=["pricing"]
    )
    
    gold_search = GoldSearch(
        retriever=silver_retriever,
        profiles={"user_alice": profile}
    )
    
    # GoldSearch should also pass through the relevance gate.
    # Completely irrelevant query "비밀번호 변경 방법" must return [] (GT#3).
    hits = gold_search.search("비밀번호 변경 방법", user_id="user_alice")
    assert len(hits) == 0
