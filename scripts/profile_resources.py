#!/usr/bin/env python3
"""자원 프로파일러 — 설정별 피크 RAM·지연시간을 실측해 **권장 사양을 기계적으로 도출**.

    python scripts/profile_resources.py <db> [--queries N] [--out profile.json]

기존 bench(toolkit.py)는 '정확도'를 재고, 이 스크립트는 '비용'(메모리·지연·스레드 확장성)을 잰다.
권장 사양(최소 RAM/권장 코어)을 손짐작이 아니라 측정에서 유도하는 것이 목적.

측정 항목(설정 조합마다 **독립 프로세스**로 실행 — GERYON_* 는 config import 시점에 한 번만
읽히므로 같은 프로세스에서 재설정 불가):
  * peak_rss_mb : 프로세스 최대 상주 메모리(RSS) — 모델 로드+검색 전체의 실제 소요 메모리
  * latency     : 질의당 지연 p50/p95 (모델 로드 후 정상상태만 — 첫 질의는 워밍업으로 제외)
  * threads 확장성: GERYON_RERANK_THREADS 를 바꿔가며 지연 변화 → 코어 추가의 실익 지점 탐색

권장 사양 도출 규칙(모두 측정값에서 계산, 임의 상수 없음):
  최소 RAM  = 측정된 peak_rss 최댓값 × 안전계수 1.5 (OS/버퍼 여유)
  권장 코어 = 스레드를 늘려도 지연 개선이 5% 미만이 되는 지점(수확체감 임계) 의 스레드 수
"""
import argparse
import json
import os
import subprocess
import sys
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

# 워커가 실행할 코드: 모델 로드 → 워밍업 1회 → 측정 N회 → RSS/지연 JSON 출력
_WORKER = r'''
import json, os, resource, sys, time
sys.path.insert(0, __SRC__)
from geryon.search.factory import build_searcher

db = sys.argv[1]
queries = json.loads(sys.argv[2])
searcher = build_searcher(db_paths=[db])

# 워밍업(모델 lazy load·캐시) — 측정에서 제외
searcher.search(queries[0], k=10)

lat = []
for q in queries:
    t0 = time.perf_counter()
    searcher.search(q, k=10)
    lat.append((time.perf_counter() - t0) * 1000)

# ru_maxrss: Linux 는 KB 단위
peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print("__RESULT__" + json.dumps({
    "peak_rss_mb": peak_kb / 1024,
    "lat_ms": lat,
}))
'''

# 도메인 중립 질의 — 사내 고유명을 넣지 말 것(저장소에 사업 정보가 드러남).
# 지연·메모리 측정이 목적이라 질의 내용 자체는 결과에 큰 영향이 없다.
QUERIES = [
    "배포 프로세스", "정책 평가 코드", "컬럼 정의", "할인 정책",
    "지표 계산", "취소 규정", "테이블 스키마", "인증 토큰 발급",
]


