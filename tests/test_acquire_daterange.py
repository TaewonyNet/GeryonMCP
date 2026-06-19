"""confluence acquire 수집 범위(최근 N일/날짜 구간/전체) CQL 생성 + CLI 날짜 검증."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from geryon.acquire.confluence_atlassian import ConfluenceClient


def _capture_cql(**kwargs) -> str:
    """search_recent 가 첫 요청에 만든 CQL 을 반환(_get 모킹)."""
    captured = {}

    def fake_get(self, path, params, retries=1):
        if params and "cql" in params and "cql" not in captured:
            captured["cql"] = params["cql"]
        return {"results": [], "_links": {}}

    c = ConfluenceClient("https://x", "u", "t")
    with patch.object(ConfluenceClient, "_get", fake_get):
        list(c.search_recent(**kwargs))
    return captured.get("cql", "")


def test_recent_days_is_default_last_month():
    """days=30(기본) → 현 시점부터 최근 한 달(lastmodified >= today-30)."""
    cql = _capture_cql(days=30)
    expected = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    assert f'lastmodified >= "{expected}"' in cql
    assert "<=" not in cql  # 상한 없음


def test_date_range_since_until():
    cql = _capture_cql(since="2026-01-01", until="2026-03-31")
    assert 'lastmodified >= "2026-01-01"' in cql
    assert 'lastmodified <= "2026-03-31"' in cql


def test_since_overrides_days():
    """since 지정 시 days 는 무시(상대 날짜가 안 들어감)."""
    cql = _capture_cql(days=30, since="2026-05-01")
    assert 'lastmodified >= "2026-05-01"' in cql
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert today not in cql


def test_until_only_sets_upper_bound():
    cql = _capture_cql(until="2026-05-31")
    assert 'lastmodified <= "2026-05-31"' in cql
    assert ">=" not in cql


def test_all_has_no_date_filter():
    cql = _capture_cql(days=None)
    assert "lastmodified >=" not in cql  # 날짜 필터 없음(order by 절의 lastmodified 는 무관)
    assert "lastmodified <=" not in cql
    assert "type=page" in cql


def test_cli_rejects_bad_date_format():
    from geryon.cli import _build_acquirer

    args = type("A", (), {"bronze_dir": None, "days": 30, "all": False,
                          "max_pages": None, "no_attachments": False,
                          "since": "2026/01/01", "until": None})()
    with pytest.raises(RuntimeError, match="형식 오류"):
        _build_acquirer(args, "confluence")


def test_cli_rejects_since_after_until():
    from geryon.cli import _build_acquirer

    args = type("A", (), {"bronze_dir": None, "days": 30, "all": False,
                          "max_pages": None, "no_attachments": False,
                          "since": "2026-05-01", "until": "2026-01-01"})()
    with pytest.raises(RuntimeError, match="늦"):
        _build_acquirer(args, "confluence")
