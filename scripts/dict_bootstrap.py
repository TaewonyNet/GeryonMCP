#!/usr/bin/env python3
"""사전 부트스트랩 — DB(들)의 제목·본문에서 동의어 후보를 추출해 사전 초안 생성.

    python scripts/dict_bootstrap.py <db1[,db2,...]> [--out dict.draft.yml] [--min 5]

추출:
  - 괄호 병기  영문(한글) / 한글(영문)  → term ↔ synonym (정확도 중, noise 필터)
  - 약어 빈출  대문자 2~5자(일반 약어 제외) → 도메인 용어(풀네임은 검수로 채움)
반자동: 초안 생성 후 사람이 검수해 dictionary 로 확정. [docs/specs/25, 동의어 연결]
"""
import argparse
import collections
import re
import sqlite3
from pathlib import Path

# 일반/기술 공통 약어 — 도메인 용어가 아니라 제외
_COMMON_ABBR = {
    "API", "URL", "SQL", "AWS", "GCP", "S3", "SSH", "UI", "UX", "ID", "DB", "CD", "CI",
    "HTTP", "HTTPS", "JSON", "YAML", "PR", "OK", "VM", "OS", "IP", "CPU", "RAM", "PDF",
    "CSV", "HTML", "CSS", "JS", "TS", "IT", "PC", "AS", "EC", "PO", "QA", "INFO",
}
# 흔한 한글 명사 — 동의어가 아니라 본문 우연 매칭이므로 제외
_STOP_KO = {
    "확인", "요청", "작성", "제목", "구분", "내용", "등록", "수정", "삭제", "목록", "정보",
    "관리", "설정", "사용", "처리", "이름", "담당", "상태", "추가", "개선", "완료", "진행",
    "생성", "변경", "대상", "기준", "결과", "오류", "문제", "방법", "관련", "필요",
}


def _is_code_field(en: str) -> bool:
    """camelCase·snake_case 같은 코드 필드명(hccThirdAgreeYn 등)인지."""
    return bool(re.search(r"[a-z][A-Z]", en)) or "_" in en


_PAREN_EN_KO = re.compile(r"([A-Za-z][A-Za-z0-9 ]{1,18})\s*[(（]([가-힣]{2,12})[)）]")
_PAREN_KO_EN = re.compile(r"([가-힣]{2,12})\s*[(（]([A-Za-z][A-Za-z0-9 ]{1,18})[)）]")
_ABBR = re.compile(r"(?<![A-Za-z])([A-Z]{2,5})(?![A-Za-z0-9])")


def scan(db_paths: list[str]) -> tuple[collections.Counter, collections.Counter]:
    paren: collections.Counter = collections.Counter()
    abbr: collections.Counter = collections.Counter()
    for db in db_paths:
        conn = sqlite3.connect(db)
        for title, body in conn.execute("SELECT title, substr(body_markdown,1,400) FROM documents"):
            text = (title or "") + " " + (body or "")
            for m in _PAREN_EN_KO.finditer(text):
                paren[(m.group(1).strip(), m.group(2))] += 1
            for m in _PAREN_KO_EN.finditer(text):
                paren[(m.group(2).strip(), m.group(1))] += 1
            for m in _ABBR.finditer(text):
                abbr[m.group(1)] += 1
        conn.close()
    return paren, abbr


def load_deny(path: str) -> tuple[set, set]:
    """제외 목록(검수로 등록한 오수집). 재추출해도 무시된다. {terms:[], synonyms:[]}."""
    import yaml
    p = Path(path)
    if not p.exists():
        return set(), set()
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return set(d.get("terms") or []), set(d.get("synonyms") or [])
    except Exception:
        return set(), set()


def build_entries(paren: collections.Counter, abbr: collections.Counter, min_freq: int,
                  deny_terms: set = frozenset(), deny_syn: set = frozenset()) -> list[dict]:
    entries: list[dict] = []
    # 괄호 병기: 영문 term 이 단어 2개 이하인 것만(구문 noise 제거)
    for (en, ko), n in paren.most_common():
        if n < min_freq or len(en.split()) > 2:
            continue
        if ko in _STOP_KO:          # 흔한 단어 synonym(확인·요청…) 제외
            continue
        if _is_code_field(en):      # 코드 필드명(hccThirdAgreeYn) 제외
            continue
        if en in deny_terms or ko in deny_syn:   # 검수로 등록한 오수집 제외(영구)
            continue
        entries.append({"term": en, "synonyms": [ko], "domain": "auto-paren", "_freq": n})
    # 약어: 일반 약어 제외, 빈출만 (풀네임은 검수로)
    for a, n in abbr.most_common():
        if n < min_freq or a in _COMMON_ABBR or a in deny_terms:
            continue
        entries.append({"term": a, "synonyms": [], "domain": "auto-abbr", "_freq": n,
                        "_todo": "풀네임/한글 동의어 채우기"})
    return entries


