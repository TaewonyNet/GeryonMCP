import pytest
import tempfile
import os
import yaml
from geryon.domain.models import SourceType, DictionaryEntry, RawRecord
from geryon.store.repository import SqliteRepository
from geryon.store.vector import VectorStore
from geryon.pipeline.ingest import IngestionPipeline
from geryon.search.hybrid import HybridRetriever
from geryon.gold.search import GoldSearch

class MockConnector:
    def __init__(self, records: list[RawRecord]):
        self.source_type = SourceType.CONFLUENCE
        self.records = records

    def iter_raw(self, since=None):
        yield from self.records

    def healthcheck(self) -> bool:
        return True

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)

def test_gt15_personalized_dictionary_expansions(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)

    # Ingest 2 mock documents
    doc1 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="p1",
        raw_body="<h1>Acme 항공 예약</h1><p>Acme 항공 예약 시스템의 할인율 정책 가이드.</p>",
        raw_format="html",
        title="Acme 항공 예약",
        space_or_repo="DEMO",
        metadata={"tags": ["pricing"]}
    )
    doc2 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="p2",
        raw_body="<h1>Jira 연동</h1><p>Jira 이슈 동기화 및 릴리즈 규칙.</p>",
        raw_format="html",
        title="Jira 연동",
        space_or_repo="TE",
        metadata={"tags": ["sync"]}
    )

    pipeline.run(MockConnector([doc1, doc2]))

    silver_retriever = HybridRetriever(repository=repo, vector_store=vstore, relevance_threshold=0.9)

    # 1. Standard DictionaryEntry (ACM -> Acme)
    global_entry = DictionaryEntry(
        term="Acme",
        synonyms=["ACM", "에이콘"],
        boost=1.5
    )

    gold_search = GoldSearch(
        retriever=silver_retriever,
        dictionary=[global_entry],
        profiles={}
    )

    # Test 1.1: Anonymous search with dictionary expansion
    # Searching for "ACM" should trigger expansion to "ACM Acme" (or "Acme")
    # Even without a user_id or profile, doc1 must be matched!
    hits_anon = gold_search.search("ACM", user_id=None)
    assert len(hits_anon) > 0
    assert hits_anon[0].doc_id == "535ccfa1888c3eda6fe04f18dc4a9610ec1b7b47"  # doc1 (Acme)

    # Test 1.2: Multi-word query token-based matching
    # Query "항공 예약 ACM" -> should successfully match "ACM" and expand to include "Acme"
    hits_multi = gold_search.search("항공 예약 ACM", user_id=None)
    assert len(hits_multi) > 0
    assert hits_multi[0].doc_id == "535ccfa1888c3eda6fe04f18dc4a9610ec1b7b47"

def test_gt15_user_dictionary_overrides(temp_db, monkeypatch):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)

    doc1 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="p1",
        raw_body="<h1>Acme 할인율</h1><p>항공 할인율 정책.</p>",
        raw_format="html",
        title="Acme 할인율",
        space_or_repo="DEMO",
        metadata={"tags": ["pricing"]}
    )
    doc2 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="p2",
        raw_body="<h1>에이콘 요금제</h1><p>항공 요금제 관련 규칙.</p>",
        raw_format="html",
        title="에이콘 요금제",
        space_or_repo="TE",
        metadata={"tags": ["pricing"]}
    )

    pipeline.run(MockConnector([doc1, doc2]))

    silver_retriever = HybridRetriever(repository=repo, vector_store=vstore, relevance_threshold=0.9)

    # Global entry: term="Acme", synonyms=["ACM"]
    global_entry = DictionaryEntry(
        term="Acme",
        synonyms=["ACM"],
        boost=1.1
    )

    gold_search = GoldSearch(
        retriever=silver_retriever,
        dictionary=[global_entry],
        profiles={}
    )

    # Setup mock user-specific dictionary override
    # User dictionary has entry: term="Acme", synonyms=["에이콘"], boost=2.0
    user_dict_data = [
        {
            "term": "Acme",
            "synonyms": ["에이콘"],
            "boost": 2.0
        }
    ]

    with tempfile.TemporaryDirectory() as tmp_home:
        # Override Path.home() using monkeypatch to point to temp_home
        from pathlib import Path
        monkeypatch.setattr(Path, "home", lambda: Path(tmp_home))
        
        user_dict_dir = Path(tmp_home) / ".geryon" / "gold" / "users"
        user_dict_dir.mkdir(parents=True, exist_ok=True)
        
        with open(user_dict_dir / "user_bob.dictionary.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(user_dict_data, f)

        # Alice has no personal dict, Bob has a personal dict
        # Bob searches for "에이콘" -> should trigger personalized override dictionary:
        # doc2 (에이콘 요금제) should match and receive boost 2.0.
        # Global dictionary has no "에이콘" synonym for Acme, but Bob's personal dict has it!
        hits_bob = gold_search.search("에이콘", user_id="user_bob")
        assert len(hits_bob) > 0
        assert any(h.doc_id == "e3ccbc2ce5a563d5101ea7a9e43d619e6cc2aeeb" for h in hits_bob)  # doc2
