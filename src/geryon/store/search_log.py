"""검색 행동 로그 — 질의와 그 뒤의 선택(문서 열람)을 로컬 DB에 남긴다.

## 왜 필요한가
오프라인 평가(골든셋)는 **문서에서 역생성한 합성 질의**라 실제 사용자 의도 분포와 다르다.
행동 로그가 있으면
  - 실사용 질의로 골든셋을 재구성할 수 있고,
  - 선택 랭크(1위를 골랐나, 5위를 골랐나)로 랭킹 품질을 직접 잴 수 있으며,
  - 클릭 신호를 static_score/개인화에 되먹일 수 있다.

## 원칙
- **로컬 전용**: 같은 SQLite 파일에 쓰고 외부로 전송하지 않는다.
- **비침습**: 로깅 실패가 검색을 막지 않는다(모든 예외를 삼킨다).
- **끌 수 있다**: `GERYON_SEARCH_LOG=0`.
- **질의문은 원문 저장**: 개인정보가 섞일 수 있으므로, 공유용 DB(`geryon publish`)를 만들 때는
  `purge_logs()` 로 지우거나 애초에 로깅을 끄고 색인할 것.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

# 프로세스 1회 기동 = 1 세션. MCP 서버는 클라이언트 세션당 프로세스가 뜨므로
# 이 단위로 "질의 → 선택" 을 묶으면 충분하다.
SESSION_ID = uuid.uuid4().hex[:16]

_LAST_SEARCH_ID: int | None = None
_LAST_TOP_IDS: list[str] = []


def enabled() -> bool:
    return os.getenv("GERYON_SEARCH_LOG", "1") not in ("0", "false", "False", "")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_search(conn: sqlite3.Connection, query: str, k: int,
               doc_ids: list[str], latency_ms: float, top_n: int = 10) -> int | None:
    """검색 1건 기록. 반환: search_log.id(실패 시 None)."""
    global _LAST_SEARCH_ID, _LAST_TOP_IDS
    if not enabled():
        return None
    try:
        cur = conn.execute(
            "INSERT INTO search_log (ts, session_id, query, k, n_results, top_doc_ids, latency_ms) "
            "VALUES (?,?,?,?,?,?,?)",
            (_now(), SESSION_ID, query, k, len(doc_ids),
             json.dumps(doc_ids[:top_n], ensure_ascii=False), latency_ms),
        )
        conn.commit()
        _LAST_SEARCH_ID = int(cur.lastrowid) if cur.lastrowid else None
        _LAST_TOP_IDS = list(doc_ids[:top_n])
        return _LAST_SEARCH_ID
    except Exception:
        return None  # 로깅 실패가 검색을 막지 않는다


def log_selection(conn: sqlite3.Connection, doc_id: str) -> None:
    """문서 열람(= 암묵적 클릭) 기록. 직전 검색과 연결하고 그 안에서의 순위를 남긴다."""
    if not enabled():
        return
    try:
        rank = (_LAST_TOP_IDS.index(doc_id) + 1) if doc_id in _LAST_TOP_IDS else None
        conn.execute(
            "INSERT INTO selection_log (ts, session_id, doc_id, search_id, rank) VALUES (?,?,?,?,?)",
            (_now(), SESSION_ID, doc_id, _LAST_SEARCH_ID, rank),
        )
        conn.commit()
    except Exception:
        return


def purge_logs(conn: sqlite3.Connection) -> int:
    """로그 전체 삭제 — 공유용 DB 배포 전 개인정보 제거용. 반환: 삭제 행 수."""
    n = 0
    for t in ("selection_log", "search_log"):
        try:
            n += conn.execute(f"DELETE FROM {t}").rowcount or 0
        except Exception:
            pass
    conn.commit()
    return n
