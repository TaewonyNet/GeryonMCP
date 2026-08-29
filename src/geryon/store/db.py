import sqlite3
from pathlib import Path
from collections.abc import Generator
from contextlib import contextmanager

from geryon.config import DB_PATH, ensure_directories

SCHEMA_VERSION = 8

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS documents (
        doc_id TEXT PRIMARY KEY,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        space_or_repo TEXT,
        url TEXT,
        title TEXT NOT NULL,
        body_markdown TEXT NOT NULL,
        summary TEXT,
        tags TEXT NOT NULL, -- JSON array
        hierarchy TEXT NOT NULL, -- JSON array
        author TEXT,
        created_at TEXT,
        updated_at TEXT,
        content_hash TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        raw_meta TEXT NOT NULL, -- JSON object
        static_score REAL NOT NULL DEFAULT 0.0, -- 인덱싱-시 사전계산 품질점수
        UNIQUE (source, source_id)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_documents_space_or_repo ON documents(space_or_repo);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_documents_author ON documents(author);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_documents_updated_at ON documents(updated_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash);
    """,
    """
    CREATE TABLE IF NOT EXISTS attachments (
        doc_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        media_type TEXT,
        local_path TEXT,
        url TEXT,
        FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_attachments_doc_id ON attachments(doc_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS document_tags (
        doc_id TEXT NOT NULL,
        tag TEXT NOT NULL,
        FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_tags_doc_id_tag ON document_tags(doc_id, tag);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_document_tags_tag ON document_tags(tag);
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
        doc_id,
        title,
        body_markdown,
        tokenize='porter unicode61'
    );
    """,
    # Synchronization triggers
    """
    CREATE TRIGGER IF NOT EXISTS trg_documents_insert AFTER INSERT ON documents
    BEGIN
        INSERT INTO documents_fts (doc_id, title, body_markdown)
        VALUES (new.doc_id, new.title, new.body_markdown);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_documents_update AFTER UPDATE ON documents
    BEGIN
        UPDATE documents_fts
        SET title = new.title,
            body_markdown = new.body_markdown
        WHERE doc_id = new.doc_id;
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_documents_delete AFTER DELETE ON documents
    BEGIN
        DELETE FROM documents_fts WHERE doc_id = old.doc_id;
    END;
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id TEXT PRIMARY KEY,
        doc_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        text TEXT NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS chunk_embeddings USING vec0(
        chunk_id TEXT,
        embedding float[384]
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_state (
        source TEXT PRIMARY KEY,
        last_synced_at TEXT,
        cursor TEXT,
        doc_count INTEGER DEFAULT 0
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_seen_docs (
        source TEXT NOT NULL,
        doc_id TEXT NOT NULL,
        PRIMARY KEY (source, doc_id)
    );
    """
]

# 신규 테이블 (tree_nodes / document_categories)
_V4_DDL = [
    """
    CREATE TABLE IF NOT EXISTS tree_nodes (
        node_id   TEXT PRIMARY KEY,
        parent_id TEXT,
        doc_id    TEXT,
        level     TEXT NOT NULL,           -- 'space'|'path'|'document'|'section'|'chunk'
        title     TEXT NOT NULL,
        summary TEXT, -- 추출식 요약, ≤280자
        score     REAL NOT NULL DEFAULT 0,
        path      TEXT NOT NULL,
        FOREIGN KEY (parent_id) REFERENCES tree_nodes(node_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tree_parent ON tree_nodes(parent_id);",
    "CREATE INDEX IF NOT EXISTS idx_tree_doc ON tree_nodes(doc_id);",
    "CREATE INDEX IF NOT EXISTS idx_tree_level ON tree_nodes(level);",
    """
    CREATE TABLE IF NOT EXISTS document_categories (
        doc_id   TEXT NOT NULL,
        category TEXT NOT NULL, -- 자유 문자열 다중값
        PRIMARY KEY (doc_id, category),
        FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_doc_cat_category ON document_categories(category);",
]
# 정적 품질점수 사전계산 (static_score) + page_links
_V5_DDL = [
    # ② static_score: documents 테이블에 컬럼 추가 (ALTER TABLE — 기존 DB에 안전)
    # SQLite ALTER TABLE ADD COLUMN은 멱등하지 않으므로 Python에서 예외 무시로 처리
    # (여기서는 마이그레이션 함수에서 try/except로 처리)
    """
    CREATE TABLE IF NOT EXISTS page_links (
        src_page_id TEXT NOT NULL,
        dst_page_id TEXT NOT NULL,
        PRIMARY KEY (src_page_id, dst_page_id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_page_links_src ON page_links(src_page_id);",
    "CREATE INDEX IF NOT EXISTS idx_page_links_dst ON page_links(dst_page_id);",
]

# v6: 키워드 FTS 토크나이저 trigram → porter unicode61.
# 측정: 키워드 단독 expected_title 22.6%→54.8%. 기존 DB는 FTS만 재구축(재임베딩 불필요).
# DROP+CREATE+repopulate는 데이터 작업이라 _apply_migrations에서 특수 처리.
_V6_DDL: list[str] = []

# v7: 한국어 조사 정규화 검색 FTS(index/korean, 19 §3.6). 색인·쿼리 양쪽 조사 제거로
# 토큰 일치 → 조사형 +13%p·원본 +3%p·속도↑(재검증). 트리거 불가(정규화 함수 필요)라
# repository.upsert가 수동 관리, 기존 DB는 _apply_migrations에서 백필.
_V7_DDL = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts_norm USING fts5("
    "doc_id, ntitle, nbody, tokenize='porter unicode61');",
]

# 신규 DB는 처음부터 포함
# v8: 검색 행동 로그(질의 → 선택). 오프라인 평가의 근본 한계를 푸는 유일한 신호원.
#   합성 골든셋은 "문서에서 역생성한 질의"라 실제 사용자 의도 분포와 다르다(covariate shift).
#   MCP 는 search → get_document 호출이 자연스럽게 "질의 → 선택"이라 암묵적 클릭을 공짜로 얻는다.
#   전부 로컬 파일에 남고 외부로 나가지 않는다. GERYON_SEARCH_LOG=0 으로 끌 수 있다.
_V8_DDL = [
    """
    CREATE TABLE IF NOT EXISTS search_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts           TEXT NOT NULL,          -- ISO8601 UTC
        session_id   TEXT,                   -- 프로세스 단위 세션(질의→선택 연결용)
        query        TEXT NOT NULL,
        k            INTEGER,
        n_results    INTEGER,
        top_doc_ids  TEXT,                   -- JSON 배열(상위 N개) — 선택 랭크 계산용
        latency_ms   REAL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_search_log_ts ON search_log(ts);",
    """
    CREATE TABLE IF NOT EXISTS selection_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts           TEXT NOT NULL,
        session_id   TEXT,
        doc_id       TEXT NOT NULL,
        search_id    INTEGER,                -- 직전 search_log.id (있으면)
        rank         INTEGER,                -- 그 검색 결과에서의 순위(1-base, 없으면 NULL)
        FOREIGN KEY (search_id) REFERENCES search_log(id)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_selection_log_doc ON selection_log(doc_id);",
]

DDL_STATEMENTS = DDL_STATEMENTS + _V4_DDL + _V5_DDL + _V6_DDL + _V7_DDL + _V8_DDL

# 버전별 증분 마이그레이션 (idempotent — 모든 문은 IF NOT EXISTS)
MIGRATIONS: dict[int, list[str]] = {
    4: _V4_DDL,
    5: _V5_DDL,
    6: _V6_DDL,
    7: _V7_DDL,
    8: _V8_DDL,
}


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """테이블에 컬럼이 존재하는지 확인."""
    rows = conn.execute(f"PRAGMA table_info({table});").fetchall()
    return any(r[1] == column for r in rows)


def _apply_migrations(conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
    """from_version+1 .. to_version 까지 순차 적용 후 schema_version 기록."""
    for v in range(from_version + 1, to_version + 1):
        for stmt in MIGRATIONS.get(v, []):
            _ = conn.execute(stmt)
        # v5: static_score 컬럼 추가 (ALTER TABLE은 IF NOT EXISTS 미지원이라 별도 처리)
        if v == 5:
            # documents 테이블이 존재할 때만 컬럼 추가 (없으면 신규 DDL에 이미 포함)
            table_exists = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()[0]
            if table_exists and not _column_exists(conn, "documents", "static_score"):
                _ = conn.execute(
                    "ALTER TABLE documents ADD COLUMN static_score REAL NOT NULL DEFAULT 0.0;"
                )
        # v6: documents_fts 토크나이저 교체(trigram→porter unicode61) — FTS 재구축.
        # 트리거는 테이블명을 참조하므로 DROP/CREATE 후에도 유효. 재임베딩 불필요.
        if v == 6:
            docs_exists = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()[0]
            _ = conn.execute("DROP TABLE IF EXISTS documents_fts;")
            _ = conn.execute(
                """
                CREATE VIRTUAL TABLE documents_fts USING fts5(
                    doc_id, title, body_markdown, tokenize='porter unicode61'
                );
                """
            )
            if docs_exists:
                _ = conn.execute(
                    "INSERT INTO documents_fts (doc_id, title, body_markdown) "
                    "SELECT doc_id, title, body_markdown FROM documents;"
                )
        # v7: 조사 정규화 FTS 백필 — 기존 documents를 normalize 해 documents_fts_norm 채움(재임베딩 불필요).
        if v == 7:
            docs_exists = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()[0]
            # 구버전/PoC 잔여 테이블(스키마 불일치) 대비 DROP+CREATE로 정합 보장
            _ = conn.execute("DROP TABLE IF EXISTS documents_fts_norm;")
            _ = conn.execute(
                "CREATE VIRTUAL TABLE documents_fts_norm USING fts5("
                "doc_id, ntitle, nbody, tokenize='porter unicode61');"
            )
            if docs_exists:
                from geryon.index.korean import normalize_korean
                rows = conn.execute("SELECT doc_id, title, body_markdown FROM documents").fetchall()
                conn.executemany(
                    "INSERT INTO documents_fts_norm (doc_id, ntitle, nbody) VALUES (?, ?, ?);",
                    [(d, normalize_korean(t), normalize_korean(b)) for d, t, b in rows],
                )
        _ = conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'));",
            (v,),
        )
    conn.commit()


def init_db(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    """Initialize database and run idempotent migrations."""
    db_path = Path(db_path)
    # Ensure directories exist
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    
    try:
        conn.enable_load_extension(True)
        import sqlite_vec  # type: ignore[import-untyped]
        sqlite_vec.load(conn)
    except Exception:
        try:
            conn.enable_load_extension(True)
            conn.load_extension("vec0")
        except Exception:
            pass

    # Enable foreign keys
    _ = conn.execute("PRAGMA foreign_keys = ON;")
    
    # Check if schema_version exists
    cursor = conn.cursor()
    _ = cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version';")
    version_table_exists = cursor.fetchone() is not None
    
    if not version_table_exists:
        # Create schema_version first
        _ = conn.execute(DDL_STATEMENTS[0])
        # Execute all DDL statements
        for stmt in DDL_STATEMENTS[1:]:
            _ = conn.execute(stmt)
        # v5: static_score 컬럼은 DDL에 이미 포함 (신규 DB는 자동으로 생성됨)
        # Initialize version
        _ = conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'));",
            (SCHEMA_VERSION,)
        )
        conn.commit()
    else:
        # Check current version
        _ = cursor.execute("SELECT max(version) FROM schema_version;")
        row = cursor.fetchone()
        current_version = int(row[0]) if row and row[0] is not None else 0
        
        # 버전별 증분 마이그레이션 적용 — 기존 no-op 재실행을 대체
        if current_version < SCHEMA_VERSION:
            _apply_migrations(conn, current_version, SCHEMA_VERSION)
            
    return conn

@contextmanager
def get_db_connection(db_path: str | Path = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite connection with foreign keys enabled."""
    ensure_directories()
    conn = init_db(db_path)
    try:
        yield conn
    finally:
        conn.close()
