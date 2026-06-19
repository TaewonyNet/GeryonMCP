"""Git 호스트 레포 목록 조회 — 응답 파싱 + list_repos 라우팅(_fetch seam 주입)."""
import pytest
import geryon.acquire.git_hosts as gh


def test_parse_github():
    data = [{"clone_url": "https://github.com/o/p.git", "full_name": "o/p"}]
    assert gh._parse_github(data) == [("https://github.com/o/p.git", "o/p")]


def test_parse_gitlab():
    data = [{"http_url_to_repo": "https://gitlab.com/o/p.git", "path_with_namespace": "o/p"}]
    assert gh._parse_gitlab(data) == [("https://gitlab.com/o/p.git", "o/p")]


def test_parse_bitbucket_picks_https():
    data = {"values": [{"full_name": "o/p", "links": {"clone": [
        {"name": "ssh", "href": "git@bitbucket.org:o/p.git"},
        {"name": "https", "href": "https://bitbucket.org/o/p.git"}]}}]}
    assert gh._parse_bitbucket(data) == [("https://bitbucket.org/o/p.git", "o/p")]


def test_list_repos_github_route(monkeypatch):
    seen = {}
    def fake(url, headers):
        seen["url"] = url
        return [{"clone_url": "https://github.com/o/p.git", "full_name": "o/p"}]
    monkeypatch.setattr(gh, "_fetch", fake)
    assert gh.list_repos("github", "tok") == [("https://github.com/o/p.git", "o/p")]
    assert "api.github.com/user/repos" in seen["url"]


def test_list_repos_gitlab_route(monkeypatch):
    monkeypatch.setattr(gh, "_fetch",
                        lambda u, h: [{"http_url_to_repo": "https://gitlab.com/o/p.git", "path_with_namespace": "o/p"}])
    assert gh.list_repos("gitlab", "tok") == [("https://gitlab.com/o/p.git", "o/p")]


def test_list_repos_bitbucket_workspace_scoped(monkeypatch):
    """/workspaces 폐기(CHANGE-2770) → 워크스페이스(slug) 직접 받아 /repositories/{ws}."""
    def fake(url, headers):
        assert "/2.0/repositories/ws1" in url and "/workspaces" not in url
        return {"values": [{"full_name": "ws1/r", "links": {"clone": [
            {"name": "https", "href": "https://bitbucket.org/ws1/r.git"}]}}]}
    monkeypatch.setattr(gh, "_fetch", fake)
    out = gh.list_repos("bitbucket", "tok", owner="ws1", username="u@x.com")
    assert out == [("https://bitbucket.org/ws1/r.git", "ws1/r")]


def test_bitbucket_basic_vs_bearer(monkeypatch):
    seen = {}
    def fake(url, headers):
        seen["auth"] = headers.get("Authorization", "")
        return {"values": []}
    monkeypatch.setattr(gh, "_fetch", fake)
    gh.list_repos("bitbucket", "tok", owner="ws", username="me")   # 사용자명 → Basic
    assert seen["auth"].startswith("Basic ")
    gh.list_repos("bitbucket", "tok", owner="ws")                  # 사용자명 없음 → Bearer
    assert seen["auth"] == "Bearer tok"


def test_list_repos_bitbucket_requires_workspace():
    with pytest.raises(ValueError):
        gh.list_repos("bitbucket", "tok", username="u@x.com")   # owner(workspace) 없음


def test_unknown_host():
    with pytest.raises(ValueError):
        gh.list_repos("svn", "tok")
