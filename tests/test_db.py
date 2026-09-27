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


# ═══════════════════════════════════ v9 — 색인 누락·트리거 과잉 (2026-09-20)

def test_v9_source_id_색인이_생기고_UPDATE가_스캔하지_않는다(tmp_path):
    """`UNIQUE(source, source_id)` 는 `WHERE source_id=?` 를 못 탄다.

    그래서 `update_static_scores` 가 문서마다 전체 스캔을 돌았다 —
    실측 수만 문서에서 O(N²). 색인이 사라지면 이 시험이 잡는다.
    """
    conn = init_db(tmp_path / "v9a.db")
    plan = " ".join(
        str(r[-1]) for r in conn.execute(
            "EXPLAIN QUERY PLAN UPDATE documents SET static_score=0 WHERE source_id=?", ("x",)
        )
    )
    assert "SCAN" not in plan, f"source_id UPDATE 가 전체 스캔이다: {plan}"
    assert "idx_documents_source_id" in plan
    conn.close()


# ═══════════════════════════════════ v10 — 쓰이지 않는 FTS(documents_fts) 제거

def _objects(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}


def test_v10_신규_DB에는_documents_fts와_트리거가_없다(tmp_path):
    conn = init_db(tmp_path / "v10a.db")
    o = _objects(conn)
    assert "documents_fts" not in o
    assert not {"trg_documents_insert", "trg_documents_update", "trg_documents_delete"} & o
    assert "documents_fts_norm" in o          # 검색이 실제로 쓰는 FTS 는 그대로
    conn.close()


def test_v10_업그레이드가_documents_fts를_지우고_검색_데이터는_남긴다(tmp_path):
    """v9 DB(옛 FTS·트리거·삭제 문서가 남긴 _norm 행 포함) → 최신."""
    from geryon.store.db import _apply_migrations
    db = tmp_path / "v9.db"
    conn = init_db(db)
    conn.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(doc_id, title, body_markdown)")
    conn.execute("CREATE TRIGGER trg_documents_insert AFTER INSERT ON documents BEGIN "
                 "INSERT INTO documents_fts VALUES (new.doc_id, new.title, new.body_markdown); END;")
    conn.execute("INSERT INTO documents (doc_id, source, source_id, title, body_markdown, tags, hierarchy, "
                 "content_hash, ingested_at, raw_meta) "
                 "VALUES ('d1','confluence','1','t','b','[]','[]','h',datetime('now'),'{}')")
    conn.execute("INSERT INTO documents_fts_norm VALUES ('d1','t','b')")
    conn.execute("INSERT INTO documents_fts_norm VALUES ('gone','t','b')")   # 삭제된 문서의 흔적
    conn.execute("DELETE FROM schema_version WHERE version >= 10")
    conn.commit()
    conn.close()

    conn = init_db(db)
    o = _objects(conn)
    assert "documents_fts" not in o and "trg_documents_insert" not in o
    assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    assert [r[0] for r in conn.execute("SELECT doc_id FROM documents_fts_norm")] == ["d1"]
    assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_v9_는_documents가_없어도_마이그레이션을_깨지_않는다(tmp_path):
    """아주 옛 DB(v3 이하)는 이 시점에 `documents` 가 없을 수 있다.

    v9 문장이 전부 documents 를 참조하므로 가드 없이 넣으면 마이그레이션이
    통째로 터진다(실제로 터졌다).
    """
    db = tmp_path / "v3only.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);")
    c.execute("INSERT INTO schema_version VALUES (3, datetime('now'));")
    c.commit()
    c.close()
    conn = init_db(db)   # 여기서 예외가 나면 실패
    assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()
