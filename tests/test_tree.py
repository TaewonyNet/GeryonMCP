import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geryon.store.tree import (  # noqa: E402
    extract_summary, split_by_heading, rollup, build_document_tree, TreeStore,
)
from geryon.domain.models import Chunk  # noqa: E402


class _Doc:
    def __init__(self, doc_id, title, body):
        self.doc_id = doc_id
        self.title = title
        self.body_markdown = body


def test_extract_summary_len_and_determinism():
    txt = "리드 문단입니다. " * 50
    s1 = extract_summary(txt)
    s2 = extract_summary(txt)
    assert s1 == s2 and len(s1) <= 280


def test_extract_summary_lead_paragraph():
    assert extract_summary("리드.\n\n둘째 문단.") == "리드."


def test_split_by_heading():
    secs = split_by_heading("# A\nx\n## B\ny", (1, 2, 3))
    assert secs == [("A", "x"), ("B", "y")]


def test_split_by_heading_none():
    secs = split_by_heading("heading 없는 본문", (1, 2, 3))
    assert len(secs) == 1 and secs[0][0] is None


def test_rollup_max():
    assert rollup([{"doc_id": "D", "score": 0.3}, {"doc_id": "D", "score": 0.8}]) == {"D": 0.8}


def test_build_and_store_single_parent(tmp_path):
    doc = _Doc("d1", "문서1", "# 섹션A\n본문a\n## 섹션B\n본문b")
    chunks = [Chunk(chunk_id="d1#0", doc_id="d1", ordinal=0, text="본문a")]
    nodes = build_document_tree(doc, chunks)
    # document 1 + section 2 + chunk 1
    levels = sorted(n.level for n in nodes)
    assert levels == ["chunk", "document", "section", "section"]
    # 단일 부모: document만 parent_id None, 나머지는 document를 부모로
    doc_node = next(n for n in nodes if n.level == "document")
    assert doc_node.parent_id is None
    assert all(n.parent_id == doc_node.node_id for n in nodes if n.level != "document")
    # 저장(FK 충족, 멱등)
    store = TreeStore(tmp_path / "t.db")
    store.replace_doc_nodes("d1", nodes)
    store.replace_doc_nodes("d1", nodes)  # 2회 — 멱등
    conn = store.get_connection()
    cnt = conn.execute("SELECT count(*) FROM tree_nodes WHERE doc_id='d1'").fetchone()[0]
    assert cnt == len(nodes)