def merge_into(existing_path: str, entries: list[dict]) -> tuple[list[dict], int, int, int]:
    """자동 후보를 기존(수동) 사전에 병합. 기존 항목은 보존(수동 우선),
    자동 신규 term 추가, 같은 term 은 synonym 합침. synonym 없는 후보(약어)는 제외.
    반환: (병합본, 신규 term 수, 추가된 synonym 수, 기존 term 수).
    """
    import yaml
    existing: list = []
    p = Path(existing_path)
    if p.exists():
        existing = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    base_n = len(existing)
    by_term = {e["term"]: e for e in existing if isinstance(e, dict) and "term" in e}
    added = 0
    syn_added = 0
    for c in entries:
        syns = c.get("synonyms") or []
        if not syns:  # 약어처럼 synonym 없으면 동의어 확장 효과 없음 → 병합 제외(검수 목록으로만)
            continue
        t = c["term"]
        if t in by_term:
            cur = by_term[t].setdefault("synonyms", [])
            for s in syns:
                if s not in cur:
                    cur.append(s)
                    syn_added += 1
        else:
            by_term[t] = {"term": t, "synonyms": list(syns),
                          "domain": c.get("domain", "auto"), "scope": "global", "boost": 1.0}
            added += 1
    return list(by_term.values()), added, syn_added, base_n


def main() -> None:
    ap = argparse.ArgumentParser(description="동의어 사전 부트스트랩(초안)")
    ap.add_argument("dbs", help="DB 경로(콤마로 여러 개)")
    ap.add_argument("--out", default="dict.draft.yml")
    ap.add_argument("--min", type=int, default=5, help="최소 빈도(기본 5)")
    ap.add_argument("--merge", help="기존 사전 yaml 에 자동 병합(기존 보존 + 자동 신규). 결과는 --out 에 list 형식")
    ap.add_argument("--deny", default=str(Path.home() / ".geryon" / "gold" / "dictionary.deny.yaml"),
                    help="제외 목록 yaml(terms/synonyms) — 오수집을 영구 무시")
    a = ap.parse_args()

    db_paths = [p.strip() for p in a.dbs.split(",") if p.strip()]
    deny_terms, deny_syn = load_deny(a.deny)
    paren, abbr = scan(db_paths)
    entries = build_entries(paren, abbr, a.min, deny_terms, deny_syn)
    if deny_terms or deny_syn:
        print(f"(제외 목록 적용: term {len(deny_terms)} · synonym {len(deny_syn)})")

    import yaml
    if a.merge:
        merged, added, syn_added, base_n = merge_into(a.merge, entries)
        Path(a.out).write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"병합: 기존(수동) {base_n} + 자동 신규 term {added} + synonym 추가 {syn_added} "
              f"= {len(merged)}개 → {a.out}")
        print("  (synonym 없는 약어 후보는 병합 제외 — 풀네임 검수 후 별도 추가)")
    else:
        # 자동 사전 파일(dictionary.auto.yaml): synonym 쌍만 list 로 — GoldSearch 가 바로 로드.
        # _freq 등 보조 필드는 검수 편의로 남겨둠(GoldSearch 는 _ 필드 무시).
        dict_entries = [e for e in entries if e.get("synonyms")]
        Path(a.out).write_text(yaml.safe_dump(dict_entries, allow_unicode=True, sort_keys=False),
                               encoding="utf-8")
        abbr_only = [e["term"] for e in entries if not e.get("synonyms")]
        print(f"자동 사전(synonym 쌍) {len(dict_entries)}건 → {a.out}")
        print(f"  설치: cp {a.out} ~/.geryon/gold/dictionary.auto.yaml  (수동 사전과 분리 관리)")
        print("  ⚠ 자동 사전은 기본 OFF(opt-in). 노이즈(작성자명·우연 괄호매칭)가 섞일 수 있어")
        print("     골든으로 정확도 확인 후 GERYON_DICT_AUTO=1 로 켠다(검수·deny-list 권장).")
        if abbr_only:
            print(f"  풀네임 없는 약어 {len(abbr_only)}건은 검수 대상: {abbr_only[:15]}")


if __name__ == "__main__":
    main()
