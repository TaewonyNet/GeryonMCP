"""Bronze manifest — 동기화 단위 기술서(source·as_of·last_change). [24_BRONZE_CONTRACT]

Bronze 루트마다 manifest.json 을 둔다:
  - source: confluence | git | jira
  - as_of:  언제까지의 데이터(수집 시각 / git 은 HEAD 커밋)
  - last_change: 직전 acquire 가 무엇을 바꿨나(added/modified/deleted) — ingest 증분의 기준

표준 라이브러리만 사용(수집 레이어).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
SCHEMA_VERSION = 1


def read_manifest(root: str | Path) -> dict[str, Any] | None:
    p = Path(root) / MANIFEST_NAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_manifest(
    root: str | Path,
    *,
    source: str,
    as_of: str | None,
    stats: dict[str, Any] | None = None,
    last_change: dict[str, Any] | None = None,
    instance: str | None = None,
) -> dict[str, Any]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "source": source, "as_of": as_of}
    if instance:
        data["instance"] = instance
    data["stats"] = stats or {}
    data["last_change"] = last_change or {"since": None, "added": [], "modified": [], "deleted": []}
    (root / MANIFEST_NAME).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def get_as_of(root: str | Path) -> str | None:
    m = read_manifest(root)
    return m.get("as_of") if m else None


def get_last_change(root: str | Path) -> dict[str, Any] | None:
    m = read_manifest(root)
    return m.get("last_change") if m else None


def empty_change(since: str | None = None) -> dict[str, Any]:
    return {"since": since, "added": [], "modified": [], "deleted": []}
