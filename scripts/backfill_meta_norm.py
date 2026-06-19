#!/usr/bin/env python3
"""기존 documents의 author·category 노이즈 정규화 백필(재임베딩 무관 — content_hash 미포함).

⚠ 대규모 DB 주의: documents 행이 크면(본문 포함) author UPDATE = 행 rewrite로 매우 느리다
(28k건/본문 1.8GB에서 수 분+). **검색·표시는 읽기 시점 정규화로 이미 처리**되므로
(advanced_search가 normalize_author 적용·매칭은 LIKE로 노이즈 무관), 이 백필은
소규모 DB나 영구 정제가 꼭 필요할 때만 쓴다. 신규 적재는 Normalizer가 정규화한다.
실행: PYTHONPATH=src python scripts/backfill_meta_norm.py
"""
import os, sys, sqlite3
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from geryon.index.meta_norm import normalize_author, normalize_category

def main():
    db = os.path.expanduser(os.environ.get("GERYON_DB", "~/.geryon/geryon.db"))
    con = sqlite3.connect(db)
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA synchronous=OFF")  # 1회성 백필 가속(fsync 생략)

    # author — 변경 행만 배치 UPDATE
    rows = con.execute("SELECT doc_id, author FROM documents WHERE author IS NOT NULL AND author<>''").fetchall()
    a_upd = [(normalize_author(a), d) for d, a in rows if normalize_author(a) != a]
    con.executemany("UPDATE documents SET author=? WHERE doc_id=?", a_upd)

    # category — 노이즈 행(#/따옴표)만 정규화
    crows = con.execute(
        "SELECT doc_id, category FROM document_categories "
        "WHERE category LIKE '#%' OR category LIKE '\"%' OR category LIKE \"'%\""
    ).fetchall()
    c_upd = 0
    for doc_id, cat in crows:
        nc = normalize_category(cat)
        con.execute("DELETE FROM document_categories WHERE doc_id=? AND category=?", (doc_id, cat))
        if nc:
            con.execute("INSERT OR IGNORE INTO document_categories (doc_id, category) VALUES (?,?)", (doc_id, nc))
        c_upd += 1

    con.commit()
    print(f"author 정규화 {len(a_upd)}건, category 정규화 {c_upd}건 백필 완료")

if __name__ == "__main__":
    main()
