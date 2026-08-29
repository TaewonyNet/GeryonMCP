#!/usr/bin/env python3
"""Term Contract 부트스트랩 — DB(들)의 문서·코드에서 "용어 정의" 후보를 추출해 초안 생성.

    python scripts/term_bootstrap.py <db1[,db2,...]> [--out terms.draft.yml] [--min-def-len 8]

dict_bootstrap.py(동의어 사전)와 짝을 이루는 스크립트. 동의어 사전은 "같은 뜻의 다른 표현"을
잡고, 이건 "이 용어가 정확히 뭔지(정의)"를 잡는다 — semantic layer / term contract 초안.

추출(신뢰도 순):
  - sql_comment    : DDL `컬럼명 ... COMMENT '설명'` → 코드가 못박은 정의(가장 신뢰도 높음)
  - title_exact    : 제목이 그 자체로 짧은 용어인 문서(글머리 정의형) → 본문 첫 문단을 정의로
  - definition_sent: 본문 중 "X는/이란/= ~" 패턴 문장 → 정의 후보(신뢰도 낮음, noise 있음)

같은 term에 서로 다른 정의가 여럿이면 자동으로 하나를 고르지 않고 conflicting으로 표시한다
(누가 공식인지는 결국 사람이 정해야 한다 — 자동화로 못 푸는 지점).

반자동: 초안 생성 후 사람이 검수해 terms.yaml 로 확정. dict_bootstrap.py 와 동일한
scan → build_entries → merge/write 흐름, deny-list 로 오수집 영구 제외.
"""
import argparse
import collections
import re
import sqlite3
from pathlib import Path

# dict_bootstrap.py 와 공유하는 노이즈 필터(흔한 한글 명사·공통 약어) — 여기서도 term 후보 배제용
_STOP_KO = {
    "확인", "요청", "작성", "제목", "구분", "내용", "등록", "수정", "삭제", "목록", "정보",
    "관리", "설정", "사용", "처리", "이름", "담당", "상태", "추가", "개선", "완료", "진행",
    "생성", "변경", "대상", "기준", "결과", "오류", "문제", "방법", "관련", "필요",
}
# 범용 컬럼명 — 테이블마다 뜻이 다른 게 정상(예: name 은 도시명일 수도 마크업명일 수도 있음).
# term 을 테이블에 스코프하지 않는 한(지금은 안 함) 전역 term 으로 다루면 "충돌"이 대량 오탐된다.
# → sql_comment/definition_sentence 의 term 후보에서 아예 배제(테이블 스코프 지원 전까지).
_GENERIC_COLUMNS = {
    "id", "name", "type", "value", "data", "url", "code", "status", "rank", "level",
    "description", "count", "date", "time", "created_at", "updated_at", "modified_at",
    "key", "label", "title", "text", "flag", "reason", "note", "memo", "amount", "price",
    "medium", "source", "target", "result", "message", "content", "comment",
}
# 회의록/일지/번호매긴 섹션제목 패턴 — title_exact 후보에서 제외(용어 정의 문서가 아님)
_NON_GLOSSARY_TITLE = re.compile(
    r"^\d{1,8}[\s.\-_)]|회의록|미팅|주간|일일|daily|weekly|회고|스프린트|sprint|경과|보고서?$"
    r"|^[A-Z]\.\s|^사본|^Copy of",
    re.IGNORECASE,
)
# 본문 시작이 인사말/서명형이면 정의가 아니라 안내문 — 배제
_GREETING_START = re.compile(r"^(안녕하세요|안녕하십니까|Hi[,\s]|Hello[,\s]|여러분)")

# 한 줄 안에서만 매칭(줄바꿈 넘어가면 DDL 여러 줄이 섞여 엉뚱한 토큰을 잡음) — [^\n]로 줄 경계 고정
_SQL_COMMENT = re.compile(
    r"^\s*[`\"']?([a-z_][a-z0-9_]{2,40})[`\"']?\s+[A-Za-z][^\n']{0,60}?COMMENT\s+'([^']{2,120})'",
    re.IGNORECASE | re.MULTILINE,
)
# SQL 예약어·타입명 — 컬럼명 자리에 우연히 걸리는 것 제외
_SQL_KEYWORDS = {
    "collate", "default", "not", "null", "character", "set", "primary", "key", "unique",
    "engine", "unsigned", "auto_increment", "timestamp", "varchar", "int", "bigint",
    "decimal", "text", "else", "then", "when", "case", "end", "as", "from", "where",
    "select", "insert", "update", "delete", "table", "index", "constraint", "references",
    "utf8mb4_unicode_ci", "utf8mb4_general_ci", "utf8_general_ci", "innodb", "float", "double",
    "datetime", "date", "tinyint", "smallint", "boolean", "bool", "json", "blob",
    "add", "alter", "column", "modify", "change", "drop", "comment",
}
# 대량 DW 테이블에 관용적으로 반복되는 감사(audit) 컬럼 — 표현만 살짝씩 다를 뿐 실질 충돌 아님.
# 대소문자·표기 변형이 많아 별도 정규화 없이 소문자 비교로 넉넉히 배제.
_AUDIT_COLUMNS = {
    "reg_date", "reg_id", "regist_dt", "regdt", "upd_date", "upd_id", "update_dt",
    "mod_date", "mod_id", "modify_dt", "del_yn", "use_yn", "ins_date", "ins_id",
}
_DEF_SENTENCE = re.compile(
    r"(?:^|\n)\s*([A-Za-z0-9_.]{2,24}|[가-힣]{2,12})\s*(?:는|은|이란|:)\s*([^\n]{10,140})"
)
_MD_HEADER = re.compile(r"^#{1,6}\s*.*$", re.MULTILINE)
# title_exact 정의가 실제 산문(prose)인지 — 표/경로/파일명 등 fragment 배제
_PROSE_END = re.compile(r"(다|음|함|입니다|이다|한다|된다|있다|없다|한다\.)[.\s]*$")
_TABLE_OR_PATH = re.compile(r"^\s*\||^[A-Za-z0-9_\-./\\]+\.[A-Za-z]{2,5}\s*$")


