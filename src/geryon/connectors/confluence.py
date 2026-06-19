import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from bs4 import BeautifulSoup

from geryon.config import DEFAULT_CONFLUENCE_DB_PATH
from geryon.connectors.base import Connector
from geryon.domain.models import Attachment, RawRecord, SourceType


class ConfluenceConnector(Connector):
    source_type: SourceType = SourceType.CONFLUENCE
    db_path: Path

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            self.db_path = DEFAULT_CONFLUENCE_DB_PATH
        else:
            self.db_path = Path(db_path)

    def healthcheck(self) -> bool:
        """Check if the db_path directory physically exists."""
        return self.db_path.is_dir()

    def iter_raw(self, only: set[str] | None = None) -> Iterator[RawRecord]:
        """소스를 순회하며 RawRecord를 지연 산출(yield)한다. only 지정 시 그 항목만(증분)."""
        if not self.healthcheck():
            return

        db_path_resolved = self.db_path.resolve()
        for root, _dirs, _files in os.walk(str(db_path_resolved)):
            page_dir = Path(root).resolve()
            try:
                rel_to_db = page_dir.relative_to(db_path_resolved)
            except ValueError:
                continue

            if len(rel_to_db.parts) != 2:
                continue

            space, page_id = rel_to_db.parts

            # 증분: manifest.last_change 가 지정한 항목(only)만 처리. None 이면 전체.
            if only is not None and f"{space}/{page_id}" not in only:
                continue

            meta_path = page_dir / "meta.json"
            meta: dict[str, Any] = {}
            has_meta = False

            if meta_path.is_file():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = cast(dict[str, Any], json.load(f))
                    has_meta = True
                except Exception:
                    pass

            if has_meta:
                content_file = str(meta.get("content_file", "content.html"))
                content_path = page_dir / content_file
                if not content_path.is_file():
                    continue

                try:
                    with open(content_path, "r", encoding="utf-8") as f:
                        raw_body = f.read()
                except Exception:
                    continue

                raw_format = str(meta.get("content_format", "storage"))
                title = meta.get("title")
                title_str = str(title) if title is not None else None
                url = meta.get("url")
                url_str = str(url) if url is not None else None
                space_or_repo = str(meta.get("space_or_repo", space))
                source_id = str(meta.get("source_id", page_id))

                metadata: dict[str, Any] = {
                    "author": meta.get("author"),
                    "created_at": meta.get("created_at"),
                    "updated_at": meta.get("updated_at"),
                    "tags": meta.get("tags", []),
                    "hierarchy": meta.get("hierarchy", []),
                    "raw_meta": meta.get("raw_meta", {}),
                    "acquired_at": meta.get("acquired_at"),
                }

                attachments: list[Attachment] = []
                raw_attachments = meta.get("attachments")
                if isinstance(raw_attachments, list):
                    for att in raw_attachments:
                        if isinstance(att, dict):
                            attachments.append(
                                Attachment(
                                    filename=str(att.get("filename", "")),
                                    media_type=str(att.get("media_type")) if att.get("media_type") is not None else None,
                                    local_path=str(att.get("local_path")) if att.get("local_path") is not None else None,
                                    url=str(att.get("url")) if att.get("url") is not None else None,
                                )
                            )

                yield RawRecord(
                    source=SourceType.CONFLUENCE,
                    source_id=source_id,
                    raw_body=raw_body,
                    raw_format=raw_format,
                    title=title_str,
                    url=url_str,
                    space_or_repo=space_or_repo,
                    metadata=metadata,
                    attachments=attachments,
                )

            else:
                # Fallback legacy path
                content_path = page_dir / "content.html"
                if not content_path.is_file():
                    continue

                try:
                    with open(content_path, "r", encoding="utf-8") as f:
                        raw_body = f.read()
                except Exception:
                    continue

                raw_format = "html"
                space_or_repo = space
                source_id = page_id

                # Best-effort extract title using BeautifulSoup (Bronze 정제)
                # 우선순위: <title> > 첫 번째 heading(h1/h2) > page_id fallback
                title_str = None
                try:
                    soup = BeautifulSoup(raw_body, "html.parser")
                    if soup.title and soup.title.string:
                        title_str = soup.title.string.strip()
                    if not title_str:
                        # 첫 번째 h1 또는 h2 heading을 best-effort title로 사용
                        for htag in ("h1", "h2"):
                            h = soup.find(htag)
                            if h:
                                text = h.get_text(strip=True)
                                if text:
                                    title_str = text
                                    break
                except Exception:
                    pass

                if not title_str:
                    title_str = page_id

                # Scan attachments subdirectory if exists
                attachments = []
                attachments_dir = page_dir / "attachments"
                if attachments_dir.is_dir():
                    for att_root, _, att_files in os.walk(str(attachments_dir)):
                        for att_file in att_files:
                            att_path = Path(att_root) / att_file
                            try:
                                rel_path = att_path.relative_to(page_dir)
                                attachments.append(
                                    Attachment(
                                        filename=att_file,
                                        local_path=str(rel_path),
                                    )
                                )
                            except Exception:
                                pass

                yield RawRecord(
                    source=SourceType.CONFLUENCE,
                    source_id=source_id,
                    raw_body=raw_body,
                    raw_format=raw_format,
                    title=title_str,
                    url=None,
                    space_or_repo=space_or_repo,
                    metadata={},
                    attachments=attachments,
                )
