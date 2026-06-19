import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geryon.index.category import categories_for  # noqa: E402
from geryon.store.repository import SqliteRepository  # noqa: E402
from geryon.domain.models import Document, SourceType  # noqa: E402


class _D:
    def __init__(self, **k):
        self.__dict__.update(k)


def _doc(doc_id="d1", category=None, body="b", title="T"):
    return Document(
        doc_id=doc_id, source=SourceType.CONFLUENCE, source_id=doc_id,
        title=title, body_markdown=body, category=category or [],
        content_hash="h_" + doc_id,
        ingested_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def test_categories_for_rules():
    rules = [{"match": {"space": "TE"}, "set": ["검색서비스"]},
             {"match": {"tag": "Silo"}, "set": ["Silo도메인"]}]
    d = _D(category=["원본"], space_or_repo="TE", tags=["Silo"], hierarchy=["TE", "X"])
    assert categories_for(d, rules) == ["원본", "검색서비스", "Silo도메인"]  # 다중·순서·중복제거


def test_upsert_and_get_multi_category(tmp_path):
    repo = SqliteRepository(tmp_path / "c.db")
    repo.upsert(_doc(category=["A", "B", "A"]))  # 중복 A
    got = repo.get("d1")
    assert set(got.category) == {"A", "B"}       # 자유 다중값 저장/조회


def test_reapply_categories_keeps_content_hash(tmp_path):
    repo = SqliteRepository(tmp_path / "c.db")
    repo.upsert(_doc("d1", category=["원본"]))
    h0 = repo.get("d1").content_hash
    rules = [{"match": {}, "set": ["규칙분류"]}]  # 빈 match = 항상 적용
    repo.reapply_categories(rules)
    got = repo.get("d1")
    assert "규칙분류" in got.category
    assert got.content_hash == h0 # content_hash 불변(본문 재색인 없음)


def test_categories_filter_in_keyword_search(tmp_path):
    from geryon.search.keyword import KeywordRetriever
    from geryon.domain.models import SearchFilter
    repo = SqliteRepository(tmp_path / "f.db")
    repo.upsert(_doc("d1", category=["alpha"], body="lorem ipsum dolor", title="lorem"))
    repo.upsert(_doc("d2", category=["beta"], body="lorem ipsum dolor", title="lorem"))
    kr = KeywordRetriever(repository=repo)
    hits = kr.search("lorem", filters=SearchFilter(categories=["alpha"]))
    ids = {h.doc_id for h in hits}
    assert "d1" in ids and "d2" not in ids # categories 필터 적용
