import re
from typing import cast
from typing_extensions import override

from geryon.domain.models import SearchHit, SearchFilter
from geryon.search.retriever import Retriever, calculate_recency_decay
from geryon.search.keyword import KeywordRetriever
from geryon.store.repository import SqliteRepository
from geryon.store.vector import VectorStore
from geryon.embed.embedder import LocalEmbedder
import math

from geryon.config import (
    RECENCY_HALF_LIFE_DAYS, RECENCY_BOOST_CEILING, RELEVANCE_DISTANCE_THRESHOLD,
    RERANK_ENABLED, RERANK_POOL, RERANK_VEC_POOL, RERANK_PASSAGE,
)
from geryon.search.rerank import rerank_scores

# 후보 풀 하한(최종 k와 분리). 대형 코퍼스에서 recall 유지용 (후보확대).
CANDIDATE_POOL = 200

class SearchHitsList(list):
    facets: dict[str, dict[str, int]]


def highlight_snippet(text: str, query: str) -> str:
    """Helper to extract a neat snippet around query terms and highlight them with <b> tags."""
    cleaned = re.sub(r"[^\w\s]", " ", query)
    terms = [t for t in cleaned.split() if len(t) >= 2]
    if not terms:
        return text[:200] + "..." if len(text) > 200 else text

    for t in terms:
        idx = text.lower().find(t.lower())
        if idx != -1:
            start = max(0, idx - 40)
            end = min(len(text), idx + len(t) + 80)
            snippet = text[start:end]
            for term in terms:
                pattern = re.compile(re.escape(term), re.IGNORECASE)
                snippet = pattern.sub(lambda m: f"<b>{m.group(0)}</b>", snippet)
            if start > 0:
                snippet = "..." + snippet
            if end < len(text):
                snippet = snippet + "..."
            return snippet

    # Fallback to the first chunk text part
    snippet = text[:200]
    for term in terms:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        snippet = pattern.sub(lambda m: f"<b>{m.group(0)}</b>", snippet)
    if len(text) > 200:
        snippet += "..."
    return snippet

def _aggregate_max_by_doc(raw_vector_hits: list[dict[str, object]]) -> list[dict[str, object]]:
    """청크 히트를 문서별 max(score)로 집계 후 점수 내림차순 정렬."""
    best: dict[str, dict[str, object]] = {}
    for chunk in raw_vector_hits:
        d = cast(str, chunk["doc_id"])
        if d not in best or cast(float, chunk["score"]) > cast(float, best[d]["score"]):
            best[d] = chunk
    return sorted(best.values(), key=lambda c: cast(float, c["score"]), reverse=True)


