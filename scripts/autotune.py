#!/usr/bin/env python3
"""셋업/자동튜닝 도구 — 사양 분석 → 옵션 권장 → (수집 후) 성능 기계 검증.

    python scripts/autotune.py analyze [--db DB]     # 1) 하드웨어·코퍼스 분석 → 권장 설정 출력
    python scripts/autotune.py measure --db DB       # 2) 이 호스트에서 실측 → 기준선(baseline) 저장
    python scripts/autotune.py verify  --db DB       # 3) 현재 성능이 기준선을 지키는지 합격/불합격 판정
    python scripts/autotune.py apply   [--env-file]  # 4) 권장 설정을 .env 스니펫으로 출력/기록

## 설계 의도
설정값을 사람이 감으로 정하지 않는다. 세 단계 모두 측정값 또는 하드웨어 사실에서 유도한다.

  analyze : 벤치마크 없이 즉답(설치 직후, 데이터 수집 전에도 쓸 수 있어야 하므로).
            하드웨어(코어/RAM) + 코퍼스 규모(문서·벡터 수) + **레퍼런스 프로파일**로 외삽.
  measure : 실제로 벤치를 돌려 이 호스트의 진짜 수치를 얻고 baseline.json 으로 고정.
            analyze 의 외삽을 실측으로 대체하는 단계.
  verify  : 수집·색인이 끝난 뒤(코퍼스가 커진 뒤) 다시 돌려 baseline 대비 회귀를 판정.
            데이터가 늘면 느려지는 게 정상이므로, 절대값이 아니라 **허용 배수**로 판정한다.

레퍼런스 프로파일(REFERENCE)은 이 저장소에서 실측한 값이다(20코어/62GB, 35,768문서/143,800벡터).
다른 사양에서는 measure 로 갱신해 쓰는 것이 정확하다 — analyze 는 어디까지나 초기 추정.
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_BASELINE = Path.home() / ".geryon" / "autotune_baseline.json"

# ── 이 저장소에서 실측한 레퍼런스(scripts/profile_resources.py 결과) ──
# 20코어 / 62GB / 35,768문서 / 143,800벡터 기준
REFERENCE = {
    "host": {"cores": 20, "ram_gb": 62},
    "corpus": {"docs": 35768, "vectors": 143800},
    "configs": {
        "default":      {"rss_mb": 3096, "p50_ms": 898},
        "rerank_off":   {"rss_mb": 908,  "p50_ms": 3195},
        "rerank_fp32":  {"rss_mb": 2720, "p50_ms": 1545},
        "passage_off":  {"rss_mb": 3095, "p50_ms": 427},
        "pool20":       {"rss_mb": 3094, "p50_ms": 329},
    },
    "threads": [
        {"threads": 1, "p50_ms": 3415}, {"threads": 2, "p50_ms": 1950},
        {"threads": 4, "p50_ms": 1064}, {"threads": 8, "p50_ms": 844},
        {"threads": 12, "p50_ms": 909}, {"threads": 16, "p50_ms": 914},
        {"threads": 20, "p50_ms": 1607},
    ],
    "optimal_threads": 8,
    "ram_safety_factor": 1.5,
}


# ─────────────────────────────── 사실 수집 ───────────────────────────────

def detect_host() -> dict:
    cores = os.cpu_count() or 1
    ram_gb = None
    try:  # /proc/meminfo (리눅스) — psutil 의존 없이
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram_gb = int(line.split()[1]) / 1024 / 1024
                break
    except Exception:
        pass
    disk_free_gb = shutil.disk_usage(Path.home()).free / 1024 ** 3
    return {"cores": cores, "ram_gb": round(ram_gb, 1) if ram_gb else None,
            "disk_free_gb": round(disk_free_gb, 1)}


def detect_corpus(db: str | None) -> dict:
    if not db or not Path(db).exists():
        return {"exists": False}
    size_gb = Path(db).stat().st_size / 1024 ** 3
    out = {"exists": True, "db_size_gb": round(size_gb, 2)}
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        out["docs"] = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        try:
            conn.enable_load_extension(True)
            import sqlite_vec
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            out["vectors"] = conn.execute("SELECT COUNT(*) FROM chunk_embeddings").fetchone()[0]
        except Exception:
            out["vectors"] = None
        conn.close()
    except Exception as e:
        out["error"] = str(e)
    return out


# ─────────────────────────────── 권장 로직 ───────────────────────────────

def scale_factor(corpus: dict) -> float:
    """레퍼런스 대비 코퍼스 배율 — 지연 외삽에 사용.
    벡터 검색은 브루트포스라 벡터 수에 대체로 선형. 벡터 수가 없으면 문서 수로 대체."""
    ref_v = REFERENCE["corpus"]["vectors"]
    v = corpus.get("vectors") or None
    if v:
        return v / ref_v
    d = corpus.get("docs")
    return (d / REFERENCE["corpus"]["docs"]) if d else 1.0


def recommend(host: dict, corpus: dict) -> dict:
    """하드웨어·코퍼스에서 권장 설정을 유도(임의 상수 없이 레퍼런스 실측에서 계산)."""
    ram = host.get("ram_gb")
    cores = host["cores"]
    sf = scale_factor(corpus)

    ref = REFERENCE["configs"]
    need_full_gb = ref["default"]["rss_mb"] * REFERENCE["ram_safety_factor"] / 1024
    need_lean_gb = ref["rerank_off"]["rss_mb"] * REFERENCE["ram_safety_factor"] / 1024

    # 1) 메모리로 프로파일 결정
    if ram is None:
        profile, reason = "balanced", "RAM 미검출 — 보수적으로 balanced 선택"
    elif ram >= need_full_gb:
        profile = "fast"
        reason = f"RAM {ram}GB ≥ 필요 {need_full_gb:.1f}GB → rerank 사용 가능"
    elif ram >= need_lean_gb:
        profile = "lean"
        reason = (f"RAM {ram}GB < rerank 요구 {need_full_gb:.1f}GB → rerank off "
                  f"(메모리 {need_lean_gb:.1f}GB로 동작, 대신 지연 증가)")
    else:
        profile = "insufficient"
        reason = f"RAM {ram}GB < 최소 {need_lean_gb:.1f}GB — 동작 불가 가능성"

    # 2) 스레드: 레퍼런스 최적(8) 과 실제 코어 수 중 작은 값. 코어가 적으면 그만큼만.
    threads = min(REFERENCE["optimal_threads"], cores)

    # 3) 설정 스니펫
    if profile == "fast":
        env = {"GERYON_RERANK": "1", "GERYON_RERANK_QUANTIZE": "1",
               "GERYON_RERANK_POOL": "20", "GERYON_RERANK_THREADS": str(threads)}
        base_ms = ref["pool20"]["p50_ms"]
        note = "pool=20 은 정확도·속도 양쪽에서 기본값(60)보다 우세함이 실측됨"
    elif profile == "lean":
        env = {"GERYON_RERANK": "0", "GERYON_RERANK_THREADS": str(threads)}
        base_ms = ref["rerank_off"]["p50_ms"]
        note = "저메모리 구성 — rerank 없이 하이브리드(FTS5+벡터)만 사용"
    elif profile == "balanced":
        env = {"GERYON_RERANK": "1", "GERYON_RERANK_QUANTIZE": "1",
               "GERYON_RERANK_THREADS": str(threads)}
        base_ms = ref["default"]["p50_ms"]
        note = "RAM 미확인 — 기본 설정 유지"
    else:
        env, base_ms, note = {}, None, "하드웨어가 최소 요구에 미달"

    est_ms = round(base_ms * sf) if base_ms else None
    return {"profile": profile, "reason": reason, "env": env, "threads": threads,
            "scale_factor": round(sf, 2), "estimated_p50_ms": est_ms, "note": note,
            "need_ram_full_gb": round(need_full_gb, 1), "need_ram_lean_gb": round(need_lean_gb, 1)}


# ─────────────────────────────── 서브커맨드 ───────────────────────────────

def cmd_analyze(a) -> None:
    host, corpus = detect_host(), detect_corpus(a.db)
    print("=== 1) 호스트 ===")
    print(f"  CPU 코어: {host['cores']}   RAM: {host['ram_gb']}GB   디스크 여유: {host['disk_free_gb']}GB")
    print("\n=== 2) 코퍼스 ===")
    if corpus.get("exists"):
        print(f"  문서 {corpus.get('docs'):,}건 · 벡터 {corpus.get('vectors') or '?':,}개 "
              f"· DB {corpus['db_size_gb']}GB")
    else:
        print("  (DB 없음 — 수집 전. 레퍼런스 규모로 추정)")

    rec = recommend(host, corpus)
    print("\n=== 3) 권장 설정 ===")
    print(f"  프로파일: {rec['profile']}   ({rec['reason']})")
    print(f"  근거 메모리 요구: rerank 사용 {rec['need_ram_full_gb']}GB / 미사용 {rec['need_ram_lean_gb']}GB")
    if rec["env"]:
        for k, v in rec["env"].items():
            print(f"    {k}={v}")
    print(f"  코퍼스 배율(레퍼런스 대비): ×{rec['scale_factor']}")
    if rec["estimated_p50_ms"]:
        print(f"  예상 질의 지연(p50): ~{rec['estimated_p50_ms']}ms  (외삽 — measure 로 실측 권장)")
    print(f"  비고: {rec['note']}")
    print("\n  다음 단계: python scripts/autotune.py measure --db <DB>   (이 호스트 실측으로 확정)")


def cmd_measure(a) -> None:
    """profile_resources.py 를 실행해 이 호스트의 실측 기준선을 만든다."""
    import subprocess
    tmp = Path(a.baseline).with_suffix(".raw.json")
    Path(a.baseline).parent.mkdir(parents=True, exist_ok=True)
    print("실측 중(설정 5종 + 스레드 스윕) — 수 분 걸립니다...\n")
    proc = subprocess.run([sys.executable, str(HERE / "profile_resources.py"), a.db,
                           "--out", str(tmp)])
    if proc.returncode != 0 or not tmp.exists():
        raise SystemExit("measure 실패 — profile_resources.py 출력을 확인하세요")

    raw = json.loads(tmp.read_text(encoding="utf-8"))
    host, corpus = detect_host(), detect_corpus(a.db)
    best_cfg = min(raw["configs"], key=lambda c: c["p50_ms"]) if raw.get("configs") else None
    baseline = {
        "host": host, "corpus": corpus,
        "configs": raw.get("configs", []), "threads": raw.get("threads", []),
        "recommend_ram_gb": raw.get("recommend_ram_gb"),
        "recommend_cores": raw.get("recommend_cores"),
        "fastest_config": best_cfg,
    }
    Path(a.baseline).write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n기준선 저장 → {a.baseline}")
    if best_cfg:
        print(f"  최속 설정: {best_cfg['label']}  p50={best_cfg['p50_ms']:.0f}ms "
              f"RSS={best_cfg['peak_rss_mb']:.0f}MB")
    print(f"  권장 RAM {baseline['recommend_ram_gb']}GB · 권장 코어 {baseline['recommend_cores']}")
    print("\n  다음 단계: 수집·색인 후  python scripts/autotune.py verify --db <DB>")


def cmd_verify(a) -> None:
    """수집 후 재측정 — 기준선 대비 회귀 판정. 코퍼스 증가분을 감안한 허용 배수로 비교."""
    import subprocess
    bp = Path(a.baseline)
    if not bp.exists():
        raise SystemExit(f"기준선 없음: {bp}  — 먼저 measure 를 실행하세요")
    base = json.loads(bp.read_text(encoding="utf-8"))

    tmp = bp.with_suffix(".verify.json")
    print("현재 성능 측정 중...\n")
    proc = subprocess.run([sys.executable, str(HERE / "profile_resources.py"), a.db,
                           "--out", str(tmp)])
    if proc.returncode != 0 or not tmp.exists():
        raise SystemExit("verify 실패")
    now = json.loads(tmp.read_text(encoding="utf-8"))

    base_corpus, now_corpus = base.get("corpus", {}), detect_corpus(a.db)
    bv = base_corpus.get("vectors") or base_corpus.get("docs") or 1
    nv = now_corpus.get("vectors") or now_corpus.get("docs") or 1
    growth = nv / bv if bv else 1.0

    print("\n=== 검증 결과 ===")
    print(f"  코퍼스: {bv:,} → {nv:,}  (×{growth:.2f})")
    # 벡터검색이 대체로 선형이므로 성장배수까지는 정상 — 여유 20% 허용
    allowed = growth * (1 + a.tolerance / 100)
    print(f"  허용 지연 배수: ×{allowed:.2f} (성장 ×{growth:.2f} + 여유 {a.tolerance:.0f}%)\n")

    base_by = {c["label"]: c for c in base.get("configs", [])}
    failed = []
    print(f"{'config':30} {'기준 p50':>10} {'현재 p50':>10} {'배수':>7} {'판정':>6}")
    for c in now.get("configs", []):
        b = base_by.get(c["label"])
        if not b:
            continue
        ratio = c["p50_ms"] / b["p50_ms"] if b["p50_ms"] else float("inf")
        ok = ratio <= allowed
        if not ok:
            failed.append((c["label"], ratio))
        print(f"{c['label']:30} {b['p50_ms']:>9.0f}ms {c['p50_ms']:>9.0f}ms "
              f"{ratio:>6.2f}x {'OK' if ok else 'FAIL':>6}")

    ram_now = max((c["peak_rss_mb"] for c in now.get("configs", [])), default=0)
    ram_base = max((c["peak_rss_mb"] for c in base.get("configs", [])), default=0)
    print(f"\n  피크 RSS: {ram_base:.0f}MB → {ram_now:.0f}MB")
    host_ram = detect_host().get("ram_gb")
    if host_ram and ram_now * REFERENCE["ram_safety_factor"] / 1024 > host_ram:
        print(f"  ⚠ 현재 피크×1.5 = {ram_now*1.5/1024:.1f}GB 가 호스트 RAM {host_ram}GB 를 초과")
        failed.append(("memory", ram_now))

    if failed:
        print(f"\n  ❌ 회귀 감지: {[f[0] for f in failed]}")
        sys.exit(1)
    print("\n  ✅ 합격 — 코퍼스 증가를 감안한 허용 범위 내")


def cmd_apply(a) -> None:
    host, corpus = detect_host(), detect_corpus(a.db)
    rec = recommend(host, corpus)
    if not rec["env"]:
        raise SystemExit(f"권장 설정 없음(profile={rec['profile']}): {rec['reason']}")
    lines = ["# GeryonMCP autotune 권장 설정 (scripts/autotune.py apply)",
             f"# 근거: {rec['reason']}",
             f"# 비고: {rec['note']}"]
    lines += [f"{k}={v}" for k, v in rec["env"].items()]
    snippet = "\n".join(lines) + "\n"
    if a.env_file:
        p = Path(a.env_file)
        prev = p.read_text(encoding="utf-8") if p.exists() else ""
        p.write_text(prev.rstrip("\n") + "\n\n" + snippet if prev else snippet, encoding="utf-8")
        print(f"기록 완료 → {p}")
    else:
        print(snippet)
        print("  (--env-file .env 로 파일에 덧붙일 수 있습니다)")


def main() -> None:
    ap = argparse.ArgumentParser(description="GeryonMCP 셋업/자동튜닝")
    sub = ap.add_subparsers(dest="cmd", required=True)

    default_db = os.getenv("GERYON_DB", str(Path.home() / ".geryon" / "geryon.db"))

    p = sub.add_parser("analyze", help="하드웨어·코퍼스 분석 → 권장 설정(벤치 없음, 즉답)")
    p.add_argument("--db", default=default_db)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("measure", help="이 호스트에서 실측 → 기준선 저장")
    p.add_argument("--db", default=default_db)
    p.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    p.set_defaults(func=cmd_measure)

    p = sub.add_parser("verify", help="수집 후 재측정 → 기준선 대비 회귀 판정")
    p.add_argument("--db", default=default_db)
    p.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    p.add_argument("--tolerance", type=float, default=20.0, help="성장배수 위 추가 허용 %% (기본 20)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("apply", help="권장 설정을 .env 스니펫으로 출력/기록")
    p.add_argument("--db", default=default_db)
    p.add_argument("--env-file", default=None)
    p.set_defaults(func=cmd_apply)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
