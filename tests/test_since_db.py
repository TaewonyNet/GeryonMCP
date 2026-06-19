"""--since-db: DB의 최신 updated_at 워터마크로 '이후만' 재수집(Bronze 불필요)."""
from datetime import datetime, timezone
from geryon.store.repository import SqliteRepository
from geryon.domain.models import Document, SourceType


def _doc(sid, dt):
    return Document(doc_id=sid, source=SourceType.CONFLUENCE, source_id=sid, space_or_repo="X",
                    title="t" + sid, body_markdown="body", content_hash="h" + sid,
                    updated_at=dt, ingested_at=datetime.utcnow())


def test_latest_doc_updated_at_returns_max(tmp_path):
    r = SqliteRepository(str(tmp_path / "t.db"))
    r.upsert(_doc("a", datetime(2026, 1, 1, tzinfo=timezone.utc)))
    r.upsert(_doc("b", datetime(2026, 3, 5, tzinfo=timezone.utc)))
    wm = r.latest_doc_updated_at(SourceType.CONFLUENCE)
    assert wm is not None and wm.strftime("%Y-%m-%d") == "2026-03-05"


def test_latest_doc_updated_at_none_when_empty(tmp_path):
    r = SqliteRepository(str(tmp_path / "t.db"))
    assert r.latest_doc_updated_at(SourceType.JIRA) is None
