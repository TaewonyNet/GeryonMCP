import logging
from datetime import datetime, timezone
from geryon.connectors.base import Connector
from geryon.connectors.confluence import ConfluenceConnector
from geryon.connectors.jira import JiraConnector
from geryon.connectors.git_repo import GitRepoConnector
from geryon.store.repository import Repository, SqliteRepository
from geryon.normalize.normalizer import Normalizer
from geryon.config import DB_PATH
from geryon.store.vector import VectorStore
from geryon.embed.embedder import LocalEmbedder
from geryon.store.tree import TreeStore, build_document_tree
from geryon.domain.models import SyncState, Document
from geryon.pipeline.quality_signals import (
    build_page_links_from_docs,
    compute_backlink_counts,
    compute_static_scores,
)

logger = logging.getLogger(__name__)

# Connector Registry mapping source strings or SourceType to Connector classes
CONNECTOR_REGISTRY: dict[str, type[Connector]] = {
    "confluence": ConfluenceConnector,
    "jira": JiraConnector,
    "git": GitRepoConnector,  # Bitbucket·GitLab·GitHub 로컬 클론 공통
}

class PruneSafetyViolation(Exception):
    """Raised when deletion safety gate is violated."""
    pass

class IngestionPipeline:
    """Ingestion pipeline that manages health checking, retrieving raw records from
    a connector, normalizing them, and upserting into the repository.
    """
    repository: Repository
    vector_store: VectorStore | None
    tree_store: TreeStore | None
    embedder: LocalEmbedder

    def __init__(
        self,
        repository: Repository | None = None,
        vector_store: VectorStore | None = None,
        tree_store: TreeStore | None = None,
    ) -> None:
        if repository is None:
            self.repository = SqliteRepository(DB_PATH)
        else:
            self.repository = repository
        self.vector_store = vector_store
        self.tree_store = tree_store
        self.embedder = LocalEmbedder()

    def run(self, connector: Connector, full_reindex: bool = False, prune: bool = True,
            incremental: bool = False) -> dict:
        if not connector.healthcheck():
            raise RuntimeError(f"Connector healthcheck failed for {connector.__class__.__name__}")

        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        mode = "full" if (full_reindex or not incremental) else "incremental"
        # 커넥터가 증분(mtime)을 지원하지 않으면(git 등) 자동으로 전체+prune
        if mode == "incremental" and not getattr(connector, "supports_incremental", True):
            mode = "full"

        # 증분: 마지막 동기화 시각 이후 수정된 Bronze 만 순회(mtime > since).
        # 증분 대상은 Bronze manifest.last_change(added+modified). [24_BRONZE_CONTRACT]
        only: set[str] | None = None
        first_run = False
        if mode == "incremental":
            prev_state = self.repository.get_sync_state(connector.source_type)
            if prev_state is None or prev_state.doc_count == 0:
                first_run = True  # 첫 색인(DB 비어있음) → 전체. 이후부터 manifest 증분
            else:
                from geryon.acquire.manifest import read_manifest
                root = getattr(connector, "db_path", None)
                mani = read_manifest(root) if root is not None else None
                lc = mani.get("last_change") if mani else None
                if lc is not None:
                    only = set(lc.get("added") or []) | set(lc.get("modified") or [])
                # lc 없으면(기존 manifest 없는 DB) only=None=전체 — 안전
        # 증분은 변경분만 보므로 prune(전체 seen 비교)·quality(전역 재계산)를 건너뛴다.
        do_prune = prune and mode == "full"
        do_quality = mode == "full"

        if do_prune:
            self.repository.clear_seen_docs(connector.source_type)

        stats: dict = {"mode": mode, "started_at": started_at.isoformat(),
                       "inserted": 0, "updated": 0, "skipped": 0, "deleted": 0, "errors": 0}
        normalizer = Normalizer()

        docs_for_quality: list[tuple[str, str, str, str]] = []
        all_indexed_docs: list[Document] = []

        for record in connector.iter_raw(only):
            try:
                doc = normalizer.normalize(record)
                status = self.repository.upsert(doc, force=full_reindex)  # inserted/updated/skipped
                stats[status] = stats.get(status, 0) + 1
                if status != "skipped":
                    chunks = self.embedder.chunk_document(doc.doc_id, doc.body_markdown)
                    if self.vector_store is not None and chunks:
                        chunk_texts = [c.text for c in chunks]
                        embeddings = self.embedder.embed_passages(chunk_texts)
                        chunks_data = [
                            (c.chunk_id, c.ordinal, c.text, emb)
                            for c, emb in zip(chunks, embeddings)
                        ]
                        self.vector_store.save_chunks(doc.doc_id, chunks_data)
                    if self.tree_store is not None:
                        nodes = build_document_tree(doc, chunks)
                        self.tree_store.replace_doc_nodes(doc.doc_id, nodes)

                if do_quality:
                    # 품질 신호(backlink)는 전역 그래프가 필요 — full 일 때만 누적
                    raw_body = record.raw_body or ""
                    space = doc.space_or_repo or ""
                    docs_for_quality.append((doc.source_id, space, doc.title, raw_body))
                    all_indexed_docs.append(doc)
                if do_prune:
                    self.repository.add_seen_doc(connector.source_type, doc.doc_id)
            except Exception as e:
                logger.error(f"Error processing record: {e}", exc_info=True)
                stats["errors"] += 1

        # Bronze 원본에서 page_links + static_score 자체 계산·저장 (full 만)
        if do_quality and docs_for_quality:
            try:
                logger.info("page_links 자체 추출 시작 (%d 문서)", len(docs_for_quality))
                links = build_page_links_from_docs(docs_for_quality)
                if isinstance(self.repository, SqliteRepository):
                    self.repository.upsert_page_links(links)
                    logger.info("page_links 저장 완료: %d건", len(links))

                    backlink_counts = compute_backlink_counts(links)
                    logger.info("static_score 자체 계산 시작 (%d 문서)", len(all_indexed_docs))
                    scores = compute_static_scores(all_indexed_docs, backlink_counts)
                    updated = self.repository.update_static_scores(scores)
                    logger.info("static_score 저장 완료: %d건 업데이트", updated)
                    stats["quality_links"] = len(links)
                    stats["quality_scores"] = updated
            except Exception as e:
                logger.error("품질 신호 자체 계산 오류: %s", e, exc_info=True)

        if do_prune:
            seen_ids = self.repository.get_seen_docs(connector.source_type)
            prev_state = self.repository.get_sync_state(connector.source_type)
            if prev_state is not None:
                prev_doc_count = prev_state.doc_count
                seen_count = len(seen_ids)
                if not full_reindex:
                    if seen_count == 0 and prev_doc_count > 0:
                        raise PruneSafetyViolation(
                            f"Safety Gate Violated: 0 documents seen, but previous doc count was {prev_doc_count}!"
                        )
                    if prev_doc_count > 5 and seen_count < prev_doc_count * 0.5:
                        raise PruneSafetyViolation(
                            f"Safety Gate Violated: Critical drop in document count from {prev_doc_count} to {seen_count}!"
                        )

            stored_ids = self.repository.get_doc_ids_by_source(connector.source_type)
            deleted_ids = set(stored_ids) - set(seen_ids)

            for doc_id in deleted_ids:
                self.repository.delete(doc_id)
                if self.vector_store is not None:
                    self.vector_store.save_chunks(doc_id, [])
                stats["deleted"] += 1

        finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        active_ids = self.repository.get_doc_ids_by_source(connector.source_type)
        new_state = SyncState(
            source=connector.source_type,
            last_synced_at=finished_at,  # 다음 증분의 기준(mtime > 이 시각)
            last_seen_doc_ids=active_ids,
            doc_count=len(active_ids),
        )
        self.repository.upsert_sync_state(new_state)

        # 갱신 메타: 시작/종료 시각, 모드, 덮어쓰기(updated)/신규(inserted)/건너뜀(skipped)/삭제(deleted)
        stats["finished_at"] = finished_at.isoformat()
        stats["indexed"] = stats["inserted"] + stats["updated"]  # 하위호환(기존 키)
        stats["first_run"] = first_run
        return stats