def _clean_body_prefix(body: str, limit: int = 220) -> str:
    """본문 시작의 마크다운 헤더·빈줄을 걷어내고 첫 문단만 정의 후보로."""
    text = _MD_HEADER.sub("", body or "").strip()
    # 첫 문단(빈 줄 전까지)
    para = text.split("\n\n", 1)[0].strip()
    return para[:limit]


def _norm_term(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip())


def _is_glossary_title(title: str) -> bool:
    t = (title or "").strip()
    if not t or len(t) > 40:
        return False
    if _NON_GLOSSARY_TITLE.search(t):
        return False
    words = t.split()
    if len(words) > 5:
        return False
    if t in _STOP_KO:
        return False
    return True


def scan(db_paths: list[str]) -> list[dict]:
    """각 후보를 개별 레코드로 수집(병합은 build_entries 에서)."""
    candidates: list[dict] = []
    for db in db_paths:
        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT title, body_markdown, url, source, space_or_repo, author, updated_at "
            "FROM documents"
        )
        for title, body, url, source, space, author, updated_at in rows:
            body = body or ""

            # 1) sql_comment — 코드/DDL 한 줄 안에서만(신뢰도 최고)
            for m in _SQL_COMMENT.finditer(body):
                ident, comment = m.group(1), m.group(2).strip()
                if ident.lower() in _SQL_KEYWORDS or ident.lower() in _GENERIC_COLUMNS or ident.lower() in _AUDIT_COLUMNS:
                    continue
                candidates.append({
                    "term": _norm_term(ident), "definition": comment, "url": url,
                    "owner": author, "last_verified": updated_at, "source_type": "sql_comment",
                    "confidence": "high", "space_or_repo": space,
                })

            # 2) title_exact — 제목 자체가 용어인 글로서리형 문서. 정의는 반드시 산문(prose)이어야
            #    함(표/파일경로/버전이력 표 헤더 같은 fragment는 정의가 아니라 노이즈).
            if _is_glossary_title(title):
                definition = _clean_body_prefix(body)
                if (len(definition) >= 12 and not _TABLE_OR_PATH.match(definition)
                        and not _GREETING_START.match(definition)
                        and _PROSE_END.search(definition) and " " in definition):
                    candidates.append({
                        "term": _norm_term(title), "definition": definition, "url": url,
                        "owner": author, "last_verified": updated_at, "source_type": "title_exact",
                        "confidence": "medium", "space_or_repo": space,
                    })

            # 3) definition_sentence — 본문 중 정의형 문장(신뢰도 낮음)
            for m in _DEF_SENTENCE.finditer(body[:4000]):  # 문서당 앞부분만(비용 제한)
                term, definition = _norm_term(m.group(1)), m.group(2).strip()
                if term in _STOP_KO or len(term) < 2 or term.lower() in _GENERIC_COLUMNS:
                    continue
                candidates.append({
                    "term": term, "definition": definition, "url": url,
                    "owner": author, "last_verified": updated_at, "source_type": "definition_sentence",
                    "confidence": "low", "space_or_repo": space,
                })
        conn.close()
    return candidates


def load_deny(path: str) -> set:
    import yaml
    p = Path(path)
    if not p.exists():
        return set()
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return set(d.get("terms") or [])
    except Exception:
        return set()


_PRIORITY = {"sql_comment": 0, "title_exact": 1, "definition_sentence": 2}


