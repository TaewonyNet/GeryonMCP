import pytest
import tempfile
import os
from datetime import datetime, timedelta, timezone
from geryon.domain.models import SourceType, RawRecord
from geryon.store.repository import SqliteRepository
from geryon.store.vector import VectorStore
from geryon.pipeline.ingest import IngestionPipeline, PruneSafetyViolation
from geryon.connectors.base import Connector
from geryon.search.hybrid import HybridRetriever

class MockConnector(Connector):
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

def test_gt13_incremental_sync_and_deletion(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)

    doc_1 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="p1",
        raw_body="<h1>최대 할인율 개발 정책</h1><p>할인율을 계산할 때는 최댓값 50% 제한 규칙을 반드시 준수해야 한다.</p>",
        raw_format="html",
        title="최대 할인율 개발 정책",
        url="http://example.com/p1"
    )
    doc_2 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="p2",
        raw_body="<h1>Jira 연동 가이드</h1><p>Jira 이슈의 필드를 싱크하고 업데이트하는 절차 정의.</p>",
        raw_format="html",
        title="Jira 연동 가이드",
        url="http://example.com/p2"
    )
    doc_3 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="p3",
        raw_body="<h1>임시 보관 문서</h1><p>이 문서는 조만간 삭제될 예정입니다.</p>",
        raw_format="html",
        title="임시 보관 문서",
        url="http://example.com/p3"
    )

    connector = MockConnector([doc_1, doc_2, doc_3])
    
    # 1. First run: Ingest 3 documents
    stats_1 = pipeline.run(connector, prune=True)
    assert stats_1["indexed"] == 3
    assert stats_1["skipped"] == 0
    assert stats_1["deleted"] == 0
    assert stats_1["errors"] == 0

    # Verify all 3 are stored
    assert repo.get("535ccfa1888c3eda6fe04f18dc4a9610ec1b7b47") is not None  # doc_1
    assert repo.get("e3ccbc2ce5a563d5101ea7a9e43d619e6cc2aeeb") is not None  # doc_2
    assert repo.get("088aefd761a863a8048784091aa3e56396209d8d") is not None  # doc_3

    # 2. Modify doc_2's body and remove doc_3 from the connector
    doc_2_modified = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="p2",
        raw_body="<h1>Jira 연동 가이드</h1><p>수정된 본문: Jira 이슈의 필드를 싱크하고 동기화하는 절차 업데이트.</p>",
        raw_format="html",
        title="Jira 연동 가이드",
        url="http://example.com/p2"
    )

    connector_2 = MockConnector([doc_1, doc_2_modified])

    # 3. Second run: doc_1 skipped, doc_2 updated, doc_3 deleted
    stats_2 = pipeline.run(connector_2, prune=True)
    assert stats_2["indexed"] == 1
    assert stats_2["skipped"] == 1
    assert stats_2["deleted"] == 1
    assert stats_2["errors"] == 0

    # 4. Verify deletions propagated completely
    assert repo.get("088aefd761a863a8048784091aa3e56396209d8d") is None  # doc_3 is gone

    # Verify chunks and vector embeddings of doc_3 are also deleted
    conn = repo.get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM chunks WHERE doc_id = ?;", ("088aefd761a863a8048784091aa3e56396209d8d",))
    assert cursor.fetchone()[0] == 0

    cursor.execute("SELECT count(*) FROM chunk_embeddings WHERE chunk_id LIKE '088aefd761a863a8048784091aa3e56396209d8d%';")
    assert cursor.fetchone()[0] == 0

    # Verify doc_2 has updated body_markdown
    updated_doc_2 = repo.get("e3ccbc2ce5a563d5101ea7a9e43d619e6cc2aeeb")
    assert updated_doc_2 is not None
    assert "수정된 본문" in updated_doc_2.body_markdown

def test_gt13_deletion_safety_gates(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)

    # Ingest 6 mock documents to exceed previous doc count limit of 5
    records = []
    for i in range(6):
        records.append(
            RawRecord(
                source=SourceType.CONFLUENCE,
                source_id=f"page_{i}",
                raw_body=f"<p>Document {i} content</p>",
                raw_format="html",
                title=f"Document {i}"
            )
        )

    connector = MockConnector(records)
    stats = pipeline.run(connector, prune=True)
    assert stats["indexed"] == 6

    # 1. Safety Gate Case A: 0 documents returned (source crash) -> Must raise PruneSafetyViolation
    empty_connector = MockConnector([])
    with pytest.raises(PruneSafetyViolation) as exc_info:
        pipeline.run(empty_connector, prune=True)
    assert "Safety Gate Violated" in str(exc_info.value)

    # 2. Safety Gate Case B: Critical drop > 50% (6 docs -> 2 docs) -> Must raise PruneSafetyViolation
    dropped_connector = MockConnector(records[:2])
    with pytest.raises(PruneSafetyViolation) as exc_info2:
        pipeline.run(dropped_connector, prune=True)
    assert "Safety Gate Violated" in str(exc_info2.value)

    # 3. Bypass Safety Gate via full_reindex=True
    stats_bypass = pipeline.run(dropped_connector, full_reindex=True, prune=True)
    assert stats_bypass["deleted"] == 4

def test_gt13_recency_score_decay(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # doc_new is created today
    doc_new = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="new_doc",
        raw_body="<h1>할인율 정책 공지</h1><p>최대 할인율 한도는 50%를 적용합니다.</p>",
        raw_format="html",
        title="할인율 정책 공지",
        metadata={"created_at": now.isoformat(), "updated_at": now.isoformat()}
    )

    # doc_old is created 30 days ago
    old_time = now - timedelta(days=30)
    doc_old = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="old_doc",
        raw_body="<h1>할인율 정책 공지</h1><p>최대 할인율 한도는 50%를 적용합니다.</p>",
        raw_format="html",
        title="할인율 정책 공지",
        metadata={"created_at": old_time.isoformat(), "updated_at": old_time.isoformat()}
    )

    connector = MockConnector([doc_new, doc_old])
    _ = pipeline.run(connector, prune=True)

    retriever = HybridRetriever(repository=repo, vector_store=vstore)
    hits = retriever.search("할인율 정책 공지", k=5)

    assert len(hits) == 2
    # doc_new must have higher score and rank first due to recency boost
    assert hits[0].doc_id == "58e630c302f5d35d1d5ff596c9111dc14054dfb5"  # new_doc
    assert hits[1].doc_id == "b395e7ab17fc027fd10ecc4f3e4e2cac7797c729"  # old_doc
    assert hits[0].score > hits[1].score
