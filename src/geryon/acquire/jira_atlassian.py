#!/usr/bin/env python3
"""Jira REST API 직접 수집 → Bronze. confluence_atlassian 과 동일 패턴.

- 자격증명: **환경변수 전용** — `JIRA_URL`/`JIRA_USERNAME`/`JIRA_API_TOKEN`, 없으면
  **Confluence 와 같은 Atlassian 사이트**로 보고 `CONFLUENCE_*` 재사용(같은 토큰). `.env` 는 config 가 로드.
- 산출물: `jira_db/{projectKey}/{ISSUE-KEY}.json` (REST 이슈 응답 그대로 = JiraConnector 가 읽는 형식)
- manifest.json 에 as_of + last_change 기록(ingest 증분 기준).

표준 라이브러리만 사용(urllib).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from geryon.acquire.manifest import write_manifest, get_as_of
from geryon.config import default_bronze

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
DEFAULT_BRONZE = Path(default_bronze("jira"))
_FIELDS = "summary,description,reporter,creator,created,updated,labels,issuetype,status,project"


def load_credentials() -> tuple[str, str, str]:
    """(base_url, username, api_token) — **환경변수 전용**. JIRA_* 우선, 없으면 CONFLUENCE_* 재사용.
    `.env` 는 `geryon.config` 가 import 시 환경변수로 로드한다."""
    url = os.getenv("JIRA_URL") or os.getenv("CONFLUENCE_URL")
    user = os.getenv("JIRA_USERNAME") or os.getenv("CONFLUENCE_USERNAME")
    token = os.getenv("JIRA_API_TOKEN") or os.getenv("CONFLUENCE_API_TOKEN")
    if url and user and token:
        return url.rstrip("/").removesuffix("/wiki"), user, token
    raise RuntimeError(
        "Jira 자격증명을 찾지 못했습니다.\n"
        "  .env 에 JIRA_URL/USERNAME/API_TOKEN (또는 CONFLUENCE_* 재사용) 을 채우세요"
        " (또는 셸에서 export)."
    )


class JiraClient:
    def __init__(self, base_url: str, username: str, api_token: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        raw = f"{username}:{api_token}".encode("utf-8")
        self.auth = "Basic " + base64.b64encode(raw).decode("ascii")
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None, retries: int = 1) -> dict[str, Any]:
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": self.auth, "Accept": "application/json"})
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                if attempt >= retries:
                    raise
                wait = 2 ** attempt
                logger.warning("GET %s 실패(%s), %ss 후 재시도", path, e, wait)
                time.sleep(wait)
        return {}

    def list_projects(self) -> list[tuple[str, str]]:
        """(key, name) 프로젝트 목록(페이지네이션). init 의 선택 목록·토큰 검증에 사용."""
        out: list[tuple[str, str]] = []
        start = 0
        while True:
            data = self._get("/rest/api/3/project/search", {"maxResults": 100, "startAt": start})
            values = data.get("values", [])
            for p in values:
                out.append((p.get("key", ""), p.get("name", "")))
            if data.get("isLast", True) or not values:
                break
            start += len(values)
        return out

    def search(self, jql: str) -> Iterator[dict[str, Any]]:
        # Jira Cloud enhanced search(/rest/api/3/search 는 410 Gone) — nextPageToken 페이지네이션
        token: str | None = None
        while True:
            params: dict[str, Any] = {"jql": jql, "maxResults": 50, "fields": _FIELDS}
            if token:
                params["nextPageToken"] = token
            data = self._get("/rest/api/3/search/jql", params, retries=1)
            issues = data.get("issues", [])
            for it in issues:
                yield it
            token = data.get("nextPageToken")
            if not token or not issues:
                break


def acquire(
    project: str,
    days: int | None = DEFAULT_DAYS,
    bronze_dir: str | Path = DEFAULT_BRONZE,
    max_issues: int | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, int]:
    """프로젝트 이슈를 REST 로 수집해 Bronze 에 기록(멱등). manifest 갱신."""
    base_url, user, token = load_credentials()
    client = JiraClient(base_url, user, token)
    root = Path(bronze_dir)
    prev_as_of = get_as_of(root)
    added: list[str] = []
    modified: list[str] = []
    stats = {"issues": 0, "skipped": 0, "errors": 0}

    jql = f"project = {project}"
    if days is not None:
        jql += f' AND updated >= "-{days}d"'
    jql += " ORDER BY updated DESC"

    for issue in client.search(jql):
        if max_issues is not None and (stats["issues"] + stats["skipped"]) >= max_issues:
            break
        try:
            key = str(issue["key"])
            fields = issue.get("fields", {}) or {}
            proj = (fields.get("project") or {}).get("key") or project
            api_updated = fields.get("updated")
            pdir = root / proj
            fpath = pdir / f"{key}.json"
            existed = fpath.is_file()

            # 멱등: 동일 updated 면 skip
            if not force and existed:
                try:
                    old = json.loads(fpath.read_text(encoding="utf-8"))
                    if (old.get("fields", {}) or {}).get("updated") == api_updated and api_updated:
                        stats["skipped"] += 1
                        continue
                except Exception:
                    pass

            if dry_run:
                logger.info("[dry-run] %s/%s '%s'", proj, key, fields.get("summary"))
                stats["issues"] += 1
                continue

            pdir.mkdir(parents=True, exist_ok=True)
            fpath.write_text(json.dumps(issue, ensure_ascii=False, indent=2), encoding="utf-8")
            (modified if existed else added).append(key)
            stats["issues"] += 1
        except Exception as e:  # noqa: BLE001
            logger.error("이슈 수집 실패 %s: %s", issue.get("key"), e, exc_info=True)
            stats["errors"] += 1

    if not dry_run:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_manifest(
            root, source="jira", as_of=now_iso, instance=f"{base_url}/{project}",
            stats={"acquired": stats["issues"], "skipped": stats["skipped"]},
            last_change={"since": prev_as_of, "added": added, "modified": modified, "deleted": []},
        )
    logger.info("Jira 수집 완료: %s", stats)
    return stats


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=os.getenv("GERYON_LOG_LEVEL", "INFO"))
    ap = argparse.ArgumentParser(description="Jira REST → Bronze 수집")
    ap.add_argument("--project", required=True)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--bronze-dir", default=str(DEFAULT_BRONZE))
    ap.add_argument("--max-issues", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    print(acquire(a.project, days=None if a.all else a.days, bronze_dir=a.bronze_dir,
                  max_issues=a.max_issues, dry_run=a.dry_run, force=a.force))
