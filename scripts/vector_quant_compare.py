#!/usr/bin/env python3
"""벡터 양자화 비교 툴 — fp32 vs int8/binary 실제 sqlite-vec 스키마로 손실 검증.

    python scripts/vector_quant_compare.py <src_db> [--mode int8|binary] [--queries q1,q2,...]

원본 DB는 읽기 전용으로만 열고, 복사본(<src>.quant-<mode>.db)에만 스키마를 바꿔 씀.
chunk_embeddings 를 목표 타입(int8[dim]/bit[dim])으로 재생성해 데이터를 채운 뒤,
같은 질의 집합으로 fp32(원본) vs 양자화(복사본) 벡터검색 top-k 를 비교해
recall@k(겹침 비율)와 파일 크기 변화를 보고한다.

vec0 컬럼 타입은 삽입/조회 모두 SQL 함수로 명시 캐스트해야 한다(sqlite-vec 0.1.9 확인):
  삽입: vec_int8(?) / vec_bit(?)  (raw blob 파라미터를 감싸서 타입 태그)
  조회: 동일하게 질의 벡터도 캐스트해서 비교해야 함(안 하면 float32로 오인해 에러).

풀 파이프라인(FTS5+RRF+rerank)이 아니라 **벡터 검색 단계만** 비교한다 — rerank는 텍스트
기반이라 벡터 양자화 노이즈에 상대적으로 둔감하고, 후보 풀이 얼마나 흔들리는지가
핵심 질문이기 때문. 실제 서비스 채택 전 마지막 확인은 golden_eval.py 로 전체 파이프라인
hit-rate 까지 보는 걸 권장(이 툴이 만든 복사본 DB 경로를 그대로 넘기면 됨).
"""
import argparse
import array
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

REPO_SRC = str(Path(__file__).resolve().parent.parent / "src")
if REPO_SRC not in sys.path:
    sys.path.insert(0, REPO_SRC)

# 기본 질의는 **도메인 중립**으로 유지한다 — 사내 고유명(프로젝트명·컬럼명·지표명)을 넣으면
# 저장소에 사업 정보가 드러난다. 실제 코퍼스에 맞춘 질의가 필요하면 --queries 로 넘길 것.
DEFAULT_QUERIES = [
    "배포 프로세스", "정책 평가 코드", "컬럼 정의", "접근 권한 오류",
    "자동 싱크 데몬", "지표 계산", "프로모션 성과", "할인 정책",
]


def _load_vec(conn: sqlite3.Connection) -> None:
    conn.enable_load_extension(True)
    import sqlite_vec
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def build_quantized_copy(src: str, dst: str, mode: str, dim: int) -> None:
    """src 를 dst 로 복사한 뒤, dst 의 chunk_embeddings 만 목표 타입으로 재구성."""
    print(f"복사: {src} → {dst}")
    shutil.copyfile(src, dst)

    conn = sqlite3.connect(dst)
    _load_vec(conn)

    print("기존 fp32 임베딩 읽는 중...")
    rows = conn.execute("SELECT chunk_id, embedding FROM chunk_embeddings").fetchall()
    ids = [r[0] for r in rows]
    mat = np.zeros((len(rows), dim), dtype=np.float32)
    for i, (_, blob) in enumerate(rows):
        mat[i] = np.frombuffer(blob, dtype=np.float32)

    conn.execute("DROP TABLE chunk_embeddings")
    if mode == "int8":
        conn.execute(f"CREATE VIRTUAL TABLE chunk_embeddings USING vec0(chunk_id TEXT, embedding int8[{dim}])")
        lo, hi = mat.min(), mat.max()
        scale = (hi - lo) / 255.0
        q = np.round((mat - lo) / scale).astype(np.int8)  # int8 범위(-128~127)로 시프트해 저장
        # sqlite-vec int8 은 부호있는 바이트이므로 0..255 스케일을 -128..127 로 이동
        q = (np.round((mat - lo) / scale) - 128).astype(np.int8)
        cast = "vec_int8(?)"
        rows_out = [(cid, array.array("b", q[i].tolist()).tobytes()) for i, cid in enumerate(ids)]
        meta = {"lo": lo, "scale": scale}
    elif mode == "binary":
        conn.execute(f"CREATE VIRTUAL TABLE chunk_embeddings USING vec0(chunk_id TEXT, embedding bit[{dim}])")
        bits = (mat >= 0).astype(np.uint8)
        packed = np.packbits(bits, axis=1)
        cast = "vec_bit(?)"
        rows_out = [(cid, packed[i].tobytes()) for i, cid in enumerate(ids)]
        meta = {}
    else:
        raise ValueError(f"unknown mode: {mode}")

    print(f"{mode} 재삽입 중... ({len(rows_out)}건)")
    t0 = time.time()
    conn.executemany(f"INSERT INTO chunk_embeddings(chunk_id, embedding) VALUES (?, {cast})", rows_out)
    conn.commit()
    print(f"  ({time.time()-t0:.1f}초)")
    conn.close()
    return meta


