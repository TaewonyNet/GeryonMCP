"""계층 요약 점수 트리. 요약은 추출식(비-LLM)만. 단일 부모 트리."""
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geryon.config import DB_PATH
from geryon.store.db import init_db


def extract_summary(text: str, limit: int = 280) -> str:
    """리드 문단 우선, 없으면 첫 문장들을 limit까지 누적. LLM 금지(결정적)."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paras:
        return ""
    lead = paras[0]
    if len(lead) <= limit:
        return lead
    sents = re.split(r"(?<=[.!?。])\s+", lead)
    out = ""
    for s in sents:
        if len(out) + len(s) + 1 > limit:
            break
        out = (out + " " + s).strip()
    return (out or lead)[:limit]


def split_by_heading(md: str, levels: tuple[int, ...] = (1, 2, 3)) -> list[tuple[str | None, str]]:
    """heading(#~###)으로 구간 분할 → [(title, body)]. heading 없으면 [(None, 전체)]."""
    max_level = max(levels)
    pat = re.compile(r"^(#{1,%d})\s+(.*)$" % max_level)
    secs: list[tuple[str | None, str]] = []
    cur_title: str | None = None
    cur: list[str] = []
    for line in md.splitlines():
        m = pat.match(line)
        if m and len(m.group(1)) <= max_level:
            if cur or cur_title is not None:
                secs.append((cur_title, "\n".join(cur).strip()))
            cur_title = m.group(2).strip()
            cur = []
        else:
            cur.append(line)
    if cur or cur_title is not None:
        secs.append((cur_title, "\n".join(cur).strip()))
    secs = [(t, b) for (t, b) in secs if (t is not None or b)]
    return secs if secs else [(None, md.strip())]


def rollup(chunk_hits: list[dict[str, Any]]) -> dict[str, float]:
    """청크 히트 → 문서별 max(score) 집계."""
    by: dict[str, float] = {}
    for h in chunk_hits:
        d = h["doc_id"]
        by[d] = max(by.get(d, 0.0), h["score"])
    return by


@dataclass
class TreeNode:
    node_id: str
    parent_id: str | None
    doc_id: str | None
    level: str
    title: str
    summary: str | None
    score: float
    path: str


def build_document_tree(doc: Any, chunks: list[Any]) -> list[TreeNode]:
    """document(루트)→section(heading)→chunk 단일 부모 트리. category는 트리에 포함하지 않음."""
    nodes: list[TreeNode] = []
    dnode = TreeNode(
        node_id=f"{doc.doc_id}#document#0", parent_id=None, doc_id=doc.doc_id,
        level="document", title=doc.title, summary=extract_summary(doc.body_markdown),
        score=0.0, path=str(doc.doc_id),
    )
    nodes.append(dnode)
    for i, (heading, body) in enumerate(split_by_heading(doc.body_markdown)):
        nodes.append(TreeNode(
            node_id=f"{doc.doc_id}#section#{i}", parent_id=dnode.node_id, doc_id=doc.doc_id,
            level="section", title=heading or f"(section {i})", summary=extract_summary(body),
            score=0.0, path=f"{doc.doc_id}#sec{i}",
        ))
    for ch in chunks:
        nodes.append(TreeNode(
            node_id=ch.chunk_id, parent_id=dnode.node_id, doc_id=doc.doc_id,
            level="chunk", title=f"chunk {ch.ordinal}", summary=None, score=0.0, path=ch.chunk_id,
        ))
    return nodes


class TreeStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else Path(DB_PATH)
        self._conn: sqlite3.Connection | None = None

    def get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = init_db(self.db_path)
        return self._conn

    def replace_doc_nodes(self, doc_id: str, nodes: list[TreeNode]) -> None:
        """문서의 트리 노드를 교체(멱등). document 노드를 먼저 삽입해 FK(parent) 충족."""
        conn = self.get_connection()
        cur = conn.cursor()
        try:
            _ = cur.execute("DELETE FROM tree_nodes WHERE doc_id = ?;", (doc_id,))
            for n in sorted(nodes, key=lambda x: 0 if x.parent_id is None else 1):
                _ = cur.execute(
                    """INSERT OR REPLACE INTO tree_nodes
                       (node_id, parent_id, doc_id, level, title, summary, score, path)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
                    (n.node_id, n.parent_id, n.doc_id, n.level, n.title, n.summary, n.score, n.path),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
