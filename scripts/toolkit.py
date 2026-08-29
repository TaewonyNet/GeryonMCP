#!/usr/bin/env python3
"""통합 검색 품질 툴킷 — 흩어진 스크립트(dict_bootstrap/term_bootstrap/golden_eval/
vector_quant_compare)를 서브커맨드 하나로 묶고, 설정(GERYON_RERANK_* 등)을 바꿔가며
골든셋으로 벤치마크하는 `bench` 를 추가한다.

    python scripts/toolkit.py <subcommand> ...

서브커맨드:
  dict    <db1[,db2]> [dict_bootstrap.py 옵션]     — 동의어 사전 초안
  term    <db1[,db2]> [term_bootstrap.py 옵션]     — Term Contract 초안
  quant   <db> [vector_quant_compare.py 옵션]      — fp32 vs int8/binary 손실 비교
  golden  <cases.yml> <db>                         — 골든셋 hit-rate 1회 측정
  golden-llm <db> [golden_llm.py 옵션]             — LLM 으로 자연어 골든셋 생성(로컬 Ollama)
  bench   <cases.yml> <db> --sweep KEY=v1,v2,...   — 설정별 golden hit-rate·소요시간 비교표

각 서브커맨드는 기존 스크립트를 그대로 서브프로세스로 호출(독립 실행 호환 유지) —
단, bench 는 설정 조합마다 **새 프로세스**로 돌려야 한다: GERYON_* 는 config.py 가
import 시점에 한 번만 읽으므로, 같은 프로세스 안에서 env 만 바꿔서는 반영되지 않는다.
"""
import argparse
import itertools
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def _run(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    return subprocess.run(args, env=full_env, capture_output=True, text=True)


def cmd_passthrough(script: str, argv: list[str]) -> None:
    """dict/term/quant — 기존 스크립트에 인자 그대로 전달, 출력 실시간 표시."""
    proc = subprocess.run([PY, str(HERE / script), *argv])
    sys.exit(proc.returncode)


def _parse_golden_output(stdout: str) -> tuple[int, int]:
    """golden_eval.py 출력의 'hit N/M (P%)' 줄에서 N, M 파싱."""
    import re
    m = re.search(r"hit (\d+)/(\d+)", stdout)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def cmd_golden(argv: list[str]) -> None:
    proc = subprocess.run([PY, str(HERE / "golden_eval.py"), *argv])
    sys.exit(proc.returncode)


def _sweep_combos(sweep_args: list[str]) -> list[dict[str, str]]:
    """['RERANK_POOL=20,60,120', 'RERANK_QUANTIZE=0,1'] → 각 축의 값 리스트 딕셔너리 후 카티전곱."""
    axes: dict[str, list[str]] = {}
    for s in sweep_args:
        key, _, values = s.partition("=")
        if not key or not values:
            raise SystemExit(f"--sweep 형식 오류: {s!r} (KEY=v1,v2,... 형식이어야 함)")
        env_key = key if key.startswith("GERYON_") else f"GERYON_{key}"
        axes[env_key] = [v.strip() for v in values.split(",") if v.strip()]

    keys = list(axes.keys())
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*(axes[k] for k in keys))]
    return combos


def cmd_bench(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="toolkit.py bench", description="설정별 골든셋 벤치마크")
    ap.add_argument("cases_file")
    ap.add_argument("db")
    ap.add_argument("--sweep", action="append", default=[],
                    help="KEY=v1,v2,... (GERYON_ 접두어 생략 가능, 여러 번 지정 시 카티전곱). "
                         "예: --sweep RERANK_POOL=20,60,120 --sweep RERANK_QUANTIZE=0,1")
    ap.add_argument("--max-combos", type=int, default=24, help="조합 상한(과도한 스윕 방지, 기본 24)")
    a = ap.parse_args(argv)

    combos = _sweep_combos(a.sweep) if a.sweep else [{}]
    if len(combos) > a.max_combos:
        raise SystemExit(f"조합 {len(combos)}개가 상한({a.max_combos})을 초과합니다 — "
                         f"--sweep 축을 줄이거나 --max-combos 를 올리세요.")

    print(f"=== bench: {len(combos)}개 설정 조합 × {a.cases_file} ===\n")
    rows: list[tuple[dict, int, int, float]] = []
    for i, combo in enumerate(combos, 1):
        label = ", ".join(f"{k.removeprefix('GERYON_')}={v}" for k, v in combo.items()) or "(기본값)"
        print(f"[{i}/{len(combos)}] {label} ...", end=" ", flush=True)
        t0 = time.time()
        proc = _run([PY, str(HERE / "golden_eval.py"), a.cases_file, a.db], env=combo)
        elapsed = time.time() - t0
        hit, total = _parse_golden_output(proc.stdout)
        rows.append((combo, hit, total, elapsed))
        pct = hit * 100 // total if total else 0
        print(f"hit {hit}/{total} ({pct}%) · {elapsed:.1f}s")

    print("\n=== 결과 요약 (hit-rate 내림차순, 동률이면 빠른 순) ===")
    rows.sort(key=lambda r: (-r[1], r[3]))
    for combo, hit, total, elapsed in rows:
        label = ", ".join(f"{k.removeprefix('GERYON_')}={v}" for k, v in combo.items()) or "(기본값)"
        pct = hit * 100 // total if total else 0
        print(f"  {label:50s} hit {hit}/{total} ({pct:3d}%)  {elapsed:6.1f}s")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sub, rest = sys.argv[1], sys.argv[2:]
    if sub == "dict":
        cmd_passthrough("dict_bootstrap.py", rest)
    elif sub == "term":
        cmd_passthrough("term_bootstrap.py", rest)
    elif sub == "quant":
        cmd_passthrough("vector_quant_compare.py", rest)
    elif sub == "golden":
        cmd_golden(rest)
    elif sub == "golden-llm":
        cmd_passthrough("golden_llm.py", rest)
    elif sub == "bench":
        cmd_bench(rest)
    else:
        print(f"알 수 없는 서브커맨드: {sub!r}\n")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
