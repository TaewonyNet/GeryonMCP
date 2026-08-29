import pytest
import tempfile
import os
from datetime import datetime
from geryon.domain.models import SourceType, SearchFilter, RawRecord
from geryon.store.repository import SqliteRepository
from geryon.store.vector import VectorStore
from geryon.pipeline.ingest import IngestionPipeline
from geryon.search.hybrid import HybridRetriever
from geryon.mcp.server import create_mcp_server

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

def test_gt14_search_enhancements_filters_and_pagination(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)

    doc1 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="page_1",
        raw_body="<h1>최대 할인율 개발 정책</h1><p>할인율을 계산할 때는 최댓값 50% 제한 규칙을 준수한다.</p>",
        raw_format="html",
        title="최대 할인율 개발 정책",
        space_or_repo="DEMO",
        metadata={
            "author": "alice",
            "created_at": "2026-05-01T00:00:00Z",
            "updated_at": "2026-05-01T00:00:00Z"
        }
    )
    doc2 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="page_2",
        raw_body="<h1>Jira 연동 가이드</h1><p>Jira 이슈 필드 연동 절차.</p>",
        raw_format="html",
        title="Jira 연동 가이드",
        space_or_repo="TE",
        metadata={
            "author": "bob",
            "created_at": "2026-05-10T00:00:00Z",
            "updated_at": "2026-05-10T00:00:00Z"
        }
    )
    doc3 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="page_3",
        raw_body="<h1>할인율 예외 규정</h1><p>할인율 최댓값 예외 기준 정의.</p>",
        raw_format="html",
        title="할인율 예외 규정",
        space_or_repo="TE",
        metadata={
            "author": "charlie",
            "created_at": "2026-05-20T00:00:00Z",
            "updated_at": "2026-05-20T00:00:00Z"
        }
    )

    pipeline.run(MockConnector([doc1, doc2, doc3]))

    retriever = HybridRetriever(repository=repo, vector_store=vstore)

    # 1. Test Authors Filter
    filter_author = SearchFilter(authors=["alice"])
    hits_author = retriever.search("할인율", filters=filter_author)
    assert len(hits_author) == 1
    assert hits_author[0].doc_id == "6c2e6f14b1925466401f6578a8de5e14e3ad34cd"  # doc1 (alice)

    # 2. Test Date From Filter (updated_at >= 2026-05-05)
    filter_date_from = SearchFilter(date_from=datetime(2026, 5, 5))
    hits_date_from = retriever.search("할인율", filters=filter_date_from)
    # doc2 and doc3 are newer than May 5, doc1 is May 1.
    # Note: doc2 title does not contain "할인율" but semantic vector matches it!
    # Let's verify that we retrieve correct hits.
    assert len(hits_date_from) >= 1
    assert not any(h.doc_id == "6c2e6f14b1925466401f6578a8de5e14e3ad34cd" for h in hits_date_from)

    # 3. Test Offset Pagination
    hits_full = retriever.search("할인율", k=10)
    assert len(hits_full) >= 2
    
    hits_page1 = retriever.search("할인율", k=1, offset=0)
    hits_page2 = retriever.search("할인율", k=1, offset=1)
    
    assert len(hits_page1) == 1
    assert len(hits_page2) == 1
    assert hits_page1[0].doc_id != hits_page2[0].doc_id

def test_gt14_facet_counting(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)

    doc1 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="page_1",
        raw_body="<h1>최대 할인율 개발 정책</h1><p>할인율 제한.</p>",
        raw_format="html",
        title="최대 할인율 개발 정책",
        space_or_repo="DEMO",
        metadata={"author": "alice", "tags": ["pricing"]}
    )
    doc2 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="page_2",
        raw_body="<h1>할인율 안내</h1><p>할인율 한도.</p>",
        raw_format="html",
        title="할인율 안내",
        space_or_repo="TE",
        metadata={"author": "bob", "tags": ["pricing", "jira"]}
    )

    pipeline.run(MockConnector([doc1, doc2]))

    retriever = HybridRetriever(repository=repo, vector_store=vstore)
    hits = retriever.search("할인율", k=10)
    
    assert hasattr(hits, "facets")
    facets = getattr(hits, "facets")
    assert facets["sources"]["confluence"] == 2
    assert facets["spaces_or_repos"]["DEMO"] == 1
    assert facets["spaces_or_repos"]["TE"] == 1
    assert facets["tags"]["pricing"] == 2
    assert facets["authors"]["alice"] == 1