def vector_topk(conn: sqlite3.Connection, query_blob: bytes, cast: str, k: int) -> list[tuple[str, str]]:
    """(chunk_id, doc_title) 목록 — chunks/documents 조인해서 사람이 읽을 제목까지."""
    sql = f"""
        SELECT c.chunk_id, d.title, vec_distance_cosine(e.embedding, {cast}) AS dist
        FROM chunk_embeddings e
        JOIN chunks c ON e.chunk_id = c.chunk_id
        LEFT JOIN documents d ON c.doc_id = d.doc_id
        ORDER BY dist ASC LIMIT ?
    """
    return [(r[0], r[1]) for r in conn.execute(sql, (query_blob, k)).fetchall()]


def recall_at_k(gt: list[tuple[str, str]], cand: list[tuple[str, str]]) -> float:
    gt_ids = {c for c, _ in gt}
    cand_ids = {c for c, _ in cand}
    return len(gt_ids & cand_ids) / len(gt_ids) if gt_ids else 0.0


def compare(src: str, dst: str, mode: str, queries: list[str], dim: int, ks: tuple[int, ...] = (10, 20, 50)) -> None:
    from geryon.embed.embedder import LocalEmbedder
    embedder = LocalEmbedder()

    conn_src = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    _load_vec(conn_src)
    conn_dst = sqlite3.connect(dst)
    _load_vec(conn_dst)

    quant_cast = {"int8": "vec_int8(?)", "binary": "vec_bit(?)"}[mode]

    print(f"\n=== 벡터 검색 top-k 비교: fp32(원본) vs {mode}(복사본) ===")
    results: dict[int, list[float]] = {k: [] for k in ks}
    for q in queries:
        qvec = embedder.embed_query(q)
        fp32_blob = array.array("f", qvec).tobytes()
        # int8/binary 는 질의 벡터도 같은 스케일/타입으로 캐스트해야 비교 가능
        if mode == "int8":
            arr = np.asarray(qvec, dtype=np.float32)
            lo, hi = arr.min(), arr.max()
            scale = (hi - lo) / 255.0 if hi > lo else 1.0
            qint8 = (np.round((arr - lo) / scale) - 128).astype(np.int8)
            quant_blob = array.array("b", qint8.tolist()).tobytes()
        else:
            bits = (np.asarray(qvec, dtype=np.float32) >= 0).astype(np.uint8)
            quant_blob = np.packbits(bits).tobytes()

        max_k = max(ks)
        gt = vector_topk(conn_src, fp32_blob, "?", max_k)
        cand = vector_topk(conn_dst, quant_blob, quant_cast, max_k)
        print(f"\n  질의: {q!r}")
        print(f"    fp32   top3: {[t[:30] for _, t in gt[:3]]}")
        print(f"    {mode:6s} top3: {[t[:30] for _, t in cand[:3]]}")
        for k in ks:
            r = recall_at_k(gt[:k], cand[:k])
            results[k].append(r)

    print(f"\n=== Recall@k 요약 (질의 {len(queries)}개 평균) ===")
    for k in ks:
        print(f"  k={k:>3}: {np.mean(results[k]):.4f}")

    src_size = Path(src).stat().st_size / 1024 / 1024
    dst_size = Path(dst).stat().st_size / 1024 / 1024
    print(f"\n=== 파일 크기 ===")
    print(f"  원본(fp32):     {src_size:.1f} MB")
    print(f"  복사본({mode}): {dst_size:.1f} MB  (VACUUM 안 한 상태 — 실제 절감은 이보다 큼)")
    print(f"\n다음 단계: golden_eval.py 로 전체 파이프라인(FTS5+rerank) 확인 가능:")
    print(f"  python scripts/golden_eval.py tests/golden/cases_sample.yml {dst}")

    conn_src.close()
    conn_dst.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="fp32 vs int8/binary 벡터 양자화 손실 비교 툴")
    ap.add_argument("src_db")
    ap.add_argument("--mode", choices=["int8", "binary"], default="int8")
    ap.add_argument("--dst", default=None, help="복사본 경로(기본: <src>.quant-<mode>.db)")
    ap.add_argument("--queries", default=None, help="콤마구분 질의(기본: 내장 샘플 8개)")
    ap.add_argument("--dim", type=int, default=384)
    ap.add_argument("--keep-copy", action="store_true", help="비교 후 복사본 파일 유지(기본은 유지)")
    a = ap.parse_args()

    dst = a.dst or str(Path(a.src_db).with_suffix(f".quant-{a.mode}.db"))
    queries = [q.strip() for q in a.queries.split(",")] if a.queries else DEFAULT_QUERIES

    if not Path(dst).exists():
        build_quantized_copy(a.src_db, dst, a.mode, a.dim)
    else:
        print(f"복사본 이미 존재, 재사용: {dst} (재생성하려면 삭제 후 재실행)")

    compare(a.src_db, dst, a.mode, queries, a.dim)


if __name__ == "__main__":
    main()
