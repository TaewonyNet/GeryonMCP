"""GitAcquirer 토큰 폴백 — 기본 plain(사용자 git 인증), GERYON_GIT_TOKEN 있으면 https 주입."""
from geryon.acquire.git import _authed_url


def test_plain_when_no_token(monkeypatch):
    monkeypatch.delenv("GERYON_GIT_TOKEN", raising=False)
    assert _authed_url("https://github.com/o/p.git") == "https://github.com/o/p.git"


def test_ssh_untouched(monkeypatch):
    monkeypatch.setenv("GERYON_GIT_TOKEN", "tok")
    assert _authed_url("git@github.com:o/p.git") == "git@github.com:o/p.git"  # SSH 그대로


def test_token_only_injected(monkeypatch):
    monkeypatch.setenv("GERYON_GIT_TOKEN", "tok")
    monkeypatch.delenv("GERYON_GIT_USERNAME", raising=False)
    assert _authed_url("https://github.com/o/p.git") == "https://tok@github.com/o/p.git"


def test_user_and_token(monkeypatch):
    monkeypatch.setenv("GERYON_GIT_TOKEN", "tok")
    monkeypatch.setenv("GERYON_GIT_USERNAME", "oauth2")
    assert _authed_url("https://gitlab.com/o/p.git") == "https://oauth2:tok@gitlab.com/o/p.git"


def test_existing_creds_untouched(monkeypatch):
    monkeypatch.setenv("GERYON_GIT_TOKEN", "tok")
    assert _authed_url("https://u:p@bitbucket.org/o/p.git") == "https://u:p@bitbucket.org/o/p.git"
