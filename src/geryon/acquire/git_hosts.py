"""Git 호스트 저장소 목록 조회 — GitHub / GitLab / Bitbucket REST API.

`geryon init` 의 Git 단계에서 토큰으로 접근 가능한 저장소를 나열해 선택하게 한다.
표준 라이브러리(urllib)만 사용. 페이지는 첫 100개(init 편의용; 더 많으면 직접 URL 입력).
반환: [(clone_url_https, full_name), ...]
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

_TIMEOUT = 30


def _fetch(url: str, headers: dict[str, str]):
    """JSON GET (테스트에서 monkeypatch 하는 seam). 실패 시 실패한 URL 을 메시지에 포함."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason} @ {url}") from None


def _parse_github(data) -> list[tuple[str, str]]:
    return [(r["clone_url"], r.get("full_name", "")) for r in data if r.get("clone_url")]


def _parse_gitlab(data) -> list[tuple[str, str]]:
    return [(r["http_url_to_repo"], r.get("path_with_namespace", "")) for r in data if r.get("http_url_to_repo")]


def _parse_bitbucket(data) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for r in data.get("values", []):
        https = next((c["href"] for c in (r.get("links", {}).get("clone") or []) if c.get("name") == "https"), None)
        if https:
            out.append((https, r.get("full_name", "")))
    return out


def list_repos(host: str, token: str, owner: str | None = None, username: str | None = None) -> list[tuple[str, str]]:
    """호스트의 접근 가능한 저장소 목록 (clone_url_https, full_name).
    - github: owner(org/user) 지정 시 그 소유자, 아니면 토큰의 repo. 토큰 헤더 Bearer.
    - gitlab: 멤버인 프로젝트(PRIVATE-TOKEN). 자체호스팅은 미지원(gitlab.com).
    - bitbucket: username + app password(Basic), 멤버 저장소."""
    host = host.lower()
    if host == "github":
        gh = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
              "User-Agent": "GeryonMCP"}
        if owner:
            try:
                return _parse_github(_fetch(f"https://api.github.com/orgs/{owner}/repos?per_page=100&sort=updated", gh))
            except Exception:  # noqa: BLE001 — org 가 아니면 user 로 재시도
                return _parse_github(_fetch(f"https://api.github.com/users/{owner}/repos?per_page=100&sort=updated", gh))
        return _parse_github(_fetch("https://api.github.com/user/repos?per_page=100&sort=updated", gh))
    if host == "gitlab":
        url = "https://gitlab.com/api/v4/projects?membership=true&per_page=100&order_by=last_activity_at"
        return _parse_gitlab(_fetch(url, {"PRIVATE-TOKEN": token}))
    if host == "bitbucket":
        # 전역 /repositories?role=member · /workspaces 는 폐기(CHANGE-2770).
        # 워크스페이스(slug)를 직접 받아 워크스페이스 범위 엔드포인트로 조회한다.
        # 인증: 사용자명이 있으면 Basic(이메일 + 스코프 API 토큰; 구 앱비밀번호도 동일),
        # 없으면 워크스페이스/리포 액세스 토큰(Bearer).
        # (앱 비밀번호는 2026 폐지 — id.atlassian.com 에서 'API token with scopes'(Bitbucket 스코프)를
        #  만들어 이메일+토큰 Basic 으로 인증. 스코프 없는 Confluence/Jira 토큰은 401.)
        if not owner:
            raise ValueError("Bitbucket 은 워크스페이스(slug)가 필요합니다.")
        if username:
            cred = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
            headers = {"Authorization": f"Basic {cred}"}
        else:
            headers = {"Authorization": f"Bearer {token}"}   # 워크스페이스/리포 액세스 토큰
        url = f"https://api.bitbucket.org/2.0/repositories/{owner}?pagelen=100&sort=-updated_on"
        return _parse_bitbucket(_fetch(url, headers))
    raise ValueError(f"알 수 없는 호스트: {host} (github|gitlab|bitbucket)")


# 호스트별 권장 GERYON_GIT_USERNAME(토큰 clone 시)
GIT_USERNAME_FOR = {"github": "", "gitlab": "oauth2", "bitbucket": "x-token-auth"}