class HybridRetriever(Retriever):
    """Hybrid search retriever using Reciprocal Rank Fusion (RRF) to blend keyword and vector search."""

    repository: SqliteRepository
    vector_store: VectorStore
    embedder: LocalEmbedder
    relevance_threshold: float

    def __init__(self, repository: SqliteRepository | None = None, vector_store: VectorStore | None = None,
                 relevance_threshold: float = RELEVANCE_DISTANCE_THRESHOLD,
                 repositories: list[SqliteRepository] | None = None,
                 vector_stores: list[VectorStore] | None = None) -> None:
        # federation[25]: 여러 DB 의 BM25 후보를 모아 통합 rerank. 단일 DB 는 특수 경우.
        if repositories is not None:
            self.repositories = list(repositories)
            self.vector_stores = list(vector_stores) if vector_stores else [None] * len(self.repositories)
        else:
            self.repositories = [repository]
            self.vector_stores = [vector_store]
        # RRF 폴백·static_score 경로는 대표(첫) DB 사용(rerank 주경로가 federation)
        self.repository = self.repositories[0]
        self.vector_store = self.vector_stores[0]
        self.embedder = LocalEmbedder()
        self.relevance_threshold = relevance_threshold

    @override
    def search(
        self,
        query: str,
        k: int = 10,
        offset: int = 0,
        filters: SearchFilter | None = None
    ) -> list[SearchHit]:
        if not query or not query.strip():
            return []

        # Rerank 경로(14 §7): 키워드(unicode61) 후보 → cross-encoder 재정렬, 벡터 강등.
        # reranker 미사용/실패 시 None → 아래 하이브리드(키워드+벡터 RRF)로 graceful fallback.
        if RERANK_ENABLED:
            reranked = self._rerank_search(query, k, offset, filters)
            if reranked is not None:
                return reranked

        # 후보 풀은 최종 k와 분리해 충분히 크게 가져온다(코퍼스가 커져도 recall 유지).
        # 풀이 k에 묶이면 대형 코퍼스에서 정답이 후보 진입 전에 잘려 recall이 붕괴한다.
        kw_pool = max((k + offset) * 2, CANDIDATE_POOL)
        vec_pool = max((k + offset) * 4, CANDIDATE_POOL)

        # System 1: Keyword search
        keyword_retriever = KeywordRetriever(repository=self.repository)
        keyword_hits = keyword_retriever.search(query, k=kw_pool, offset=0, filters=filters)

        # System 2: Vector search
        query_vector = self.embedder.embed_query(query)
        raw_vector_hits = self.vector_store.search_vector(query_vector, k=vec_pool, offset=0, filters=filters)

        # Chunk-to-Document aggregation: 문서별 max(score)
        vector_hits: list[dict[str, object]] = _aggregate_max_by_doc(raw_vector_hits)

        # Reciprocal Rank Fusion (RRF)
        # We extract ordered list of doc_ids
        keyword_doc_ids = [hit.doc_id for hit in keyword_hits]
        vector_doc_ids = [cast(str, hit["doc_id"]) for hit in vector_hits]

        # Maps for quick lookup of existing keyword hits and vector hits
        keyword_hit_map = {hit.doc_id: hit for hit in keyword_hits}
        vector_hit_map = {cast(str, hit["doc_id"]): hit for hit in vector_hits}

        # Apply the Search Relevance Gate:
        # Exclude vector hits that have no keyword match AND have a cosine distance > relevance_threshold (score < 1.0 - relevance_threshold).
        filtered_vector_doc_ids: list[str] = []
        for doc_id in vector_doc_ids:
            if doc_id not in keyword_hit_map:
                chunk = vector_hit_map[doc_id]
                score = cast(float, chunk["score"])
                distance = 1.0 - score
                if distance > self.relevance_threshold:
                    continue
            filtered_vector_doc_ids.append(doc_id)

        rrf_scores: dict[str, float] = {}
        constant_k = 60

        # Score keyword list
        for rank, doc_id in enumerate(keyword_doc_ids, start=1):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (constant_k + rank))

        # Score vector list (filtered by the Relevance Gate)
        for rank, doc_id in enumerate(filtered_vector_doc_ids, start=1):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0.0) + (1.0 / (constant_k + rank))

        # static_score를 RRF에 경량 가중으로 반영
        # static_score를 DB에서 배치 조회 (N+1 방지: 한 쿼리로 처리)
        STATIC_ALPHA = 0.1  # 경량 가중 (검색 속도 유지)
        conn = self.repository.get_connection()
        candidate_ids = list(rrf_scores.keys())
        if candidate_ids:
            placeholders = ",".join("?" * len(candidate_ids))
            rows = conn.execute(
                f"SELECT doc_id, static_score FROM documents WHERE doc_id IN ({placeholders})",
                candidate_ids
            ).fetchall()
            static_scores_map: dict[str, float] = {r[0]: float(r[1] or 0.0) for r in rows}
        else:
            static_scores_map = {}

        # static_score 가중 적용 후 재정렬
        def _boosted_score(doc_id: str) -> float:
            rrf = rrf_scores[doc_id]
            ss = static_scores_map.get(doc_id, 0.0)
            return rrf * (1.0 + STATIC_ALPHA * ss)

        total_candidate_doc_ids = sorted(rrf_scores.keys(), key=_boosted_score, reverse=True)
        facets: dict[str, dict[str, int]] = {
            "sources": {},
            "spaces_or_repos": {},
            "tags": {},
            "authors": {}
        }

        sorted_doc_ids = total_candidate_doc_ids[offset:offset+k]

        # Hydrate SearchHits (facets는 반환 대상 slice 기준으로만 집계)
        hits: list[SearchHit] = []
        max_rrf_possible = 2.0 / 61.0  # max score maps to 1.0

        for doc_id in sorted_doc_ids:
            doc = self.repository.get(doc_id)
            if not doc:
                continue

            rrf_score = rrf_scores[doc_id]
            base_score = min(1.0, rrf_score / max_rrf_possible)

            decay_factor = calculate_recency_decay(doc.updated_at or doc.created_at, RECENCY_HALF_LIFE_DAYS)
            static_boost = static_scores_map.get(doc_id, 0.0)
            # 최종 점수에 static_score 경량 반영
            normalized_score = base_score * (1.0 + RECENCY_BOOST_CEILING * decay_factor) * (1.0 + STATIC_ALPHA * static_boost)

            # facet 집계
            src_val = doc.source.value if hasattr(doc.source, "value") else str(doc.source)
            facets["sources"][src_val] = facets["sources"].get(src_val, 0) + 1
            if doc.space_or_repo:
                facets["spaces_or_repos"][doc.space_or_repo] = facets["spaces_or_repos"].get(doc.space_or_repo, 0) + 1
            if doc.author:
                facets["authors"][doc.author] = facets["authors"].get(doc.author, 0) + 1
            for tag in doc.tags:
                facets["tags"][tag] = facets["tags"].get(tag, 0) + 1

            # Determine snippet
            if doc_id in keyword_hit_map:
                snippet = keyword_hit_map[doc_id].snippet
            elif doc_id in vector_hit_map:
                chunk_text = cast(str, vector_hit_map[doc_id]["text"])
                snippet = highlight_snippet(chunk_text, query)
            else:
                snippet = highlight_snippet(doc.body_markdown, query)

            hits.append(
                SearchHit(
                    doc_id=doc.doc_id,
                    title=doc.title,
                    url=doc.url,
                    source=doc.source,
                    space_or_repo=doc.space_or_repo,
                    snippet=snippet,
                    score=normalized_score
                )
            )

        hits_list = SearchHitsList(hits)
        hits_list.facets = facets
        return hits_list

    def _rerank_search(
        self,
        query: str,
        k: int,
        offset: int,
        filters: SearchFilter | None,
    ) -> list[SearchHit] | None:
        """키워드 후보 → cross-encoder 재정렬. reranker 불가/키워드 무매칭이면 None(폴백)."""
        # federation[25]: 각 DB 에서 BM25 top-N 후보를 모은다(SearchHit 가 정보를 완비하므로
        # doc_id→DB 매핑 불필요). 단일 DB 면 repositories=[repo] 라 기존과 동일.
        # pool 분배: DB 수로 나눠 후보 총량을 단일 수준으로 유지(rerank 입력 폭증 방지).
        # 단일(N=1)이면 RERANK_POOL 그대로. 소스별 최소는 k+offset 보장(recall).
        n_dbs = len(self.repositories)
        per_pool = max(RERANK_POOL // n_dbs, k + offset)
        kw_hits: list[SearchHit] = []
        for repo in self.repositories:
            kw_hits.extend(KeywordRetriever(repository=repo).search(query, k=per_pool, offset=0, filters=filters))

        # 벡터 후보 합류(RERANK_VEC_POOL): 각 (repo, vector_store) 에서 보강. 키워드에 없는 doc만.
        if RERANK_VEC_POOL > 0:
            seen = {h.doc_id for h in kw_hits}
            qvec = self.embedder.embed_query(query)
            for repo, vs in zip(self.repositories, self.vector_stores):
                if vs is None:
                    continue
                try:
                    raw = vs.search_vector(qvec, k=RERANK_VEC_POOL * 4, offset=0, filters=filters)
                    for d in _aggregate_max_by_doc(raw)[:RERANK_VEC_POOL]:
                        did = cast(str, d["doc_id"])
                        if did in seen:
                            continue
                        doc = repo.get(did)
                        if doc:
                            seen.add(did)
                            kw_hits.append(SearchHit(
                                doc_id=doc.doc_id, title=doc.title, url=doc.url, source=doc.source,
                                space_or_repo=doc.space_or_repo, snippet="", score=0.0,
                            ))
                except Exception:
                    pass  # 벡터 보강 실패해도 키워드 후보로 진행

        if not kw_hits:
            # 키워드·벡터 모두 무매칭 → 하이브리드로 폴백
            return None

        # best-passage MAX 결합: cross-encoder는 (쿼리, 본문구절) 쌍 평가용인데 제목만 주면
        # "제목-본문 동떨어진 문서"(핵심이 본문에만)를 놓침(본문중심 33%). 그래서 각 후보를
        # ①제목 ②제목+쿼리매칭구절(passage) 두 입력으로 재정렬하고 점수를 max로 합친다.
        # 제목으로 맞는 정답은 ①로, 본문으로 맞는 정답은 ②로 부상 → OR. 1회 배치(2N)로 호출.
        # 측정(PoC): 골든 85→84%(-1, 노이즈), 본문중심 33→100%. RRF/무조건passage가 못 한 양립.
        n = len(kw_hits)
        has_passage = RERANK_PASSAGE and any(h.passage for h in kw_hits)
        if has_passage:
            texts = [h.title for h in kw_hits] + [
                (h.title + " " + h.passage).strip() for h in kw_hits
            ]
            both = rerank_scores(query, texts)
            if both is None:
                return None
            scores = [max(both[i], both[n + i]) for i in range(n)]
        else:
            scores = rerank_scores(query, [h.title for h in kw_hits])
            if scores is None:
                return None  # reranker 미사용 → 하이브리드 폴백

        order = sorted(range(len(kw_hits)), key=lambda i: scores[i], reverse=True)
        ranked = [(kw_hits[i], scores[i]) for i in order]

        facets: dict[str, dict[str, int]] = {
            "sources": {}, "spaces_or_repos": {}, "tags": {}, "authors": {}
        }
        hits: list[SearchHit] = []
        for hit, raw in ranked[offset:offset + k]:
            hit.score = 1.0 / (1.0 + math.exp(-raw))  # 로짓 → [0,1] 정규화
            facets["sources"][hit.source.value] = facets["sources"].get(hit.source.value, 0) + 1
            if hit.space_or_repo:
                facets["spaces_or_repos"][hit.space_or_repo] = facets["spaces_or_repos"].get(hit.space_or_repo, 0) + 1
            hits.append(hit)

        result = SearchHitsList(hits)
        result.facets = facets
        return result

    def get_related(self, doc_id: str, k: int = 10) -> list[SearchHit]:
        doc = self.repository.get(doc_id)
        if not doc:
            raise ValueError(f"not_found: Document with ID {doc_id} not found.")

        import json as _json
        hits: list[SearchHit] = []
        seen_ids: set[str] = {doc_id}

        # 1. hierarchy 트리 탐색 — 형제(0.9) > 부모(0.7)
        #    벡터 대신 문서 트리 구조로 관련 문서를 찾는다. 즉시 동작, LLM/임베딩 불필요.
        hierarchy = doc.hierarchy or []
        if len(hierarchy) >= 2:
            conn = self.repository.get_connection()
            rows = conn.execute(
                "SELECT doc_id, title, url, source, space_or_repo, hierarchy, body_markdown "
                "FROM documents WHERE source = ? AND doc_id != ?",
                (doc.source, doc_id),
            ).fetchall()

            parent_path = hierarchy[:-1]
            siblings, parents = [], []
            for row in rows:
                h = _json.loads(row[5] or "[]")
                if h[:-1] == parent_path and len(h) == len(hierarchy):
                    siblings.append(row)
                elif h == parent_path:
                    parents.append(row)

            for row, score in [(r, 0.9) for r in siblings] + [(r, 0.7) for r in parents]:
                if row[0] in seen_ids or len(hits) >= k:
                    break
                seen_ids.add(row[0])
                hits.append(SearchHit(
                    doc_id=row[0], title=row[1], url=row[2],
                    source=row[3], space_or_repo=row[4],
                    snippet=highlight_snippet(row[6] or "", doc.title),
                    score=score,
                ))

        # 2. page_links 1-hop 보강 (Confluence 링크 기반 연결 문서)
        link_doc_ids = self.repository.get_related_by_links(doc.source_id, k=k)
        for linked_id in link_doc_ids:
            if linked_id in seen_ids or len(hits) >= k:
                continue
            linked_doc = self.repository.get(linked_id)
            if not linked_doc:
                continue
            seen_ids.add(linked_id)
            hits.append(SearchHit(
                doc_id=linked_doc.doc_id, title=linked_doc.title,
                url=linked_doc.url, source=linked_doc.source,
                space_or_repo=linked_doc.space_or_repo,
                snippet=highlight_snippet(linked_doc.body_markdown, doc.title),
                score=0.5,
            ))

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]
