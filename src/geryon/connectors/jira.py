"""Jira 커넥터 — 로컬 Bronze(bronze/jira)의 이슈 JSON을 표준 RawRecord로 산출.

확장 패턴(docs/EXTENDING_SOURCES.md): Connector 를 구현하고 RawRecord.metadata 에
표준 키(author·created_at·updated_at·tags·doc_type)를 채우면 Normalizer 가 소스 무관으로
표준 Document 로 변환한다.

Bronze 레이아웃: bronze/jira/{project}/{ISSUE-KEY}.json  (Jira REST 이슈 응답 그대로 저장)
실제 수집(REST 자동화)은 acquire/ 에 confluence_atlassian 과 동일 패턴으로 추가할 수 있다(향후).
"""
import json
import os
from collections.abc import Iterator
from pathlib import Path

from geryon.connectors.base import Connector
from geryon.domain.models import RawRecord, SourceType
from geryon.config import default_bronze


def _description_text(desc: object) -> str:
    """Jira description 을 평문으로. 문자열(REST v2)·ADF(dict, v3) 모두 처리."""
    if not desc:
        return ""
    if isinstance(desc, str):
        return desc
    # ADF(Atlassian Document Format): text 노드만 추출
    out: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                out.append(str(node.get("text", "")))
            walk(node.get("content"))
        elif isinstance(node, list):
            for c in node:
                walk(c)

    walk(desc)
    return " ".join(t for t in out if t)


class JiraConnector(Connector):
    source_type: SourceType = SourceType.JIRA
    db_path: Path

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path(default_bronze("jira"))

    def healthcheck(self) -> bool:
        return self.db_path.is_dir()

    def iter_raw(self, only: set[str] | None = None) -> Iterator[RawRecord]:
        if not self.healthcheck():
            return
        base = self.db_path.resolve()
        for root, _dirs, files in os.walk(str(base)):
            for fn in files:
                if not fn.endswith(".json"):
                    continue
                # 증분: manifest.last_change 가 지정한 이슈키(only)만. 파일명=KEY 라 read 전 판정.
                if only is not None and Path(fn).stem not in only:
                    continue
                try:
                    issue = json.loads(Path(root, fn).read_text(encoding="utf-8"))
                except Exception:
                    continue
                fields = issue.get("fields", {}) if isinstance(issue, dict) else {}
                key = issue.get("key") or Path(fn).stem
                project = (fields.get("project") or {}).get("key") or Path(root).name
                reporter = (fields.get("reporter") or fields.get("creator") or {})
                issue_type = (fields.get("issuetype") or {}).get("name")
                # 출처 원본 링크: REST self URL → 브라우저 URL(/browse/KEY)
                self_url = issue.get("self") or ""
                browse_url = (
                    f"{self_url.split('/rest/')[0]}/browse/{key}"
                    if "/rest/" in self_url else (self_url or None)
                )
                yield RawRecord(
                    source=SourceType.JIRA,
                    source_id=str(key),
                    raw_body=_description_text(fields.get("description")),
                    raw_format="markdown",  # 평문/마크다운 — Normalizer 가 그대로 사용
                    title=fields.get("summary"),
                    url=browse_url,
                    space_or_repo=str(project) if project else None,
                    metadata={
                        "author": (reporter.get("displayName") if isinstance(reporter, dict) else None),
                        "created_at": fields.get("created"),
                        "updated_at": fields.get("updated"),
                        "tags": fields.get("labels", []) or [],
                        "doc_type": "issue",
                        "issue_type": issue_type,
                        "status": (fields.get("status") or {}).get("name"),
                    },
                )