def run_worker(db: str, env_overrides: dict, queries: list[str]) -> dict | None:
    env = os.environ.copy()
    env.update(env_overrides)
    src = str(HERE.parent / "src")
    # .format() 은 워커 코드 안의 JSON 중괄호를 치환자리로 오인하므로 단순 치환 사용
    code = _WORKER.replace("__SRC__", repr(src))
    proc = subprocess.run([PY, "-c", code, db, json.dumps(queries, ensure_ascii=False)],
                          env=env, capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    print(f"  [실패] {env_overrides}", file=sys.stderr)
    print(proc.stderr[-600:], file=sys.stderr)
    return None


def pick_optimal_threads(rows: list[dict], tolerance_pct: float = 5.0) -> dict | None:
    """최속 대비 tolerance 이내면서 **가장 적은** 스레드 수를 고른다.

    이전 구현은 '직전 대비 개선 <5%인 첫 지점'을 썼는데, 성능이 **악화**되는 구간
    (예: 8→12스레드 -7.7%)도 조건을 만족해 오답(12)을 냈다. 최속 기준 상대비교로 교체.
    """
    if not rows:
        return None
    best = min(rows, key=lambda r: r["p50_ms"])
    limit = best["p50_ms"] * (1 + tolerance_pct / 100)
    ok = [r for r in rows if r["p50_ms"] <= limit]
    return min(ok, key=lambda r: r["threads"]) if ok else best


def summarize(res: dict) -> tuple[float, float, float]:
    lat = sorted(res["lat_ms"])
    p50 = statistics.median(lat)
    p95 = lat[max(0, int(len(lat) * 0.95) - 1)]
    return res["peak_rss_mb"], p50, p95


def main() -> None:
    ap = argparse.ArgumentParser(description="설정별 메모리·지연 실측 → 권장 사양 도출")
    ap.add_argument("db")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-threads", type=int, default=os.cpu_count() or 8)
    a = ap.parse_args()

    report: dict = {"host": {"cpu_count": os.cpu_count()}, "configs": [], "threads": []}

    print("=== 1) 설정별 피크 메모리·지연 ===")
    print(f"{'config':38} {'peak RSS':>10} {'p50':>9} {'p95':>9}")
    configs = [
        ("기본(rerank on, int8)", {}),
        ("rerank off", {"GERYON_RERANK": "0"}),
        ("rerank fp32(quantize off)", {"GERYON_RERANK_QUANTIZE": "0"}),
        ("passage off", {"GERYON_RERANK_PASSAGE": "0"}),
        ("pool 20", {"GERYON_RERANK_POOL": "20"}),
    ]
    for label, env in configs:
        res = run_worker(a.db, env, QUERIES)
        if not res:
            continue
        rss, p50, p95 = summarize(res)
        report["configs"].append({"label": label, "env": env,
                                  "peak_rss_mb": rss, "p50_ms": p50, "p95_ms": p95})
        print(f"{label:38} {rss:>9.0f}M {p50:>8.0f}ms {p95:>8.0f}ms")

    print("\n=== 2) 스레드 확장성 (GERYON_RERANK_THREADS) ===")
    print(f"{'threads':>8} {'p50':>9} {'개선율':>9}")
    thread_vals = [t for t in (1, 2, 4, 8, 12, 16, 20) if t <= a.max_threads]
    prev = None
    for t in thread_vals:
        res = run_worker(a.db, {"GERYON_RERANK_THREADS": str(t)}, QUERIES)
        if not res:
            continue
        _rss, p50, _p95 = summarize(res)
        gain = ((prev - p50) / prev * 100) if prev else None
        report["threads"].append({"threads": t, "p50_ms": p50, "gain_pct": gain})
        print(f"{t:>8} {p50:>8.0f}ms {('%+.1f%%' % gain) if gain is not None else '  (기준)':>9}")
        prev = p50

    # ---- 권장 사양 도출(측정값 기반) ----
    print("\n=== 3) 권장 사양 (측정값에서 도출) ===")
    if report["configs"]:
        peak = max(c["peak_rss_mb"] for c in report["configs"])
        lean = min(c["peak_rss_mb"] for c in report["configs"])
        peak_label = max(report["configs"], key=lambda c: c["peak_rss_mb"])["label"]
        lean_label = min(report["configs"], key=lambda c: c["peak_rss_mb"])["label"]
        print(f"  측정 피크 RSS: {peak:.0f}MB ({peak_label})  /  최소: {lean:.0f}MB ({lean_label})")
        print(f"  → 최소 RAM 권장: {peak * 1.5 / 1024:.1f}GB  (피크 × 1.5 안전계수)")
        print(f"  → 저사양 구성 시: {lean * 1.5 / 1024:.1f}GB  ({lean_label} 설정 사용)")
        report["recommend_ram_gb"] = round(peak * 1.5 / 1024, 1)

    knee = pick_optimal_threads(report["threads"])
    if knee:
        best = min(report["threads"], key=lambda r: r["p50_ms"])
        print(f"  → 권장 코어: {knee['threads']}개 ({knee['p50_ms']:.0f}ms)  "
              f"— 최속 {best['threads']}스레드({best['p50_ms']:.0f}ms) 대비 5% 이내이면서 가장 적은 스레드")
        report["recommend_cores"] = knee["threads"]

    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  결과 → {a.out}")


if __name__ == "__main__":
    main()
