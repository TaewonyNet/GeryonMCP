"""무앵커 주제 발견 + 탈선 분리 — BERTopic 계열의 경량(numpy) 변형.

흐름:
  1. 문서 → 조각(passage) 분리
  2. e5 임베딩(기존 LocalEmbedder, 신규 모델 0)
  3. 코사인 kNN 그래프 + 연결요소 = 주제 군집
       · 어떤 조각과도 약하게 연결(고립) = OFF_TOPIC(탈선)
  4. 교차문서 지지도(여러 문서 반복=정당 주제 / 한 문서만=탈선 후보)
  5. c-TF-IDF 로 군집 대표어 추출(형태소 분석기 불필요)

결정적·CPU·오프라인. UMAP/HDBSCAN/sklearn 미사용.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from geryon.embed.embedder import LocalEmbedder
from geryon.index.korean import normalize_korean

OFF_TOPIC = -1


@dataclass
class Passage:
    """문서에서 분리된 한 조각."""
    doc_id: str
    ordinal: int
    text: str
    cluster: int = OFF_TOPIC          # 귀속 군집 id (OFF_TOPIC = 탈선)
    affinity: float = 0.0             # 가장 가까운 이웃과의 코사인(=연결 강도)


@dataclass
class Cluster:
    """발견된 주제 군집."""
    cluster_id: int
    passage_idx: list[int] = field(default_factory=list)
    doc_support: int = 0              # 기여한 distinct 문서 수(교차문서 지지도)
    keywords: list[str] = field(default_factory=list)  # c-TF-IDF 대표어
    subject_affinity: float = 0.0     # 로버스트 전역중심(본 주제 질량)과의 유사도
    off_topic: bool = False           # 본 주제 우산 밖(탈선 군집)

    @property
    def size(self) -> int:
        return len(self.passage_idx)


@dataclass
class AnalyzeResult:
    passages: list[Passage]
    clusters: list[Cluster]
    off_topic_idx: list[int]
    threshold: float                  # 적용된 엣지 임계(μ+ασ)


# ---------------------------------------------------------------- 조각 분리

_SENT_SPLIT = re.compile(r"(?<=[.!?。…])\s+|\n+")


def split_passages(doc_id: str, text: str) -> list[Passage]:
    """문단/문장 단위로 조각 분리(경계 정밀도 불필요 — 자르는 게 목적 아님)."""
    chunks = [c.strip() for c in _SENT_SPLIT.split(text) if c and c.strip()]
    return [Passage(doc_id=doc_id, ordinal=i, text=c) for i, c in enumerate(chunks)]


# ---------------------------------------------------------------- 군집화

class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _build_clusters(
    emb: np.ndarray,
    k: int,
    alpha: float,
    min_size: int,
    center: bool = True,
) -> tuple[np.ndarray, np.ndarray, float]:
    """코사인 kNN 그래프 → 연결요소. 반환 (labels, affinity, threshold).

    e5 코사인은 기저값이 0.9 안팎으로 압축돼 절대 임계가 무의미하다.
    → 평균 중심화(anisotropy 제거, "all-but-the-top")로 공간을 펴서 유사도 분산 확보,
      그 위에서 분포 기반 μ+ασ 임계 + 상호 kNN 으로 군집/고립 판정.
    """
    n = emb.shape[0]
    if center and n > 1:
        emb = emb - emb.mean(axis=0, keepdims=True)        # 공통성분(평균방향) 제거
        emb = emb / np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12, None)
    sims = emb @ emb.T                       # 정규화 벡터 → dot=cosine
    np.fill_diagonal(sims, -np.inf)

    # 분포 기반 임계 (off-diagonal 상삼각)
    iu = np.triu_indices(n, k=1)
    flat = sims[iu]
    finite = flat[np.isfinite(flat)]
    thr = float(finite.mean() + alpha * finite.std()) if finite.size else 0.0

    # 각 노드의 top-k 이웃(자기 제외)
    topk = np.argsort(-sims, axis=1)[:, :k]
    neigh = [set(int(j) for j in topk[i]) for i in range(n)]

    affinity = np.array([float(sims[i, topk[i, 0]]) if k else 0.0 for i in range(n)])

    uf = _UnionFind(n)
    for i in range(n):
        for j in neigh[i]:
            if i in neigh[j] and sims[i, j] >= thr:   # 상호 kNN + 임계
                uf.union(i, j)

    # 라벨 정리 + 최소 크기 미만은 OFF_TOPIC
    comp: dict[int, list[int]] = {}
    for i in range(n):
        comp.setdefault(uf.find(i), []).append(i)

    labels = np.full(n, OFF_TOPIC, dtype=int)
    cid = 0
    for members in comp.values():
        if len(members) >= min_size:
            for m in members:
                labels[m] = cid
            cid += 1
    return labels, affinity, thr


# ---------------------------------------------------------------- c-TF-IDF

_STOP = {"그리고", "그래서", "근데", "그게", "저기", "그거", "이거", "거기", "여기",
         "정도", "조금", "그런", "이런", "저런", "하는", "되는", "해서", "있는",
         "그럼", "그러", "네네", "음음", "아니", "그냥"}


def _tokens(text: str) -> list[str]:
    return [t for t in normalize_korean(text).split() if len(t) >= 2 and t not in _STOP]


def _robust_global_centroid(emb: np.ndarray, trim: float = 0.2) -> np.ndarray:
    """본 주제 질량 = 전역중심. 단, 잡담 오염 방지 위해 중심에서 먼 trim 비율은 버리고 재계산."""
    g = emb.mean(0)
    g /= np.clip(np.linalg.norm(g), 1e-12, None)
    sims = emb @ g
    keep = sims >= np.quantile(sims, trim)
    g = emb[keep].mean(0)
    return g / np.clip(np.linalg.norm(g), 1e-12, None)


def _flag_off_topic(clusters: list[Cluster], emb: np.ndarray, alpha: float) -> None:
    """군집 단위로 본 주제 우산 밖(탈선)을 판정(in-place).

    탈선은 고립되지 않고 잡담끼리 군집을 이루며 교차문서로도 반복된다(실측).
    → 각 군집 중심의 '로버스트 전역중심' 유사도를 보고, 분포 하단 이상치(μ-ασ)를 탈선으로.
    군집이 3개 미만이면 분포가 불안정 → 판정 보류.
    """
    if len(clusters) < 3:
        return
    g = _robust_global_centroid(emb)
    scores = []
    for c in clusters:
        cc = emb[c.passage_idx].mean(0)
        cc /= np.clip(np.linalg.norm(cc), 1e-12, None)
        c.subject_affinity = float(cc @ g)
        scores.append(c.subject_affinity)
    arr = np.asarray(scores)
    cut = float(arr.mean() - alpha * arr.std())
    for c in clusters:
        c.off_topic = c.subject_affinity < cut


def _ctfidf(clusters: list[Cluster], passages: list[Passage], top: int = 6) -> None:
    """군집별 대표어를 c-TF-IDF 로 추출해 cluster.keywords 에 채운다(in-place)."""
    # 군집 단위 term frequency
    tf: list[dict[str, int]] = []
    for c in clusters:
        bag: dict[str, int] = {}
        for idx in c.passage_idx:
            for t in _tokens(passages[idx].text):
                bag[t] = bag.get(t, 0) + 1
        tf.append(bag)

    # 전역 빈도 f_t (몇 개 군집에 등장하는 총량) + 평균 군집 토큰 수 A
    global_freq: dict[str, int] = {}
    for bag in tf:
        for t, v in bag.items():
            global_freq[t] = global_freq.get(t, 0) + v
    A = (sum(sum(b.values()) for b in tf) / len(tf)) if tf else 1.0

    for c, bag in zip(clusters, tf):
        scored = [
            (t, v * math.log(1.0 + A / global_freq[t]))
            for t, v in bag.items()
        ]
        scored.sort(key=lambda x: -x[1])
        c.keywords = [t for t, _ in scored[:top]]


# ---------------------------------------------------------------- 오케스트레이션

def analyze(
    documents: list[tuple[str, str]],
    *,
    k: int = 3,
    alpha: float = 1.0,
    min_size: int = 2,
    min_doc_support: int = 1,
    offtopic_alpha: float = 1.0,
) -> AnalyzeResult:
    """documents: [(doc_id, text), ...] → 주제 군집 + 탈선 분리.

    탈선 판정(검증된 방식): 군집 단위 × 로버스트 전역중심 유사도의 하단 이상치(μ-offtopic_alpha·σ).
    min_doc_support>1 이면 교차문서 지지도 미달 군집을 추가로 탈선 강등(보조).
    """
    passages: list[Passage] = []
    for doc_id, text in documents:
        passages.extend(split_passages(doc_id, text))
    if not passages:
        return AnalyzeResult([], [], [], 0.0)

    embedder = LocalEmbedder()
    vecs = embedder.embed_passages([p.text for p in passages])
    emb = np.asarray(vecs, dtype=np.float32)
    emb /= np.clip(np.linalg.norm(emb, axis=1, keepdims=True), 1e-12, None)

    labels, affinity, thr = _build_clusters(emb, k=k, alpha=alpha, min_size=min_size)

    for i, p in enumerate(passages):
        p.cluster = int(labels[i])
        p.affinity = float(affinity[i])

    # 군집 구성 + 교차문서 지지도
    by_label: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        if lab != OFF_TOPIC:
            by_label.setdefault(int(lab), []).append(i)

    clusters: list[Cluster] = []
    for cid, idxs in sorted(by_label.items()):
        docs = {passages[i].doc_id for i in idxs}
        clusters.append(Cluster(cluster_id=cid, passage_idx=idxs, doc_support=len(docs)))

    # 탈선 판정 — 군집 단위 × 로버스트 전역중심 (검증된 1차)
    _flag_off_topic(clusters, emb, alpha=offtopic_alpha)
    # 교차문서 지지도 미달 → 추가 강등 (보조, 기본 off)
    if min_doc_support > 1:
        for c in clusters:
            if c.doc_support < min_doc_support:
                c.off_topic = True
    for c in clusters:
        if c.off_topic:
            for i in c.passage_idx:
                passages[i].cluster = OFF_TOPIC

    _ctfidf(clusters, passages, top=6)

    off_idx = [i for i, p in enumerate(passages) if p.cluster == OFF_TOPIC]
    return AnalyzeResult(passages=passages, clusters=clusters,
                         off_topic_idx=off_idx, threshold=thr)