def test_gt14_get_related(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)

    # get_related 는 벡터 유사도가 아니라 **문서 트리(계층) 형제/부모 + 링크** 기반(임베딩 불필요).
    # 두 문서를 같은 부모(["DEMO","할인정책"]) 아래 형제로 두면 서로 연관 문서가 되어야 한다.
    doc1 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="page_1",
        raw_body="<h1>최대 할인율 개발 정책</h1><p>할인율 제한 규칙 준수.</p>",
        raw_format="html",
        title="최대 할인율 개발 정책",
        space_or_repo="DEMO",
        metadata={"hierarchy": ["DEMO", "할인정책", "최대할인율"]},
    )
    doc2 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="page_2",
        raw_body="<h1>할인율 예외 규정</h1><p>할인율 최댓값 예외 기준 정의.</p>",
        raw_format="html",
        title="할인율 예외 규정",
        space_or_repo="DEMO",
        metadata={"hierarchy": ["DEMO", "할인정책", "예외규정"]},
    )

    pipeline.run(MockConnector([doc1, doc2]))

    retriever = HybridRetriever(repository=repo, vector_store=vstore)

    # doc_id 는 내용+계층 해시라 고정값을 못 쓴다 — source_id 로 조회.
    conn = repo.get_connection()
    doc1_id = conn.execute(
        "SELECT doc_id FROM documents WHERE source = ? AND source_id = ?",
        (SourceType.CONFLUENCE.value, "page_1"),
    ).fetchone()[0]

    related = retriever.get_related(doc1_id, k=5)
    assert len(related) > 0
    # 같은 부모 아래 형제(doc2)가 연관 문서로 잡혀야 한다.
    assert any(h.title == "할인율 예외 규정" for h in related)
    # 질의 문서 자신은 결과에 포함되면 안 된다.
    assert not any(h.doc_id == doc1_id for h in related)

def test_gt14_mcp_resources_and_prompts(temp_db):
    repo = SqliteRepository(temp_db)
    vstore = VectorStore(db_path=temp_db)
    pipeline = IngestionPipeline(repository=repo, vector_store=vstore)

    doc1 = RawRecord(
        source=SourceType.CONFLUENCE,
        source_id="page_1",
        raw_body="<h1>최대 할인율 개발 정책</h1><p>할인율 제한 규칙 준수.</p>",
        raw_format="html",
        title="최대 할인율 개발 정책",
        space_or_repo="DEMO"
    )

    pipeline.run(MockConnector([doc1]))

    mcp_app = create_mcp_server(db_path=temp_db)
    assert mcp_app is not None

    # Retrieve and call MCP resource function
    # FastMCP resources are registered under app._resource_manager
    import asyncio
    async def get_res():
        resource_obj = await mcp_app._resource_manager.get_resource("geryon://documents/6c2e6f14b1925466401f6578a8de5e14e3ad34cd")
        assert resource_obj is not None
        return await resource_obj.read()
        
    body = asyncio.run(get_res())
    assert "할인율 제한" in body

    # Retrieve and call MCP Prompts
    prompt_qa = mcp_app._prompt_manager.get_prompt("document_qa")
    assert prompt_qa is not None
    prompt_qa_text = prompt_qa.fn(query="What is the discount limit?", document_text="Discount limit is 50%")
    assert "Discount limit is 50%" in prompt_qa_text
    assert "What is the discount limit?" in prompt_qa_text
