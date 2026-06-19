#!/usr/bin/env python3
"""기존 Silver 문서의 hierarchy → document_categories 백필 (재임베딩 없이).

category 0% 원인은 데이터 부재가 아니라 normalizer의 hierarchy→category 변환 누락(19 §3.5).
이 스크립트는 기존 적재분을 경량 백필한다(신규 적재는 normalizer가 자동 처리).
사용: PYTHONPATH=src .venv/bin/python scripts/backfill_categories.py
"""
import sqlite3, json, time, os
from pathlib import Path

GDB = os.path.expanduser(os.getenv("GERYON_DB", str(Path.home() / ".geryon" / "geryon.db")))
con = sqlite3.connect(GDB)
t0 = time.perf_counter()
rows = con.execute("SELECT doc_id, hierarchy, category FROM documents").fetchall() \
    if "category" in [c[1] for c in con.execute("PRAGMA table_info(documents)")] \
    else con.execute("SELECT doc_id, hierarchy FROM documents").fetchall()
filled = skipped = 0
cur = con.cursor()
for r in rows:
    doc_id, hier = r[0], r[1]
    # 이미 category 있으면 건너뜀(meta 기반 우선)
    has = con.execute("SELECT 1 FROM document_categories WHERE doc_id=? LIMIT 1", (doc_id,)).fetchone()
    if has:
        skipped += 1; continue
    try:
        cats = list(dict.fromkeys(json.loads(hier or "[]")))
    except Exception:
        cats = []
    if not cats:
        continue
    cur.executemany("INSERT OR IGNORE INTO document_categories (doc_id, category) VALUES (?, ?)",
                    [(doc_id, c) for c in cats])
    filled += 1
con.commit()
total = con.execute("SELECT count(DISTINCT doc_id) FROM document_categories").fetchone()[0]
ndoc = con.execute("SELECT count(*) FROM documents").fetchone()[0]
print(f"백필 {filled}건 채움(기존 {skipped} skip) {time.perf_counter()-t0:.1f}s")
print(f"category 커버리지: {total}/{ndoc} ({total/ndoc*100:.0f}%)")
