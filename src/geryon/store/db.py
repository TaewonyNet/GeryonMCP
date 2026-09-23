import sqlite3
from pathlib import Path
from collections.abc import Generator
from contextlib import contextmanager

from geryon.config import DB_PATH, ensure_directories

SCHEMA_VERSION = 9

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
    -- ⚠️ WHEN 가드 필수. 없으면 `static_score` 같은 «본문과 무관한» 컬럼만 바꿔도
    --    FTS 본문이 통째로 재색인된다. 실측(2026-09-20): 수만 행 점수 갱신이
    --    10분을 넘겼고, 계측 결과 그 시간이 전부 이 트리거였다(앞단은 1초 미만).
    --    같은 정의가 _V9_DDL 에도 있다(기존 DB 교체용) — 고칠 때 둘 다 고칠 것.
    CREATE TRIGGER IF NOT EXISTS trg_documents_update AFTER UPDATE ON documents
        WHEN new.title IS NOT old.title
          OR new.body_markdown IS NOT old.body_markdown
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

# v9: 색인 갱신 비용 두 건을 고친다. 둘 다 2026-09-20 실측으로 드러났다.
#
# ① `documents(source_id)` 색인 — 없어서 O(N²) 였다.
#    제약이 `UNIQUE(source, source_id)` 라 `WHERE source_id = ?` 는 복합 색인의
#    선두 컬럼이 아니어서 못 탄다(EXPLAIN: `SCAN documents`). 그런데
#    `repository.update_static_scores` 가 문서마다 그 UPDATE 를 돈다 —
#    실측 수만 문서에서 문서 수만큼의 전체 스캔 ≈ 수십억 행 방문.
#
# ② `trg_documents_update` 에 WHEN 가드 — 없어서 «점수만 바꿔도 본문이 재색인»됐다.
#    트리거가 UPDATE 종류를 가리지 않고 `documents_fts` 의 title/body 를 다시 쓴다.
#    `static_score` 한 컬럼만 바꾸는 배치가 FTS 전체 재색인을 유발했고,
#    계측 결과 재계산 600초 중 **앞단 전부가 1초 미만, 나머지 전부가 이 트리거**였다.
#    제목·본문이 실제로 바뀐 경우에만 FTS 를 건드리게 한다.
#
# ⚠️ 트리거는 `IF NOT EXISTS` 로는 교체되지 않는다. DROP 후 재생성해야 한다.
_V9_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_documents_source_id ON documents(source_id);",
    "DROP TRIGGER IF EXISTS trg_documents_update;",
    """
    CREATE TRIGGER trg_documents_update AFTER UPDATE ON documents
        WHEN new.title IS NOT old.title
          OR new.body_markdown IS NOT old.body_markdown
        BEGIN
            UPDATE documents_fts
            SET title = new.title,
                body_markdown = new.body_markdown
            WHERE doc_id = new.doc_id;
        END;
    """,
]

DDL_STATEMENTS = DDL_STATEMENTS + _V4_DDL + _V5_DDL + _V6_DDL + _V7_DDL + _V8_DDL + _V9_DDL

# 버전별 증분 마이그레이션 (idempotent — 모든 문은 IF NOT EXISTS)
MIGRATIONS: dict[int, list[str]] = {
    4: _V4_DDL,
    5: _V5_DDL,
    6: _V6_DDL,
    7: _V7_DDL,
    8: _V8_DDL,
    # ⚠️ v9 는 여기 두지 않는다. 문장이 전부 `documents` 를 참조하는데, 아주 옛
    #    버전(v3 이하)에서 올라오는 DB 는 그 시점에 `documents` 가 없을 수 있다
    #    (`tests/test_db.py::test_migration_v3_to_latest` 가 그 경우를 모사한다).
    #    v5·v6·v7 과 같은 선례대로 `_apply_migrations` 에서 테이블 존재를 확인한
    #    뒤 실행한다. 신규 DB 는 `DDL_STATEMENTS` 에 포함돼 그대로 만들어진다.
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
        # v9: documents(source_id) 색인 + trg_documents_update 의 WHEN 가드.
        #     둘 다 `documents` 를 참조하므로 테이블이 있을 때만 적용한다.
        if v == 9:
            docs_exists = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()[0]
            if docs_exists:
                for stmt in _V9_DDL:
                    _ = conn.execute(stmt)
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
