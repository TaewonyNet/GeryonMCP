#!/usr/bin/env python3
"""Confluence REST API 직접 수집 → Bronze 원본 폴더.

- mcp-atlassian(MCP)을 거치지 않고 Confluence Cloud REST API를 **직접** 호출한다.
- 자격증명은 **환경변수**(`CONFLUENCE_URL`/`CONFLUENCE_USERNAME`/`CONFLUENCE_API_TOKEN`)로만
  제어한다. `.env` 는 `geryon.config` 가 import 시 환경변수로 로드한다.
- 기본 수집 범위는 **최근 1개월**(CQL `lastmodified >= today-30d`).
- 산출물 규격: `bronze/confluence/{space}/{pageId}/`(`content.html` + `meta.json` + `attachments/`),
  meta.json은 `docs/specs/02_CONTRACTS.md` §4 표준 어휘를 따른다.

표준 라이브러리만 사용(urllib) — 추가 의존성 없음. 네트워크는 수집 레이어에만 허용된다.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from geryon.index.classify import classify_doc_type
from geryon.acquire.manifest import write_manifest, get_as_of
from geryon.config import default_bronze, ATTACH_MAX_BYTES, ATTACH_SKIP_EXT

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
DEFAULT_BRONZE = Path(default_bronze("confluence"))


# --------------------------------------------------------------------------- #
# 자격증명 로딩 — 환경변수 전용(.env 는 config 가 로드)
# --------------------------------------------------------------------------- #
def load_credentials() -> tuple[str, str, str]:
    """(base_url, username, api_token) 반환 — **환경변수에서만** 읽는다.
    `.env` 는 `geryon.config` 가 import 시 환경변수로 로드한다(별도 자격증명 파일 없음)."""
    url = os.getenv("CONFLUENCE_URL")
    user = os.getenv("CONFLUENCE_USERNAME")
    token = os.getenv("CONFLUENCE_API_TOKEN")
    if url and user and token:
        return url.rstrip("/"), user, token
    raise RuntimeError(
        "Confluence 자격증명을 찾지 못했습니다.\n"
        "  .env 에 CONFLUENCE_URL / CONFLUENCE_USERNAME / CONFLUENCE_API_TOKEN 를 채우세요"
        " (또는 셸에서 export).\n"
        "  cp .env.sample .env  — 실제 .env 는 git 에 올라가지 않습니다."
    )


# --------------------------------------------------------------------------- #
# REST 클라이언트 (urllib + Basic auth + 지수 백오프)
# --------------------------------------------------------------------------- #
class ConfluenceClient:
    def __init__(self, base_url: str, username: str, api_token: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.wiki = self.base if self.base.endswith("/wiki") else self.base + "/wiki"
        raw = f"{username}:{api_token}".encode("utf-8")
        self.auth = "Basic " + base64.b64encode(raw).decode("ascii")
        self.timeout = timeout
        self.download_base = self._resolve_download_base()

    def _resolve_download_base(self) -> str:
        """첨부 다운로드 베이스 URL 결정.

        사이트 도메인(`{site}.atlassian.net/wiki`)의 `/download/attachments/...` 경로는
        이메일+API토큰 Basic 인증을 받지 않고 OAuth(`WWW-Authenticate: OAuth`)를 요구해 401 을 낸다.
        반면 `https://api.atlassian.com/ex/confluence/{cloudId}` 게이트웨이는 동일 Basic 인증을
        정상 처리하고 미디어로 프록시한다. cloudId 조회 실패 시 사이트 도메인으로 폴백한다.
        """
        site_root = self.base.split("/wiki")[0]
        try:
            req = urllib.request.Request(site_root + "/_edge/tenant_info")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                cloud_id = json.loads(resp.read().decode("utf-8")).get("cloudId")
            if cloud_id:
                logger.info("첨부 다운로드 게이트웨이 사용: cloudId=%s", cloud_id)
                return f"https://api.atlassian.com/ex/confluence/{cloud_id}"
        except Exception as e:  # noqa: BLE001
            logger.warning("cloudId 조회 실패, 사이트 도메인으로 폴백: %s", e)
        return self.wiki

    def _get(self, path: str, params: dict[str, Any] | None = None, retries: int = 1) -> dict[str, Any]:
        url = self.wiki + path
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

    def list_spaces(self) -> list[tuple[str, str]]:
        """(key, name) 전역 스페이스 목록(페이지네이션). init 의 선택 목록·토큰 검증에 사용."""
        out: list[tuple[str, str]] = []
        start = 0
        while True:
            data = self._get("/rest/api/space", {"type": "global", "limit": 100, "start": start})
            results = data.get("results", [])
            for s in results:
                out.append((s.get("key", ""), s.get("name", "")))
            if not results or not (data.get("_links") or {}).get("next"):
                break
            start += len(results)
        return out

    def search_recent(self, days: int | None = None,
                      since: str | None = None, until: str | None = None,
                      spaces: list[str] | None = None) -> Iterator[dict[str, Any]]:
        """수정된 page 를 CQL 로 순회. 우선순위:
        - since/until(YYYY-MM-DD) 지정 시 그 **날짜 구간**(lastmodified 범위)
        - 아니면 days 지정 시 **현 시점부터 최근 N일**(lastmodified >= today-days)
        - 둘 다 없으면 전체.
        `spaces` 지정 시 그 스페이스로 한정(`space in (...)`).
        """
        conds = ["type=page"]
        if spaces:
            keys = ",".join(f'"{s}"' for s in spaces)
            conds.append(f"space in ({keys})")
        if since or until:
            if since:
                conds.append(f'lastmodified >= "{since}"')
            if until:
                conds.append(f'lastmodified <= "{until}"')
        elif days is not None:
            d = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            conds.append(f'lastmodified >= "{d}"')
        cql = " and ".join(conds) + " order by lastmodified desc"
        params = {
            "cql": cql,
            "limit": 25,
            "expand": "space,version,history,ancestors,metadata.labels,body.storage",
        }
        path = "/rest/api/content/search"
        while True:
            data = self._get(path, params, retries=1)
            for r in data.get("results", []):
                yield r
            nxt = (data.get("_links") or {}).get("next")
            if not nxt:
                break
            # next는 절대/상대 경로 — params를 비우고 next 경로 사용
            path = nxt.split("/wiki", 1)[-1] if "/wiki" in nxt else nxt
            params = None

    def attachments(self, page_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        params = {"limit": 50, "expand": "metadata,extensions"}
        path = f"/rest/api/content/{page_id}/child/attachment"
        while True:
            data = self._get(path, params, retries=1)
            out.extend(data.get("results", []))
            nxt = (data.get("_links") or {}).get("next")
            if not nxt:
                break
            path = nxt.split("/wiki", 1)[-1] if "/wiki" in nxt else nxt
            params = None
        return out

    def download(self, download_path: str, dest: Path, max_bytes: int | None = None) -> bool:
        # 사이트 도메인의 /download 는 API 토큰을 거부(401·OAuth)하므로 게이트웨이 베이스를 쓴다.
        url = self.download_base + download_path if download_path.startswith("/") else download_path
        req = urllib.request.Request(url, headers={"Authorization": self.auth})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # 크기 상한 2차 방어 — 메타 fileSize 가 0/누락이어도 응답 Content-Length 로 차단.
                if max_bytes:
                    cl = resp.headers.get("Content-Length")
                    if cl and int(cl) > max_bytes:
                        logger.info("첨부 크기 초과 skip(%.1fMB): %s", int(cl) / 1024 / 1024, download_path)
                        return False
                dest.write_bytes(resp.read())
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("첨부 다운로드 실패 %s: %s", download_path, e)
            return False


# --------------------------------------------------------------------------- #
# Bronze 생성
# --------------------------------------------------------------------------- #
def _meta_from_page(page: dict[str, Any], base_url: str, attachments: list[dict[str, Any]]) -> dict[str, Any]:
    space = (page.get("space") or {}).get("key")
    version = page.get("version") or {}
    history = page.get("history") or {}
    ancestors = [a.get("title") for a in page.get("ancestors", []) if a.get("title")]
    labels = [lb.get("name") for lb in ((page.get("metadata") or {}).get("labels") or {}).get("results", [])]
    webui = (page.get("_links") or {}).get("webui", "")
    att_meta = []
    for a in attachments:
        ext = a.get("extensions") or {}
        fn = a.get("title")
        att_meta.append({
            "filename": fn,
            "media_type": ext.get("mediaType"),
            "file_size": ext.get("fileSize"),
            "local_path": f"attachments/{fn}" if fn else None,
            "url": (a.get("_links") or {}).get("download"),
        })
    hierarchy = ([space] if space else []) + ancestors
    return {
        "source": "confluence",
        "source_id": page.get("id"),
        "space_or_repo": space,
        "title": page.get("title"),
        "url": (base_url + webui) if webui else None,
        "author": (history.get("createdBy") or {}).get("displayName"),
        "created_at": history.get("createdDate"),
        "updated_at": version.get("when"),
        "tags": labels,
        "category": [" > ".join(hierarchy)] if hierarchy else [], # 분류 라벨(규칙 보정은 Silver)
        "doc_type": classify_doc_type(page.get("title") or "", hierarchy, labels), # 자체 분류
        "hierarchy": hierarchy,
        "attachments": att_meta,
        "content_file": "content.html",
        "content_format": "storage",
        "raw_meta": {
            "version": version.get("number"),
            "last_modifier": (version.get("by") or {}).get("displayName"),
            "acquired_via": "rest-api:/rest/api/content/search",
        },
        "acquired_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _existing_version(meta_path: Path) -> int | None:
    """기존 meta.json의 수집 버전(raw_meta.version) 반환. 없으면 None."""
    if not meta_path.is_file():
        return None
    try:
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        return (old.get("raw_meta") or {}).get("version")
    except Exception:
        return None


def acquire(
    days: int | None = DEFAULT_DAYS,
    bronze_dir: str | Path = DEFAULT_BRONZE,
    max_pages: int | None = None,
    download_attachments: bool = False,   # 기본 미수집(첨부 본문은 색인 안 됨) — 원할 때만 켠다
    dry_run: bool = False,
    force: bool = False,
    since: str | None = None,
    until: str | None = None,
    spaces: list[str] | None = None,
) -> dict[str, int]:
    """페이지를 REST API로 수집해 Bronze 폴더에 기록(멱등). 통계 반환.

    수집 범위(우선순위):
    - `since`/`until`(YYYY-MM-DD) 지정 시 그 **날짜 구간**(lastmodified 기준).
    - 아니면 `days` 기준 **현 시점부터 최근 N일**(기본 30 = 최근 한 달).
    - `days=None`(`--all`) 이면 전체.

    - **멱등**: 기존 `meta.json`의 version이 API version과 같으면 skip(`force`로 무시).
    - **첨부 멱등**: 기존 첨부 파일이 있으면 다운로드하지 않음(`force`면 재다운로드).
    - 메타데이터는 항상 `meta.json` 파일로 남기며, `force=True`로 파일에서 강제 갱신 가능.
    """
    base_url, user, token = load_credentials()
    client = ConfluenceClient(base_url, user, token)
    root = Path(bronze_dir)
    prev_as_of = get_as_of(root)  # 직전 수집 시점(manifest)
    added: list[str] = []
    modified: list[str] = []
    stats = {"pages": 0, "skipped": 0, "attachments": 0, "att_skipped": 0, "errors": 0}

    for page in client.search_recent(days, since=since, until=until, spaces=spaces):
        if max_pages is not None and (stats["pages"] + stats["skipped"]) >= max_pages:
            break
        try:
            pid = str(page["id"])
            space = (page.get("space") or {}).get("key") or "UNKNOWN"
            api_ver = (page.get("version") or {}).get("number")
            pdir = root / space / pid
            meta_path = pdir / "meta.json"
            existed = meta_path.is_file()  # 신규(added) vs 변경(modified) 판정

            # 멱등: 변경 없음(동일 version)이고 force 아니면 skip
            if not force and _existing_version(meta_path) == api_ver and api_ver is not None:
                stats["skipped"] += 1
                continue

            body = ((page.get("body") or {}).get("storage") or {}).get("value", "")
            atts = client.attachments(pid) if download_attachments else []

            if dry_run:
                logger.info("[dry-run] %s/%s '%s' att=%d (force=%s)", space, pid, page.get("title"), len(atts), force)
                stats["pages"] += 1
                continue

            (pdir / "attachments").mkdir(parents=True, exist_ok=True)
            (pdir / "content.html").write_text(body, encoding="utf-8")
            meta = _meta_from_page(page, base_url, atts)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

            for a in atts:
                dl = (a.get("_links") or {}).get("download")
                fn = a.get("title")
                if not (dl and fn):
                    continue
                # 스킵 필터: 차단 확장자(압축·미디어·실행) / 메타 fileSize 크기 상한 (env 제어)
                ext = Path(fn).suffix.lstrip(".").lower()
                fsize = (a.get("extensions") or {}).get("fileSize")
                if ext in ATTACH_SKIP_EXT:
                    stats["att_skipped"] += 1
                    continue
                if isinstance(fsize, int) and fsize > ATTACH_MAX_BYTES:
                    logger.info("첨부 크기 초과 skip(%.1fMB): %s", fsize / 1024 / 1024, fn)
                    stats["att_skipped"] += 1
                    continue
                dest = pdir / "attachments" / fn
                # 첨부 멱등: 기존 파일 있으면 다운로드 skip(force면 재다운로드)
                if dest.exists() and not force:
                    stats["att_skipped"] += 1
                    continue
                # Content-Length 2차 방어(메타 fileSize 누락 대비)
                if client.download(dl, dest, max_bytes=ATTACH_MAX_BYTES):
                    stats["attachments"] += 1
                else:
                    stats["att_skipped"] += 1
            (modified if existed else added).append(f"{space}/{pid}")
            stats["pages"] += 1
        except Exception as e:  # noqa: BLE001
            logger.error("페이지 수집 실패 %s: %s", page.get("id"), e, exc_info=True)
            stats["errors"] += 1

    # Bronze manifest 기록 — as_of(언제까지) + last_change(뭐가 바뀜). ingest 증분의 기준.
    if not dry_run:
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        write_manifest(
            root, source="confluence", as_of=now_iso,
            stats={"acquired": stats["pages"], "skipped": stats["skipped"]},
            last_change={"since": prev_as_of, "added": added, "modified": modified, "deleted": []},
        )
    logger.info("수집 완료: %s", stats)
    return stats


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=os.getenv("GERYON_LOG_LEVEL", "INFO"))
    ap = argparse.ArgumentParser(description="Confluence REST API → Bronze 수집(최근 N일)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--bronze-dir", default=str(DEFAULT_BRONZE))
    ap.add_argument("--max-pages", type=int, default=None)
    ap.add_argument("--no-attachments", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="기존 파일 무시하고 강제 재수집(파일에서 업데이트)")
    ap.add_argument("--all", action="store_true", help="전체 수집(type=page, 날짜 제한 없음) — --days 무시")
    ap.add_argument("--since", default=None, help="수집 구간 시작(YYYY-MM-DD) — 지정 시 --days 무시")
    ap.add_argument("--until", default=None, help="수집 구간 끝(YYYY-MM-DD)")
    a = ap.parse_args()
    days = None if a.all else a.days
    print(acquire(days=days, bronze_dir=a.bronze_dir,
                  max_pages=a.max_pages, download_attachments=not a.no_attachments,
                  dry_run=a.dry_run, force=a.force, since=a.since, until=a.until))
