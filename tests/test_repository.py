import pytest
from datetime import datetime
from geryon.domain.models import Document, SourceType, Attachment
# We will implement SqliteRepository inside geryon.store.repository
from geryon.store.repository import SqliteRepository
import tempfile
import os

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)

def test_repository_upsert_idempotency(temp_db):
    repo = SqliteRepository(temp_db)
    
    doc = Document(
        doc_id="doc1",
        source=SourceType.CONFLUENCE,
        source_id="12345",
        space_or_repo="DEMO",
        url="http://example.com/doc1",
        title="Geryon Search Engine",
        body_markdown="Geryon is a search engine based on SQLite.",
        summary="A search engine",
        tags=["search", "sqlite"],
        hierarchy=["DEMO", "search"],
        author="alice",
        created_at=datetime(2024, 7, 1, 0, 0, 0),
        updated_at=datetime(2024, 7, 2, 0, 0, 0),
        attachments=[
            Attachment(filename="file1.txt", media_type="text/plain", local_path="file1.txt")
        ],
        raw_meta={"version": 1},
        content_hash="hash1",
        ingested_at=datetime(2026, 5, 29, 9, 26, 0)
    )
    
    # 1. First upsert: Should insert
    inserted = repo.upsert(doc)
    assert inserted == "inserted"
    
    # Verify the document is inserted correctly
    fetched = repo.get("doc1")
    assert fetched is not None
    assert fetched.title == "Geryon Search Engine"
    assert fetched.content_hash == "hash1"
    assert len(fetched.attachments) == 1
    assert fetched.attachments[0].filename == "file1.txt"
    assert fetched.tags == ["search", "sqlite"]
    
    # 2. Second upsert (same content_hash): Should skip
    inserted2 = repo.upsert(doc)
    assert inserted2 == "skipped"

    # 3. Modify content_hash: Should update (덮어쓰기)
    doc_modified = doc.model_copy(update={
        "body_markdown": "Geryon is a fast search engine.",
        "content_hash": "hash2"
    })
    inserted3 = repo.upsert(doc_modified)
    assert inserted3 == "updated"
    
    # Verify updated content
    fetched3 = repo.get("doc1")
    assert fetched3.body_markdown == "Geryon is a fast search engine."
    assert fetched3.content_hash == "hash2"

def test_repository_fts_sync_and_query(temp_db):
    repo = SqliteRepository(temp_db)
    
    doc = Document(
        doc_id="doc2",
        source=SourceType.CONFLUENCE,
        source_id="54321",
        space_or_repo="DEMO",
        title="최대 할인율 개발 문서",
        body_markdown="할인율 산정 공식과 최댓값 규칙 정의",
        content_hash="hash_fts_1",
        ingested_at=datetime.utcnow()
    )
    
    repo.upsert(doc)
    
    # Verify we can find it via FTS5 trigram tokenization
    # '할인율' contains partial matches or trigrams '할인율'
    conn = repo.get_connection()
    cursor = conn.cursor()
    
    # Raw trigram query check
    cursor.execute("SELECT doc_id FROM documents_fts WHERE documents_fts MATCH '할인율'")
    results = cursor.fetchall()
    assert len(results) == 1
    assert results[0][0] == "doc2"
    
    # Check deletion also removes from FTS
    repo.delete("doc2")
    
    cursor.execute("SELECT doc_id FROM documents_fts WHERE documents_fts MATCH '할인율'")
    results_after = cursor.fetchall()
    assert len(results_after) == 0
