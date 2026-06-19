"""상태/모니터링 수집. `geryon status`가 JSON으로 반환."""
import os
from pathlib import Path

from geryon.config import DB_PATH
from geryon.store.db import init_db


def collect_status(db_path: str | Path | None = None) -> dict:
    """문서/청크/벡터/트리/카테고리 수, by_source, DB 크기, 모델, sqlite_vec, 버전."""
    p = Path(db_path) if db_path else Path(DB_PATH)
    conn = init_db(p)

    def cnt(table: str):
        try:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        except Exception:
            return None

    try:
        by_source = {r[0]: r[1] for r in conn.execute(
            "SELECT source, count(*) FROM documents GROUP BY source")}
    except Exception:
        by_source = {}

    sqlite_vec_ok = True
    try:
        conn.execute("SELECT count(*) FROM chunk_embeddings")
    except Exception:
        sqlite_vec_ok = False

    version = None
    for vp in (Path("VERSION"), Path(__file__).resolve().parent.parent.parent / "VERSION"):
        if vp.exists():
            version = vp.read_text(encoding="utf-8").strip()
            break
    if not version:
        # 설치본엔 VERSION 파일이 없으므로 패키지 메타데이터로 폴백.
        try:
            from importlib.metadata import version as _pkg_version
            version = _pkg_version("geryonmcp")
        except Exception:
            version = None

    return {
        "documents": cnt("documents"),
        "chunks": cnt("chunks"),
        "chunk_embeddings": cnt("chunk_embeddings"),
        "tree_nodes": cnt("tree_nodes"),
        "categories": cnt("document_categories"),
        "by_source": by_source,
        "db_size_bytes": os.path.getsize(p) if os.path.exists(p) else 0,
        "model": "intfloat/multilingual-e5-small",
        "sqlite_vec": sqlite_vec_ok,
        "version": version,
    }
