import pytest
import tempfile
import os
import json
from datetime import datetime
from geryon.domain.models import Document, SourceType
from geryon.store.repository import SqliteRepository
from geryon.store.vector import VectorStore
from geryon.pipeline.ingest import IngestionPipeline
# We will expose the server creation helper in geryon.mcp.server
from geryon.mcp.server import create_mcp_server

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)

def test_mcp_tools(temp_db):
    # Setup test DB
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)
    
    doc1 = Document(
        doc_id="doc1",
        source=SourceType.CONFLUENCE,
        source_id="page_1",
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
    
    # Create the MCP server
    mcp_app = create_mcp_server(db_path=temp_db)
    
    # Verify tools exist on the app
    # FastMCP has ._tools or registers tools on the mcp app
    assert mcp_app is not None
    
    # Retrieve and test the raw tools directly
    # FastMCP tools can be executed directly as functions
    search_tool = mcp_app.get_tool("search")
    assert search_tool is not None
    
    # Run search directly
    results_str = search_tool(query="할인율")
    results = json.loads(results_str)
    assert len(results["hits"]) == 1
    assert results["hits"][0]["doc_id"] == "6c2e6f14b1925466401f6578a8de5e14e3ad34cd"
    
    # Retrieve get_document tool
    get_doc_tool = mcp_app.get_tool("get_document")
    doc_str = get_doc_tool(doc_id="6c2e6f14b1925466401f6578a8de5e14e3ad34cd")
    doc_data = json.loads(doc_str)
    assert doc_data["title"] == "최대 할인율 개발 정책"
    assert "50% 제한" in doc_data["body_markdown"]
    
    # Retrieve list_sources tool
    list_sources_tool = mcp_app.get_tool("list_sources")
    sources_str = list_sources_tool()
    sources = json.loads(sources_str)
    assert len(sources) == 1
    assert sources[0]["source"] == "confluence"
    assert sources[0]["space_or_repo"] == "DEMO"
    assert sources[0]["doc_count"] == 1
    
    # Retrieve browse tool
    browse_tool = mcp_app.get_tool("browse")
    browse_str = browse_tool(source="confluence", space="DEMO")
    browse_data = json.loads(browse_str)
    assert len(browse_data) == 1
    assert browse_data[0]["title"] == "최대 할인율 개발 정책"
    assert browse_data[0]["doc_id"] == "6c2e6f14b1925466401f6578a8de5e14e3ad34cd"

def test_mcp_not_found_handling(temp_db):
    # Create the MCP server
    mcp_app = create_mcp_server(db_path=temp_db)
    
    get_doc_tool = mcp_app.get_tool("get_document")
    
    # Missing document should return or raise not_found error message
    with pytest.raises(Exception) as exc_info:
        get_doc_tool(doc_id="nonexistent_id")
    
    assert "not_found" in str(exc_info.value).lower()
