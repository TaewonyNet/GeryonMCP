import pytest
import tempfile
import os
from geryon.domain.models import SourceType, RawRecord
from geryon.store.repository import SqliteRepository
from geryon.store.vector import VectorStore
from geryon.embed.embedder import LocalEmbedder
from geryon.pipeline.ingest import IngestionPipeline

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)

def test_vector_store_cosine_similarity(temp_db):
    embedder = LocalEmbedder()
    vstore = VectorStore(db_path=temp_db)
    
    # 1. Test embedding generation
    passages = ["Geryon is an integrates search engine.", "We use fastembed for local embeddings."]
    embeddings = embedder.embed_passages(passages)
    assert len(embeddings) == 2
    assert len(embeddings[0]) == 384  # multilingual-e5-small is 384d
    
    # 2. Insert chunks and vectors into VectorStore
    # We simulate chunks for doc1
    vstore.save_chunks("doc1", [
        ("doc1#0", 0, "Geryon is an integrates search engine.", embeddings[0]),
        ("doc1#1", 1, "We use fastembed for local embeddings.", embeddings[1])
    ])
    
    # 3. Semantic search for "fastembed"
    query_vector = embedder.embed_query("fastembed")
    hits = vstore.search_vector(query_vector, k=5)
    
    assert len(hits) > 0
    # The chunk containing "fastembed" should rank first
    assert hits[0]["chunk_id"] == "doc1#1"
    assert "fastembed" in hits[0]["text"]

def test_pipeline_vector_sync(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)
    
    record = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="page_1",
        raw_body="할인율 산정 정책 공식. 최대 할인율은 50%를 초과할 수 없다. 정책의 예외 규정은 다음과 같다.",
        raw_format="html",
        title="할인율 정책",
        url="http://example.com/p1"
    )
    
    # Ingestion pipeline runs: should automatically trigger chunking and vector storage
    class MockConnector:
        source_type = SourceType.CONFLUENCE
        def iter_raw(self, since=None):
            yield record
        def healthcheck(self):
            return True
            
    stats = pipeline.run(MockConnector())
    assert stats["indexed"] == 1
    
    # Check that chunks and vectors are saved
    conn = repo.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT chunk_id, text FROM chunks WHERE doc_id = ?", ("6c2e6f14b1925466401f6578a8de5e14e3ad34cd",))
    rows = cursor.fetchall()
    assert len(rows) > 0
    assert "할인율" in rows[0][1]
    
    # Check that vector is in sqlite-vec table
    # We query vec_chunks
    cursor.execute("SELECT chunk_id FROM chunk_embeddings LIMIT 1")
    vec_rows = cursor.fetchall()
    assert len(vec_rows) > 0
    assert vec_rows[0][0] == rows[0][0]
