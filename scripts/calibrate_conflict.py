#!/usr/bin/env python3
"""정의-충돌 판정기 임계값 보정 — 사람 라벨 없이 코퍼스에서 정답셋을 만들어 기계적으로 최적화.

    python scripts/calibrate_conflict.py <db> [--out calib_report.json]

## 문제
term_bootstrap.py 의 _defs_conflict() 는 두 정의 텍스트가 "같은 개념이냐"를 판정하는데,
임계값(containment>=0.7, jaccard<0.25)이 근거 없이 손으로 정해진 숫자였다. 그래서 판정 결과를
사람이 다시 눈으로 골라내야 했다(= 기계적으로 파라미터/기준을 정할 수 없었다).

## 해법 — DDL 구조에서 정답 라벨을 기계적으로 유도
CREATE TABLE 블록을 파싱해 (테이블, 컬럼, COMMENT) 삼중항을 뽑으면 라벨이 공짜로 나온다:

  * 양성(SAME, "충돌 아님"이 정답):
      같은 (테이블, 컬럼) 의 정의가 서로 다른 문서에 등장한 쌍.
      같은 테이블의 같은 컬럼이므로 **정의상 같은 개념** — 표현만 다를 뿐.
      예) city.name_kr = '공통 도시명(한글)'  vs  '도시명 한글'

  * 음성(DIFF, "충돌"이 정답) — 의도적으로 **어려운** 음성:
      **같은 테이블 안의 서로 다른 컬럼** 쌍.
      같은 도메인이라 어휘·문체가 비슷해 구분이 어렵지만, 다른 컬럼이므로 **확실히 다른 개념**.
      예) dep_date='출발일'  vs  arr_date='도착일'
      (무작위 테이블 간 쌍은 텍스트가 너무 달라 쉽게 맞혀서 보정에 쓸모가 없다 — 일부러 배제)

이 라벨은 사람의 의미 판단이 아니라 **DDL 스키마 구조**에서 유도되므로 완전히 기계적이고 재현 가능.

## 산출
containment/jaccard 임계값 격자를 전수 탐색해 각 조합의 precision/recall/F1/accuracy 를 계산,
F1 최댓값 조합을 권장값으로 출력. 현재 하드코딩 값(0.7/0.25)의 성적도 같은 표에서 비교한다.
"""
import argparse
import collections
import itertools
import json
import random
import re
import sqlite3
from pathlib import Path

_CREATE_TABLE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:EXTERNAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"']?"
    r"([A-Za-z_][A-Za-z0-9_.]*)",
    re.IGNORECASE,
)
_COL_COMMENT = re.compile(
    r"^\s*[`\"']?([a-z_][a-z0-9_]{2,40})[`\"']?\s+[A-Za-z][^\n']{0,60}?COMMENT\s+'([^']{2,120})'",
    re.IGNORECASE | re.MULTILINE,
)
# term_bootstrap 과 동일한 제외 목록(범용/감사 컬럼은 테이블 간 의미가 같아 라벨 신뢰도가 낮음)
_EXCLUDE = {
    "id", "name", "type", "value", "data", "url", "code", "status", "rank", "level",
    "description", "count", "date", "time", "created_at", "updated_at", "modified_at",
    "key", "label", "title", "text", "flag", "reason", "note", "memo", "amount", "price",
    "reg_date", "reg_id", "regist_dt", "upd_date", "upd_id", "mod_date", "mod_id",
    "collate", "default", "not", "null", "primary", "unique", "engine", "add", "alter",
    "column", "modify", "change", "drop", "comment",
}


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[가-힣A-Za-z0-9]+", s.lower()))


def conflict_decision(a: str, b: str, containment_th: float, jaccard_th: float) -> bool:
    """term_bootstrap._defs_conflict 와 동일한 로직 — 임계값만 파라미터화.
    True = '충돌(다른 개념)' 으로 판정."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    containment = len(smaller & larger) / len(smaller)
    if containment >= containment_th:
        return False
    jaccard = len(ta & tb) / len(ta | tb)
    return jaccard < jaccard_th


def extract_table_columns(db: str) -> list[tuple[str, str, str, str]]:
    """(table, column, comment, doc_url) 삼중항+출처. CREATE TABLE 블록 경계로 컬럼을 귀속."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT url, body_markdown FROM documents WHERE body_markdown LIKE '%COMMENT%'"
    ).fetchall()
    conn.close()

    out: list[tuple[str, str, str, str]] = []
    for url, body in rows:
        body = body or ""
        # CREATE TABLE 위치들을 찾아 각 블록 범위를 [현재 매치, 다음 매치) 로 자른다
        marks = [(m.start(), m.group(1)) for m in _CREATE_TABLE.finditer(body)]
        if not marks:
            continue
        marks.append((len(body), None))  # sentinel
        for i in range(len(marks) - 1):
            start, table = marks[i][0], marks[i][1]
            end = marks[i + 1][0]
            block = body[start:end]
            for cm in _COL_COMMENT.finditer(block):
                col, comment = cm.group(1).lower(), cm.group(2).strip()
                if col in _EXCLUDE:
                    continue
                out.append((table.lower(), col, comment, url))
    return out


