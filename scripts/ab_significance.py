#!/usr/bin/env python3
"""설정 A vs B 차이가 우연인지 검증 — McNemar 검정(짝지은 이진결과 비교의 표준 기법).

    python scripts/ab_significance.py <cases.yml> <db> \\
        --a RERANK=1 --b RERANK=0

golden_eval.py 를 설정 A/B 각각으로 같은 케이스 순서에 돌려 --json-out 을 받고,
같은 인덱스끼리 짝지어(A만 맞음/B만 맞음/둘다/둘다틀림) McNemar 표를 만든다.
검정 대상은 "불일치 쌍"(A만 맞음 b, B만 맞음 c) 뿐 — 둘 다 맞거나 둘 다 틀린 케이스는
차이에 기여하지 않는다(McNemar 의 핵심). n<25 면 정확이항검정, 크면 연속성보정 카이제곱.

부가: golden_bootstrap 메타(n_terms, 케이스당 희소어 개수)로 "키워드-편향 가설"도 계층분석
— A/B 승패가 n_terms 낮은(자연어에 가까운) 케이스와 높은(키워드나열) 케이스에 고르게
퍼져있는지, 아니면 한쪽에 쏠려있는지 보여준다. 쏠려있으면 "차이가 진짜 설정 효과가 아니라
골든셋의 질의 스타일 편향일 수 있다"는 신호.
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def run_config(cases_file: str, db: str, env_overrides: dict[str, str], out_json: str) -> list[dict]:
    import os
    env = os.environ.copy()
    env.update(env_overrides)
    proc = subprocess.run(
        [PY, str(HERE / "golden_eval.py"), cases_file, db, f"--json-out={out_json}"],
        env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"golden_eval.py 실패(env={env_overrides})")
    return json.loads(Path(out_json).read_text(encoding="utf-8"))


def mcnemar_exact_p(b: int, c: int) -> float:
    """정확 이항검정(양측) — b,c: 불일치 쌍 개수. scipy 없이 표준 라이브러리만으로 계산."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)

    def binom_cdf(k: int, n: int, p: float = 0.5) -> float:
        return sum(math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i)) for i in range(k + 1))

    return min(1.0, 2 * binom_cdf(k, n))


def parse_env_arg(s: str) -> dict[str, str]:
    """'RERANK=1,RERANK_POOL=20' → {'GERYON_RERANK':'1','GERYON_RERANK_POOL':'20'}"""
    out = {}
    for kv in s.split(","):
        k, _, v = kv.partition("=")
        k = k.strip()
        env_key = k if k.startswith("GERYON_") else f"GERYON_{k}"
        out[env_key] = v.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="McNemar 검정으로 설정 A/B 차이의 통계적 유의성 확인")
    ap.add_argument("cases_file")
    ap.add_argument("db")
    ap.add_argument("--a", required=True, help="설정 A, 'KEY=v,KEY2=v2' (예: RERANK=1)")
    ap.add_argument("--b", required=True, help="설정 B (예: RERANK=0)")
    ap.add_argument("--alpha", type=float, default=0.05, help="유의수준(기본 0.05)")
    a = ap.parse_args()

    scratch = Path("/tmp") / "ab_significance"
    scratch.mkdir(exist_ok=True)
    a_json, b_json = str(scratch / "a.json"), str(scratch / "b.json")

    env_a, env_b = parse_env_arg(a.a), parse_env_arg(a.b)
    print(f"설정 A = {env_a}")
    print(f"설정 B = {env_b}")
    print("\n[A 실행 중...]")
    res_a = run_config(a.cases_file, a.db, env_a, a_json)
    print("\n[B 실행 중...]")
    res_b = run_config(a.cases_file, a.db, env_b, b_json)

    if len(res_a) != len(res_b):
        raise SystemExit(f"케이스 수 불일치: A={len(res_a)} B={len(res_b)}")

    both_ok = both_bad = a_only = b_only = 0
    a_only_terms, b_only_terms = [], []
    for ra, rb in zip(res_a, res_b):
        if ra["ok"] and rb["ok"]:
            both_ok += 1
        elif not ra["ok"] and not rb["ok"]:
            both_bad += 1
        elif ra["ok"] and not rb["ok"]:
            a_only += 1
            a_only_terms.append(ra["n_terms"])
        else:
            b_only += 1
            b_only_terms.append(ra["n_terms"])

    n = len(res_a)
    print(f"\n=== McNemar 분할표 (n={n}) ===")
    print(f"           B 맞음   B 틀림")
    print(f"  A 맞음    {both_ok:>5}    {a_only:>5}")
    print(f"  A 틀림    {b_only:>5}    {both_bad:>5}")
    print(f"\n  둘 다 맞음/틀림(검정 무관): {both_ok + both_bad}")
    print(f"  A만 맞음(b): {a_only}   B만 맞음(c): {b_only}")

    p = mcnemar_exact_p(a_only, b_only)
    sig = "유의함 ✅" if p < a.alpha else "유의하지 않음(우연일 가능성) ⚠️"
    print(f"\n=== McNemar 정확검정 ===")
    print(f"  p-value = {p:.4f}  (α={a.alpha})")
    print(f"  → {sig}")
    if p >= a.alpha:
        print("  ⚠ 이 표본(n={0})으로는 A/B 차이가 통계적으로 우연과 구분되지 않습니다.".format(n))
        print("     골든셋을 늘리거나(권장 n≥100~200), 케이스 질을 높여야 신뢰 가능한 결론이 됩니다.")

    def summarize(label: str, terms: list[int]) -> None:
        if not terms:
            print(f"  {label}: 해당 케이스 없음")
            return
        avg = sum(terms) / len(terms)
        print(f"  {label}: {len(terms)}건, 평균 희소어수 {avg:.1f}개  {sorted(terms)}")

    print(f"\n=== 키워드-편향 계층분석 (n_terms = 골든 케이스의 희소어 개수) ===")
    summarize("A만 맞은 케이스", a_only_terms)
    summarize("B만 맞은 케이스", b_only_terms)
    if a_only_terms and b_only_terms:
        avg_a, avg_b = sum(a_only_terms) / len(a_only_terms), sum(b_only_terms) / len(b_only_terms)
        if abs(avg_a - avg_b) < 0.3:
            print("  → 두 그룹의 희소어수 분포가 비슷함 — 키워드 편향 가설 약화(설정 효과가 진짜일 가능성↑)")
        else:
            print(f"  → 희소어수 차이 뚜렷({avg_a:.1f} vs {avg_b:.1f}) — 승패가 질의의 '키워드성'과 "
                  f"상관될 수 있음(골든셋 편향 의심, 결론 보류 권장)")


if __name__ == "__main__":
    main()
