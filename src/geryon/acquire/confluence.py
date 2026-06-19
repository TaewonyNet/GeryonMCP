"""ConfluenceAcquirer — 기존 confluence_atlassian.acquire() 를 Acquirer 인터페이스로 래핑."""
from __future__ import annotations

from geryon.acquire.base import Acquirer
from geryon.acquire.confluence_atlassian import acquire as _confluence_acquire, load_credentials


class ConfluenceAcquirer(Acquirer):
    def __init__(
        self,
        bronze_dir: str = "bronze/confluence",
        days: int = 30,
        all_pages: bool = False,
        max_pages: int | None = None,
        download_attachments: bool = False,   # 기본 미수집(첨부 본문 미색인) — 원할 때만
        since: str | None = None,
        until: str | None = None,
        spaces: list[str] | None = None,
    ) -> None:
        self.bronze_dir = bronze_dir
        self.days = None if all_pages else days  # None = 전체
        self.max_pages = max_pages
        self.download_attachments = download_attachments
        self.since = since  # 날짜 구간 시작(YYYY-MM-DD) — 지정 시 days 무시
        self.until = until  # 날짜 구간 끝
        self.spaces = spaces  # 한정할 스페이스 키(비우면 전체)

    def acquire(self, *, force: bool = False, dry_run: bool = False) -> dict[str, int]:
        return _confluence_acquire(
            days=self.days,
            bronze_dir=self.bronze_dir,
            max_pages=self.max_pages,
            download_attachments=self.download_attachments,
            dry_run=dry_run,
            force=force,
            since=self.since,
            until=self.until,
            spaces=self.spaces,
        )

    def healthcheck(self) -> bool:
        try:
            load_credentials()
            return True
        except Exception:
            return False
