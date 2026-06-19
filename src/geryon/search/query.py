"""검색 질의 공유 서비스 — 문자열 인자 → SearchFilter → 검색 → dict 직렬화.

MCP `search` 도구와 CLI `geryon search` 가 **모두 이 모듈**을 통해 동일한 경로로
검색·직렬화한다(어댑터별 로직 복제 방지; factory.py 의 단일 진입점 원칙과 동일선상).
표현(JSON 문자열 / 표)만 각 어댑터가 담당한다.
"""
from __future__ import annotations

from datetime import datetime

from geryon.domain.models import SearchFilter, SourceType

_EMPTY_FACETS: dict[str, dict] = {"sources": {}, "spaces_or_repos": {}, "tags": {}, "authors": {}}


def _parse_sources(sources: list[str] | None) -> list[SourceType] | None:
    if sources is None:
        return None
    out: list[SourceType] = []
    for src in sources:
        try:
            out.append(SourceType(src.lower()))
        except ValueError:
            pass  # 알 수 없는 소스명은 무시(부분 필터)
    return out


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None  # 잘못된 날짜는 무시(필터 미적용)


def build_filter(
    *, sources=None, spaces=None, tags=None, categories=None, authors=None,
    date_from=None, date_to=None,
) -> SearchFilter:
    return SearchFilter(
        sources=_parse_sources(sources),
        spaces_or_repos=spaces,
        tags=tags,
        categories=categories,
        authors=authors,
        date_from=_parse_dt(date_from),
        date_to=_parse_dt(date_to),
    )


def hit_to_dict(hit, *, include_author: bool = False) -> dict:
    d = {
        "doc_id": hit.doc_id,
        "title": hit.title,
        "url": hit.url,
        "source": hit.source.value if hasattr(hit.source, "value") else str(hit.source),
        "space_or_repo": hit.space_or_repo,
        "snippet": hit.snippet,
        "score": hit.score,
    }
    if include_author:   # advanced_search 는 작성자 필드를 함께 노출
        d["author"] = hit.author
    return d


def results_to_dict(hits) -> dict:
    """검색 결과(list 또는 facets 보유 SearchHitsList) → {"hits": [...], "facets": {...}}."""
    return {
        "hits": [hit_to_dict(h) for h in hits],
        "facets": getattr(hits, "facets", _EMPTY_FACETS),
    }


def run_search(
    searcher, query: str, *, k: int = 10, offset: int = 0, user_id: str | None = None,
    sources=None, spaces=None, tags=None, categories=None, authors=None,
    date_from=None, date_to=None,
) -> dict:
    """문자열 인자로 검색을 실행하고 직렬화 dict 를 반환(MCP·CLI 공용 단일 경로)."""
    filters = build_filter(
        sources=sources, spaces=spaces, tags=tags, categories=categories,
        authors=authors, date_from=date_from, date_to=date_to,
    )
    hits = searcher.search(query=query, k=k, user_id=user_id, filters=filters, offset=offset)
    return results_to_dict(hits)
