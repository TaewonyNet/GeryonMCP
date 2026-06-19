import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geryon.store.db import init_db, SCHEMA_VERSION  # noqa: E402


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_db_has_new_tables(tmp_path):
    conn = init_db(tmp_path / "fresh.db")
    t = _tables(conn)
    assert {"tree_nodes", "document_categories"} <= t
    assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_migration_v3_to_latest(tmp_path):
    db = tmp_path / "old.db"
    # v3 상태 모사: schema_version=3, 신규 테이블 없음
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);")
    c.execute("INSERT INTO schema_version VALUES (3, datetime('now'));")
    c.commit()
    c.close()
    conn = init_db(db)  # 마이그레이션 트리거
    # v4 테이블 포함 확인
    assert {"tree_nodes", "document_categories"} <= _tables(conn)
    # v5 테이블 포함 확인 (page_links)
    assert "page_links" in _tables(conn)
    # 최신 버전으로 업그레이드
    assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_migration_v4_to_v5(tmp_path):
    """v4 DB(documents 테이블 존재)에서 v5 마이그레이션: static_score 컬럼 + page_links 테이블 추가."""
    import sqlite_vec
    db = tmp_path / "v4.db"
    # v4 상태 모사: documents 테이블 있고 static_score 없음
    c = sqlite3.connect(db)
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    # 최소한의 v4 스키마
    c.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);")
    c.execute("INSERT INTO schema_version VALUES (4, datetime('now'));")
    c.execute("""CREATE TABLE documents (
        doc_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
        space_or_repo TEXT, url TEXT, title TEXT NOT NULL, body_markdown TEXT NOT NULL,
        summary TEXT, tags TEXT NOT NULL, hierarchy TEXT NOT NULL, author TEXT,
        created_at TEXT, updated_at TEXT, content_hash TEXT NOT NULL,
        ingested_at TEXT NOT NULL, raw_meta TEXT NOT NULL,
        UNIQUE (source, source_id)
    );""")
    c.commit()
    c.close()
    conn = init_db(db)
    # static_score 컬럼 추가 확인
    cols = [r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()]
    assert "static_score" in cols
    # page_links 테이블 추가 확인
    assert "page_links" in _tables(conn)
    assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_migration_idempotent(tmp_path):
    db = tmp_path / "idem.db"
    init_db(db).close()      # 1회
    conn = init_db(db)       # 2회 — 에러/중복 테이블 없어야
    tn = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name='tree_nodes'")]
    assert len(tn) == 1
    conn.close()