# 임계값은 손으로 고른 값이 아니라 calibrate_conflict.py 로 **기계 보정**한 값이다.
# 정답셋은 DDL 구조에서 유도(같은 테이블·같은 컬럼=같은 개념 / 같은 테이블·다른 컬럼=다른 개념),
# 격자탐색 F1 최댓값이 jaccard=0.10 이었다(현행 0.25 대비 F1 0.734→0.791).
# containment 분기는 보정 데이터에서 12회 발동했으나 **판정을 한 번도 바꾸지 않아** 사실상 무효였다
# — 임계값을 무엇으로 놓든 F1이 동일했다. 남겨두되 그 사실을 명시한다(재보정 시 재확인 대상).
# 재보정: python scripts/calibrate_conflict.py <db>
_CONFLICT_CONTAINMENT_TH = 0.7   # (보정 결과: 무효 파라미터 — 결정에 영향 없음)
_CONFLICT_JACCARD_TH = 0.10      # (보정 결과: F1 최적)


def _defs_conflict(a: str, b: str) -> bool:
    """두 정의 텍스트가 실질적으로 다른가(True=충돌). 임계값은 위 보정 상수 사용."""
    ta, tb = set(re.findall(r"[가-힣A-Za-z0-9]+", a.lower())), set(re.findall(r"[가-힣A-Za-z0-9]+", b.lower()))
    if not ta or not tb:
        return False
    smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    containment = len(smaller & larger) / len(smaller)
    if containment >= _CONFLICT_CONTAINMENT_TH:
        return False
    jaccard = len(ta & tb) / len(ta | tb)
    return jaccard < _CONFLICT_JACCARD_TH


def build_entries(candidates: list[dict], min_freq: int, deny_terms: set = frozenset()) -> list[dict]:
    by_term: dict[str, list[dict]] = collections.defaultdict(list)
    for c in candidates:
        if c["term"] in deny_terms:
            continue
        by_term[c["term"]].append(c)

    entries: list[dict] = []
    for term, cands in by_term.items():
        if len(cands) < min_freq:
            continue
        cands.sort(key=lambda c: _PRIORITY.get(c["source_type"], 9))
        primary = cands[0]
        primary_rank = _PRIORITY.get(primary["source_type"], 9)

        # 서로 다른 출처의 정의가 실질적으로 갈리면 충돌로 표시(자동 확정 안 함).
        # 단, primary보다 신뢰도 낮은 후보(예: sql_comment 옆의 definition_sentence 노이즈)는
        # 충돌로 카운트하지 않는다 — 낮은 신뢰도가 높은 신뢰도를 "흔들게" 두지 않는다.
        conflicts = []
        seen_defs = {primary["definition"]}
        for c in cands[1:]:
            if _PRIORITY.get(c["source_type"], 9) > primary_rank:
                continue
            if any(_defs_conflict(c["definition"], d) for d in seen_defs):
                conflicts.append({
                    "definition": c["definition"], "url": c["url"],
                    "source_type": c["source_type"], "owner": c["owner"],
                })
                seen_defs.add(c["definition"])

        entries.append({
            "term": term,
            "status": "draft",
            "definition": primary["definition"],
            "source_doc": primary["url"],
            "owner": primary["owner"],
            "last_verified": primary["last_verified"],
            "source_type": primary["source_type"],
            "confidence": primary["confidence"],
            "space_or_repo": primary["space_or_repo"],
            "conflicting_definitions": conflicts,
            "_candidate_count": len(cands),
        })
    entries.sort(key=lambda e: (0 if e["conflicting_definitions"] else 1, _PRIORITY.get(e["source_type"], 9), -e["_candidate_count"]))
    return entries


def main() -> None:
    ap = argparse.ArgumentParser(description="Term Contract 부트스트랩(초안)")
    ap.add_argument("dbs", help="DB 경로(콤마로 여러 개)")
    ap.add_argument("--out", default="terms.draft.yml")
    ap.add_argument("--min-freq", type=int, default=1, help="term 당 최소 후보 수(기본 1)")
    ap.add_argument("--deny", default=str(Path.home() / ".geryon" / "gold" / "terms.deny.yaml"))
    ap.add_argument("--limit", type=int, default=200, help="출력 상위 N개(신뢰도·충돌 우선 정렬)")
    a = ap.parse_args()

    db_paths = [p.strip() for p in a.dbs.split(",") if p.strip()]
    deny_terms = load_deny(a.deny)
    print(f"스캔 중: {len(db_paths)}개 DB...")
    candidates = scan(db_paths)
    print(f"원시 후보: {len(candidates)}건")
    entries = build_entries(candidates, a.min_freq, deny_terms)
    entries = entries[: a.limit]

    import yaml
    Path(a.out).write_text(yaml.safe_dump(entries, allow_unicode=True, sort_keys=False), encoding="utf-8")

    by_type = collections.Counter(e["source_type"] for e in entries)
    n_conflict = sum(1 for e in entries if e["conflicting_definitions"])
    print(f"\nTerm 초안 {len(entries)}건 → {a.out}")
    print(f"  출처별: {dict(by_type)}")
    print(f"  충돌(정의 2개 이상 불일치): {n_conflict}건 — 사람 검수 우선순위")
    print(f"  설치: cp {a.out} ~/.geryon/gold/terms.yaml  (검수 후)")


if __name__ == "__main__":
    main()
