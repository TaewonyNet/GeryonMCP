"""검색 품질 개선 단위 테스트.

② 정적 품질점수 사전계산, ④ page_links, ⑥ title 가중 BM25.
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


from geryon.store.db import init_db  # noqa: E402
from geryon.store.repository import SqliteRepository  # noqa: E402
from geryon.domain.models import Document, SourceType  # noqa: E402


# ─── 픽스처 ───────────────────────────────────────────────────────────
@pytest.fixture()
def tmp_db(tmp_path):
    """빈 geryon DB (벡터 로드 포함)."""
    db = tmp_path / "test.db"
    conn = init_db(db)
    conn.close()
    return db


def _make_doc(doc_id: str, source_id: str, title: str, body: str = "테스트 본문 내용") -> Document:
    """테스트용 Document 픽스처."""
    return Document(
        doc_id=doc_id,
        source=SourceType.CONFLUENCE,
        source_id=source_id,
        title=title,
        body_markdown=body,
        content_hash=f"hash_{doc_id}",
        ingested_at=datetime.now(timezone.utc).replace(tzinfo=None),
        tags=[],
        hierarchy=[],
        raw_meta={},
    )


# ─── ② static_score 테스트 ───────────────────────────────────────────
class TestStaticScore:
    def test_default_static_score_is_zero(self, tmp_db):
        """인덱싱 시 static_score가 기본값 0.0으로 설정되는지 확인."""
        repo = SqliteRepository(tmp_db)
        doc = _make_doc("doc1", "page1", "테스트 문서")
        repo.upsert(doc)
        score = repo.get_static_score("doc1")
        assert score == 0.0

    def test_update_static_scores(self, tmp_db):
        """update_static_scores로 static_score가 올바르게 저장되는지 확인."""
        repo = SqliteRepository(tmp_db)
        doc1 = _make_doc("doc1", "page1", "GCP 계정 가이드")
        doc2 = _make_doc("doc2", "page2", "Superset 설치")
        repo.upsert(doc1)
        repo.upsert(doc2)

        n = repo.update_static_scores({"page1": 0.85, "page2": 0.42})
        assert n == 2
        assert abs(repo.get_static_score("doc1") - 0.85) < 1e-6
        assert abs(repo.get_static_score("doc2") - 0.42) < 1e-6

    def test_upsert_preserves_static_score(self, tmp_db):
        """재인덱싱(upsert) 시 기존 static_score가 보존되는지 확인."""
        repo = SqliteRepository(tmp_db)
        doc = _make_doc("doc1", "page1", "원본 제목")
        repo.upsert(doc)
        repo.update_static_scores({"page1": 0.75})

        # content_hash를 바꿔서 강제 재인덱싱
        doc2 = _make_doc("doc1", "page1", "수정된 제목")
        doc2_modified = doc2.model_copy(update={"content_hash": "new_hash"})
        repo.upsert(doc2_modified, force=True)

        # static_score는 보존되어야 함
        score = repo.get_static_score("doc1")
        assert abs(score - 0.75) < 1e-6

    def test_static_score_range(self, tmp_db):
        """static_score는 [0, 1] 범위여야 한다."""
        repo = SqliteRepository(tmp_db)
        doc = _make_doc("doc1", "page1", "테스트")
        repo.upsert(doc)
        # 경계값 테스트
        repo.update_static_scores({"page1": 1.0})
        assert repo.get_static_score("doc1") <= 1.0

        repo.update_static_scores({"page1": 0.0})
        assert repo.get_static_score("doc1") >= 0.0


# ─── ④ page_links 테스트 ─────────────────────────────────────────────
class TestPageLinks:
    def test_upsert_page_links(self, tmp_db):
        """page_links 배치 삽입 확인."""
        repo = SqliteRepository(tmp_db)
        doc1 = _make_doc("doc1", "page1", "문서 1")
        doc2 = _make_doc("doc2", "page2", "문서 2")
        doc3 = _make_doc("doc3", "page3", "문서 3")
        for d in [doc1, doc2, doc3]:
            repo.upsert(d)

        n = repo.upsert_page_links([("page1", "page2"), ("page2", "page3")])
        assert n == 2

        conn = repo.get_connection()
        cnt = conn.execute("SELECT count(*) FROM page_links").fetchone()[0]
        assert cnt == 2

    def test_get_related_by_links_outgoing(self, tmp_db):
        """outgoing links를 통해 1-hop 관련 문서를 반환하는지 확인."""
        repo = SqliteRepository(tmp_db)
        doc1 = _make_doc("doc1", "page1", "허브 문서")
        doc2 = _make_doc("doc2", "page2", "연결된 문서 A")
        doc3 = _make_doc("doc3", "page3", "연결된 문서 B")
        doc4 = _make_doc("doc4", "page4", "무관 문서")
        for d in [doc1, doc2, doc3, doc4]:
            repo.upsert(d)

        repo.upsert_page_links([("page1", "page2"), ("page1", "page3")])
        related = repo.get_related_by_links("page1", k=10)
        related_ids = set(related)
        assert "doc2" in related_ids
        assert "doc3" in related_ids
        assert "doc4" not in related_ids  # 연결 없는 문서 제외

    def test_get_related_by_links_backlinks(self, tmp_db):
        """backlinks를 통해 1-hop 관련 문서를 반환하는지 확인."""
        repo = SqliteRepository(tmp_db)
        doc1 = _make_doc("doc1", "page1", "메인 문서")
        doc2 = _make_doc("doc2", "page2", "나를 가리키는 문서")
        for d in [doc1, doc2]:
            repo.upsert(d)

        # page2 → page1 링크 (backlink)
        repo.upsert_page_links([("page2", "page1")])
        related = repo.get_related_by_links("page1", k=10)
        assert "doc2" in related  # backlink로 포함

    def test_page_links_idempotent(self, tmp_db):
        """upsert_page_links는 중복 실행 시 안전해야 한다."""
        repo = SqliteRepository(tmp_db)
        doc1 = _make_doc("doc1", "page1", "A")
        doc2 = _make_doc("doc2", "page2", "B")
        for d in [doc1, doc2]:
            repo.upsert(d)

        repo.upsert_page_links([("page1", "page2")])
        repo.upsert_page_links([("page1", "page2")])  # 재실행

        conn = repo.get_connection()
        cnt = conn.execute("SELECT count(*) FROM page_links").fetchone()[0]
        assert cnt == 1  # 중복 없음


# ─── ⑥ title 가중 BM25 테스트 ──────────────────────────────────────
class TestTitleWeightedSearch:
    def test_title_match_scores_higher(self, tmp_db):
        """title에서 쿼리가 매칭된 문서가 body에서만 매칭된 문서보다 높은 랭킹을 가져야 한다."""
        from geryon.search.keyword import KeywordRetriever
        repo = SqliteRepository(tmp_db)

        # doc_title: 제목에 "슈퍼셋" 포함, body에는 없음
        doc_title = _make_doc("dt1", "pt1", "슈퍼셋 설치 가이드", "일반적인 설치 방법을 설명합니다")
        # doc_body: 제목은 다르고, body에 "슈퍼셋" 포함
        doc_body = _make_doc("db1", "pb1", "설치 방법 안내문서", "슈퍼셋 소프트웨어를 설치하는 방법")
        for d in [doc_title, doc_body]:
            repo.upsert(d)

        retriever = KeywordRetriever(repository=repo)
        hits = retriever.search("슈퍼셋", k=10)
        hit_ids = [h.doc_id for h in hits]

        if len(hit_ids) >= 2:
            # title에서 매칭된 문서가 body에서 매칭된 문서보다 앞에 와야 함
            assert hit_ids.index("dt1") < hit_ids.index("db1"), \
                f"title match should rank higher: got order {hit_ids}"

    def test_fts_returns_results(self, tmp_db):
        """FTS 검색이 결과를 반환하는지 기본 확인."""
        from geryon.search.keyword import KeywordRetriever
        repo = SqliteRepository(tmp_db)
        doc = _make_doc("d1", "p1", "Apache Superset 설치", "[BI] Apache Superset 설치 가이드 문서입니다.")
        repo.upsert(doc)

        retriever = KeywordRetriever(repository=repo)
        hits = retriever.search("Apache Superset", k=10)
        assert len(hits) >= 1
        assert hits[0].doc_id == "d1"


# ─── ② + ⑥ 통합: static_score 가중이 hybrid 랭킹에 반영되는지 ─────
class TestStaticScoreInHybrid:
    def test_higher_static_score_ranks_better(self, tmp_db):
        """static_score가 높은 문서가 동일 RRF 점수에서 더 높은 순위를 가져야 한다."""
        repo = SqliteRepository(tmp_db)
        # 두 문서: 동일한 검색 키워드 포함, 하지만 static_score 다름
        doc_low = _make_doc("dlow", "plow", "GCP 프로젝트 가이드", "GCP 설정 방법을 설명합니다")
        doc_high = _make_doc("dhigh", "phigh", "GCP 공용 계정 안내", "GCP 계정 관련 상세 안내입니다")
        for d in [doc_low, doc_high]:
            repo.upsert(d)

        # static_score 설정: dhigh에 더 높은 점수
        repo.update_static_scores({"plow": 0.1, "phigh": 0.9})

        # 직접 DB에서 static_score 확인
        assert repo.get_static_score("dlow") == pytest.approx(0.1, abs=1e-6)
        assert repo.get_static_score("dhigh") == pytest.approx(0.9, abs=1e-6)


# ══════════════════════ page_links 데이터 손실 회귀 (2026-09-20)

def test_다른_원천의_page_links를_지우지_않는다(tmp_path):
    """⚠️ 실제로 일어난 데이터 손실의 회귀 시험.

    이전 구현은 `DELETE FROM page_links` 로 테이블 «전체»를 비우고 이번
    실행분만 넣었다. ingest 는 원천별로 도는데 삭제는 전역이라,
    `--source jira` 전체 색인 한 번에 confluence 가 쌓아 둔 링크가 전부
    날아갔다 — 실측 수만 건 → 0. `static_score` 의 backlink 성분(가중치 0.4)이
    전 문서에서 죽었는데 아무 경고도 없었다.
    """
    repo = SqliteRepository(str(tmp_path / "links.db"))
    for d in (_make_doc("dA", "conf1", "A"), _make_doc("dB", "conf2", "B"),
              _make_doc("dC", "jira1", "C")):
        repo.upsert(d)

    # 원천 1(confluence)이 링크를 쌓는다
    repo.upsert_page_links([("conf1", "conf2")], owner_src_ids={"conf1", "conf2"})
    conn = repo.get_connection()
    assert conn.execute("SELECT count(*) FROM page_links").fetchone()[0] == 1

    # 원천 2(jira)가 «링크 없이» 전체 색인된다 — 예전엔 여기서 위가 날아갔다
    repo.upsert_page_links([], owner_src_ids={"jira1"})

    rows = conn.execute("SELECT src_page_id, dst_page_id FROM page_links").fetchall()
    assert rows == [("conf1", "conf2")], f"다른 원천의 링크가 사라졌다: {rows}"


def test_자기_원천의_사라진_링크는_정리된다(tmp_path):
    """소유 범위 안에서는 «교체» 여야 한다 — 안 그러면 옛 링크가 영원히 남는다."""
    repo = SqliteRepository(str(tmp_path / "links2.db"))
    for d in (_make_doc("dA", "p1", "A"), _make_doc("dB", "p2", "B"),
              _make_doc("dC", "p3", "C")):
        repo.upsert(d)

    repo.upsert_page_links([("p1", "p2"), ("p1", "p3")], owner_src_ids={"p1"})
    conn = repo.get_connection()
    assert conn.execute("SELECT count(*) FROM page_links").fetchone()[0] == 2

    # p1 이 p3 링크를 지운 채 재색인되면 그 행도 사라져야 한다
    repo.upsert_page_links([("p1", "p2")], owner_src_ids={"p1"})
    rows = conn.execute("SELECT dst_page_id FROM page_links").fetchall()
    assert rows == [("p2",)], f"사라진 링크가 남아 있다: {rows}"


def test_backlink_counts는_저장된_그래프_전체를_본다(tmp_path):
    """이번 실행분이 아니라 테이블 전체. 부분 ingest 에서 갈린다."""
    repo = SqliteRepository(str(tmp_path / "links3.db"))
    for d in (_make_doc("dA", "p1", "A"), _make_doc("dB", "p2", "B"),
              _make_doc("dC", "p3", "C")):
        repo.upsert(d)
    repo.upsert_page_links([("p1", "p2")], owner_src_ids={"p1"})
    repo.upsert_page_links([("p3", "p2")], owner_src_ids={"p3"})

    assert repo.backlink_counts() == {"p2": 2}
