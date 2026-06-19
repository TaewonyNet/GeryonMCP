"""검색 공유 서비스(geryon.search.query) — MCP·CLI 가 공유하는 직렬화·필터 계약 고정."""
from types import SimpleNamespace
from geryon.domain.models import SourceType
from geryon.search.query import results_to_dict, build_filter


def _hit(**kw):
    base = dict(doc_id="d1", title="T", url="u", source=SourceType.CONFLUENCE,
               space_or_repo="ENG", snippet="s", score=0.9)
    base.update(kw)
    return SimpleNamespace(**base)


def test_hit_serialization_shape():
    d = results_to_dict([_hit()])
    h = d["hits"][0]
    assert set(h.keys()) == {"doc_id", "title", "url", "source", "space_or_repo", "snippet", "score"}
    assert h["source"] == "confluence"   # enum → value 직렬화
    assert h["doc_id"] == "d1"


def test_include_author_option():
    from geryon.search.query import hit_to_dict
    base = hit_to_dict(_hit(author="홍길동"))
    assert "author" not in base                       # 기본은 author 미포함(search·get_related)
    adv = hit_to_dict(_hit(author="홍길동"), include_author=True)
    assert adv["author"] == "홍길동"                  # advanced_search 는 포함


def test_default_facets_when_plain_list():
    d = results_to_dict([_hit()])
    assert set(d["facets"].keys()) == {"sources", "spaces_or_repos", "tags", "authors"}


def test_facets_passthrough_from_results_object():
    class HitsList(list):
        pass
    lst = HitsList([_hit()])
    lst.facets = {"sources": {"confluence": 1}, "spaces_or_repos": {}, "tags": {}, "authors": {}}
    assert results_to_dict(lst)["facets"]["sources"] == {"confluence": 1}


def test_build_filter_lenient():
    f = build_filter(sources=["confluence", "bogus"], date_from="not-a-date", date_to="2026-01-01")
    assert f.sources == [SourceType.CONFLUENCE]   # 알 수 없는 소스명 무시
    assert f.date_from is None                    # 잘못된 날짜 무시
    assert f.date_to is not None                  # 유효 날짜는 파싱