def build_labeled_pairs(triples: list[tuple[str, str, str, str]], seed: int = 0
                        ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """양성(같은 개념) / 어려운 음성(같은 테이블·다른 컬럼) 쌍 생성."""
    rng = random.Random(seed)

    # 양성: 같은 (table, col) 의 서로 다른 정의(문서가 다르거나 표현이 다른 경우)
    by_tc: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for table, col, comment, _url in triples:
        by_tc[(table, col)].add(comment)
    positives: list[tuple[str, str]] = []
    for (_t, _c), defs in by_tc.items():
        defs_l = sorted(defs)
        if len(defs_l) < 2:
            continue
        for a, b in itertools.combinations(defs_l, 2):
            positives.append((a, b))

    # 어려운 음성: 같은 테이블 안의 서로 다른 컬럼(도메인 유사, 개념은 확실히 다름)
    by_table: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for table, col, comment, _url in triples:
        by_table[table].setdefault(col, comment)
    negatives: list[tuple[str, str]] = []
    for _table, cols in by_table.items():
        items = sorted(cols.items())
        if len(items) < 2:
            continue
        for (ca, da), (cb, db_) in itertools.combinations(items, 2):
            if da == db_:
                continue  # 정의 텍스트가 완전 동일하면 라벨 신뢰 불가(같은 뜻일 수 있음) — 제외
            negatives.append((da, db_))

    # 클래스 균형(적은 쪽에 맞춤) — 정확도 지표가 다수클래스에 휘둘리지 않게
    n = min(len(positives), len(negatives))
    rng.shuffle(positives)
    rng.shuffle(negatives)
    return positives[:n], negatives[:n]


def evaluate(positives, negatives, c_th: float, j_th: float) -> dict:
    """양성=충돌아님(False)이 정답, 음성=충돌(True)이 정답."""
    tp = sum(1 for a, b in negatives if conflict_decision(a, b, c_th, j_th))       # 충돌을 충돌로
    fn = len(negatives) - tp                                                       # 충돌을 놓침
    fp = sum(1 for a, b in positives if conflict_decision(a, b, c_th, j_th))       # 같은걸 충돌로(오탐)
    tn = len(positives) - fp
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    acc = (tp + tn) / (len(positives) + len(negatives)) if (positives or negatives) else 0.0
    return {"containment_th": c_th, "jaccard_th": j_th, "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": prec, "recall": rec, "f1": f1, "accuracy": acc}


def main() -> None:
    ap = argparse.ArgumentParser(description="정의-충돌 판정 임계값 기계적 보정")
    ap.add_argument("db")
    ap.add_argument("--out", default=None, help="결과 JSON 저장 경로")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    print("DDL 파싱 중...")
    triples = extract_table_columns(a.db)
    tables = len({t for t, _, _, _ in triples})
    print(f"  (table, column, comment) 삼중항 {len(triples)}건 · 테이블 {tables}개")

    positives, negatives = build_labeled_pairs(triples, a.seed)
    print(f"\n정답셋(기계 유도, 클래스 균형):")
    print(f"  양성(같은 개념, 같은 테이블·같은 컬럼): {len(positives)}쌍")
    print(f"  음성(다른 개념, 같은 테이블·다른 컬럼): {len(negatives)}쌍")
    if min(len(positives), len(negatives)) < 30:
        print("  ⚠ 표본이 작아 보정 신뢰도가 낮습니다(각 30쌍 미만).")

    grid_c = [round(x, 2) for x in [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]]
    grid_j = [round(x, 2) for x in [0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]]
    results = [evaluate(positives, negatives, c, j) for c, j in itertools.product(grid_c, grid_j)]
    results.sort(key=lambda r: (-r["f1"], -r["accuracy"]))

    print(f"\n=== 임계값 격자 탐색 상위 10 (F1 기준) ===")
    print(f"{'contain':>8} {'jaccard':>8} | {'precision':>9} {'recall':>7} {'F1':>7} {'acc':>7}")
    for r in results[:10]:
        print(f"{r['containment_th']:>8} {r['jaccard_th']:>8} | {r['precision']:>9.3f} "
              f"{r['recall']:>7.3f} {r['f1']:>7.3f} {r['accuracy']:>7.3f}")

    current = next(r for r in results if r["containment_th"] == 0.7 and r["jaccard_th"] == 0.25)
    best = results[0]
    print(f"\n=== 현재 하드코딩 값 vs 최적값 ===")
    print(f"  현재 (0.7 / 0.25): F1={current['f1']:.3f} precision={current['precision']:.3f} "
          f"recall={current['recall']:.3f} acc={current['accuracy']:.3f}")
    print(f"  최적 ({best['containment_th']} / {best['jaccard_th']}): F1={best['f1']:.3f} "
          f"precision={best['precision']:.3f} recall={best['recall']:.3f} acc={best['accuracy']:.3f}")
    delta = best["f1"] - current["f1"]
    if delta <= 0.01:
        print(f"  → 개선폭 {delta:+.3f} — 현재 값이 사실상 최적(교체 이득 없음)")
    else:
        print(f"  → 개선폭 {delta:+.3f} — term_bootstrap._defs_conflict 임계값 교체 권장")

    if a.out:
        Path(a.out).write_text(json.dumps(
            {"n_positive": len(positives), "n_negative": len(negatives),
             "grid": results, "current": current, "best": best},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  결과 → {a.out}")


if __name__ == "__main__":
    main()
