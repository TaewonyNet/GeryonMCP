import pytest
import tempfile
import os
from geryon.domain.models import SourceType, RawRecord
from geryon.connectors.base import Connector
from geryon.store.repository import SqliteRepository
from geryon.pipeline.ingest import IngestionPipeline

class MockConnector(Connector):
    def __init__(self, records):
        self.source_type = SourceType.CONFLUENCE
        self.records = records
        self._health = True

    def iter_raw(self, since=None):
        yield from self.records

    def healthcheck(self) -> bool:
        return self._health

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)

def test_pipeline_confluence_ingest(temp_db):
    repo = SqliteRepository(temp_db)
    
    # Create 2 mock records
    records = [
        RawRecord(
            source=SourceType.CONFLUENCE,
            source_id="page_1",
            raw_body="<h1>Page 1 Title</h1><p>Body 1</p>",
            raw_format="html",
            title="Page 1 Title",
            url="http://example.com/p1",
            space_or_repo="DEMO"
        ),
        RawRecord(
            source=SourceType.CONFLUENCE,
            source_id="page_2",
            raw_body="<h1>Page 2 Title</h1><p>Body 2</p>",
            raw_format="html",
            title="Page 2 Title",
            url="http://example.com/p2",
            space_or_repo="DEMO"
        )
    ]
    
    connector = MockConnector(records)
    pipeline = IngestionPipeline(repository=repo)
    
    # 1. First run: Should index all 2 records
    stats1 = pipeline.run(connector)
    assert stats1["indexed"] == 2
    assert stats1["skipped"] == 0
    assert stats1["errors"] == 0
    
    # Verify DB contains the records
    doc1 = repo.get("6c2e6f14b1925466401f6578a8de5e14e3ad34cd") # sha1("confluence:page_1")
    assert doc1 is not None
    assert doc1.title == "Page 1 Title"
    
    # 2. Second run: Should skip all 2 records (idempotency check)
    stats2 = pipeline.run(connector)
    assert stats2["indexed"] == 0
    assert stats2["skipped"] == 2
    assert stats2["errors"] == 0

def test_pipeline_error_isolation(temp_db):
    repo = SqliteRepository(temp_db)
    
    # Create 3 mock records, second one is malformed (e.g. raw_body is None, causing normalization exception)
    records = [
        RawRecord(
            source=SourceType.CONFLUENCE,
            source_id="good_1",
            raw_body="<p>Good 1</p>",
            raw_format="html",
            title="Good 1"
        ),
        RawRecord(
            source=SourceType.CONFLUENCE,
            source_id="bad_2",
            raw_body=None, # type: ignore # This will cause an exception during BeautifulSoup parsing or mapping
            raw_format="html",
            title="Bad 2"
        ),
        RawRecord(
            source=SourceType.CONFLUENCE,
            source_id="good_3",
            raw_body="<p>Good 3</p>",
            raw_format="html",
            title="Good 3"
        )
    ]
    
    connector = MockConnector(records)
    pipeline = IngestionPipeline(repository=repo)
    
    stats = pipeline.run(connector)
    
    # Should index 2 and fail 1, but NOT crash
    assert stats["indexed"] == 2
    assert stats["errors"] == 1
    assert stats["skipped"] == 0
    
    # Verify that the two good documents were saved
    doc1 = repo.get("feee17f50347b7f9a4896376a97fda3ea5d70827") # sha1("confluence:good_1")
    assert doc1 is not None
    doc3 = repo.get("81a222755e1166bc76fbf01123d66b4ed8dc8374") # sha1("confluence:good_3")
    assert doc3 is not None
