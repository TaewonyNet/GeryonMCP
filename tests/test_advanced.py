import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pytest
from geryon.store.repository import SqliteRepository
from geryon.store.db import init_db
from geryon.domain.models import Document, SourceType
from geryon.search.advanced import advanced_search
from datetime import datetime, timezone

@pytest.fixture
def repo(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    r = SqliteRepository(db)
    docs = [
        Document(doc_id="d1", source=SourceType.CONFLUENCE, source_id="s1", space_or_repo="ENG",
                 url=None, title="서버 배포 가이드", body_markdown="무중단 배포 절차", summary=None,
                 tags=["ops"], hierarchy=[], author="홍길동B (Deactivated)",
                 created_at=datetime(2024,3,1,tzinfo=timezone.utc), updated_at=datetime(2024,3,1,tzinfo=timezone.utc),
                 content_hash="h1", ingested_at=datetime(2024,3,1,tzinfo=timezone.utc)),
        Document(doc_id="d2", source=SourceType.CONFLUENCE, source_id="s2", space_or_repo="HR",
                 url=None, title="휴가 정책", body_markdown="연차 사용 규정", summary=None,
                 tags=["policy"], hierarchy=[], author="김철수",
                 created_at=datetime(2023,1,1,tzinfo=timezone.utc), updated_at=datetime(2023,1,1,tzinfo=timezone.utc),
                 content_hash="h2", ingested_at=datetime(2023,1,1,tzinfo=timezone.utc)),
    ]
    for d in docs: r.upsert(d)
    return r

def test_title_field(repo):
    assert [h.doc_id for h in advanced_search(repo, title="배포")] == ["d1"]

def test_author_partial_match(repo):
    # "홍길동"로 "홍길동B (Deactivated)" 매칭 (메타만, query 없이)
    assert [h.doc_id for h in advanced_search(repo, author="홍길동")] == ["d1"]

def test_field_combination_and(repo):
    assert [h.doc_id for h in advanced_search(repo, title="배포", author="홍길동")] == ["d1"]
    assert advanced_search(repo, title="배포", author="김철수") == []

def test_space_and_date(repo):
    assert [h.doc_id for h in advanced_search(repo, space="HR")] == ["d2"]
    assert [h.doc_id for h in advanced_search(repo, date_from="2024-01-01")] == ["d1"]

def test_no_field_returns_empty(repo):
    assert advanced_search(repo) == []
