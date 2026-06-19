"""Bronze 원본 정제 품질 신호 자체 계산 단위 테스트.

외부 DB 없이 content.html Bronze 원본만으로 page_links·static_score를
자체 계산하는 pipeline/quality_signals.py 검증.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from geryon.pipeline.quality_signals import (
    extract_outgoing_titles,
    build_page_links_from_docs,
    compute_backlink_counts,
    compute_static_scores,
    _recency_score,
    _richness_score,
)
from geryon.domain.models import Document, SourceType


# ─── 픽스처 ───────────────────────────────────────────────────────────

def _make_doc(
    source_id: str,
    title: str,
    body: str = "테스트 본문 내용",
    space: str = "SPACE1",
    updated_at: datetime | None = None,
) -> Document:
    import hashlib
    doc_id = hashlib.sha1(f"confluence:{source_id}".encode()).hexdigest()
    return Document(
        doc_id=doc_id,
        source=SourceType.CONFLUENCE,
        source_id=source_id,
        space_or_repo=space,
        title=title,
        body_markdown=body,
        content_hash=f"hash_{source_id}",
        ingested_at=datetime.now(timezone.utc).replace(tzinfo=None),
        updated_at=updated_at,
        tags=[],
        hierarchy=[],
        raw_meta={},
    )


_SAMPLE_HTML_WITH_LINK = """
<h1>허브 문서</h1>
<p>이 문서는 다음 문서를 참조합니다:</p>
<ac:link ac:card-appearance="inline">
  <ri:page ri:content-title="연결된 문서 A" ri:version-at-save="4"></ri:page>
  <ac:link-body>연결된 문서 A</ac:link-body>
</ac:link>
<ac:link>
  <ri:page ri:content-title="연결된 문서 B"></ri:page>
</ac:link>
"""

_SAMPLE_HTML_NO_LINK = """
<h1>고립 문서</h1>
<p>이 문서는 다른 문서를 참조하지 않습니다.</p>
"""


# ─── extract_outgoing_titles 테스트 ──────────────────────────────────

class TestExtractOutgoingTitles:
    def test_extracts_ri_content_title(self):
        """ri:content-title 속성에서 링크 제목을 추출한다."""
        titles = extract_outgoing_titles(_SAMPLE_HTML_WITH_LINK)
        assert "연결된 문서 A" in titles
        assert "연결된 문서 B" in titles

    def test_empty_html_returns_empty(self):
        """빈 HTML은 빈 목록을 반환한다."""
        assert extract_outgoing_titles("") == []

    def test_no_links_returns_empty(self):
        """링크 없는 HTML은 빈 목록을 반환한다."""
        titles = extract_outgoing_titles(_SAMPLE_HTML_NO_LINK)
        assert titles == []

    def test_handles_invalid_html_gracefully(self):
        """잘못된 HTML도 예외 없이 처리한다."""
        result = extract_outgoing_titles("<broken><ac:link><ri:page ri:content-title='테스트'>")
        # 에러 없이 반환, 결과가 있거나 없거나
        assert isinstance(result, list)


# ─── build_page_links_from_docs 테스트 ───────────────────────────────

class TestBuildPageLinksFromDocs:
    def test_links_within_same_space(self):
        """같은 space 내 title 매핑으로 page_links를 생성한다."""
        docs_info = [
            ("page1", "SPACE1", "허브 문서", _SAMPLE_HTML_WITH_LINK),
            ("page2", "SPACE1", "연결된 문서 A", _SAMPLE_HTML_NO_LINK),
            ("page3", "SPACE1", "연결된 문서 B", _SAMPLE_HTML_NO_LINK),
        ]
        links = build_page_links_from_docs(docs_info)
        link_set = set(links)
        assert ("page1", "page2") in link_set
        assert ("page1", "page3") in link_set

    def test_self_link_excluded(self):
        """자기 자신을 가리키는 링크는 제외된다."""
        html = """
        <ac:link><ri:page ri:content-title="허브 문서"></ri:page></ac:link>
        """
        docs_info = [
            ("page1", "SPACE1", "허브 문서", html),
        ]
        links = build_page_links_from_docs(docs_info)
        assert ("page1", "page1") not in links

    def test_unknown_title_skipped(self):
        """알 수 없는 제목을 참조하는 링크는 무시된다."""
        html = """
        <ac:link><ri:page ri:content-title="존재하지 않는 문서"></ri:page></ac:link>
        """
        docs_info = [
            ("page1", "SPACE1", "허브 문서", html),
        ]
        links = build_page_links_from_docs(docs_info)
        assert len(links) == 0

    def test_cross_space_fallback(self):
        """다른 space 문서도 전역 title 매핑 fallback으로 연결된다."""
        html = """
        <ac:link><ri:page ri:content-title="타 공간 문서"></ri:page></ac:link>
        """
        docs_info = [
            ("page1", "SPACE1", "허브 문서", html),
            ("page2", "SPACE2", "타 공간 문서", _SAMPLE_HTML_NO_LINK),
        ]
        links = build_page_links_from_docs(docs_info)
        assert ("page1", "page2") in links

    def test_no_links_returns_empty(self):
        """링크 없는 문서들은 빈 목록을 반환한다."""
        docs_info = [
            ("page1", "SPACE1", "문서1", _SAMPLE_HTML_NO_LINK),
            ("page2", "SPACE1", "문서2", _SAMPLE_HTML_NO_LINK),
        ]
        links = build_page_links_from_docs(docs_info)
        assert links == []

    def test_page_id_title_excluded_from_mapping(self):
        """title이 page_id와 동일한 문서(legacy)는 title 매핑에서 제외된다."""
        html = """
        <ac:link><ri:page ri:content-title="4653547907"></ri:page></ac:link>
        """
        docs_info = [
            ("4653547907", "SPACE1", "4653547907", _SAMPLE_HTML_NO_LINK),  # title = source_id (legacy)
            ("page1", "SPACE1", "다른 문서", html),
        ]
        links = build_page_links_from_docs(docs_info)
        # 숫자 page_id title은 매핑 제외 → 링크 생성 안 됨
        assert ("page1", "4653547907") not in links


# ─── compute_backlink_counts 테스트 ──────────────────────────────────

class TestComputeBacklinkCounts:
    def test_backlink_count_correct(self):
        """각 목적지 page_id의 backlink 수를 올바르게 계산한다."""
        links = [
            ("page1", "page3"),
            ("page2", "page3"),
            ("page1", "page2"),
        ]
        counts = compute_backlink_counts(links)
        assert counts["page3"] == 2
        assert counts["page2"] == 1
        assert counts.get("page1", 0) == 0  # page1은 수신 링크 없음

    def test_empty_links_returns_empty(self):
        """빈 링크는 빈 딕셔너리를 반환한다."""
        assert compute_backlink_counts([]) == {}


# ─── compute_static_scores 테스트 ────────────────────────────────────

class TestComputeStaticScores:
    def test_scores_in_valid_range(self):
        """모든 static_score는 [0, 1] 범위에 있어야 한다."""
        docs = [
            _make_doc("p1", "문서1", "짧은 본문"),
            _make_doc("p2", "문서2", "# 제목\n" * 20 + "매우 긴 본문 " * 1000),
        ]
        backlink_counts = {"p1": 0, "p2": 5}
        scores = compute_static_scores(docs, backlink_counts)
        for sid, score in scores.items():
            assert 0.0 <= score <= 1.0, f"{sid}: score={score} 범위 밖"

    def test_higher_backlink_ranks_higher(self):
        """backlink가 많은 문서가 더 높은 static_score를 가져야 한다 (다른 조건 동일)."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        same_body = "# 제목\n동일한 본문 내용입니다. " * 50
        doc_low = _make_doc("plow", "문서1", same_body, updated_at=now)
        doc_high = _make_doc("phigh", "문서2", same_body, updated_at=now)
        backlink_counts = {"plow": 1, "phigh": 10}
        scores = compute_static_scores([doc_low, doc_high], backlink_counts)
        assert scores["phigh"] > scores["plow"]

    def test_richer_content_ranks_higher(self):
        """본문이 풍부한 문서가 더 높은 static_score를 가져야 한다 (backlink 동일)."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        doc_short = _make_doc("pshort", "짧은 문서", "짧아요", updated_at=now)
        doc_long = _make_doc("plong", "긴 문서", "# 제목\n" + "긴 본문 내용입니다. " * 500, updated_at=now)
        backlink_counts = {"pshort": 0, "plong": 0}
        scores = compute_static_scores([doc_short, doc_long], backlink_counts)
        assert scores["plong"] > scores["pshort"]

    def test_empty_docs_returns_empty(self):
        """빈 문서 목록은 빈 딕셔너리를 반환한다."""
        assert compute_static_scores([], {}) == {}

    def test_all_zero_backlinks(self):
        """모든 backlink가 0이어도 static_score가 올바르게 계산된다."""
        doc = _make_doc("p1", "문서1", "일반 본문 내용입니다.")
        scores = compute_static_scores([doc], {})
        assert "p1" in scores
        assert 0.0 <= scores["p1"] <= 1.0

    def test_no_date_doc_gets_neutral_recency(self):
        """날짜 정보 없는 문서는 중립 recency(0.3)를 받는다."""
        doc = _make_doc("p1", "문서1", "본문")
        # updated_at/created_at 모두 None
        score = _recency_score(None, None)
        assert score == pytest.approx(0.3)


# ─── _recency_score 테스트 ───────────────────────────────────────────

class TestRecencyScore:
    def test_recent_doc_scores_high(self):
        """최근 문서는 높은 recency를 받는다."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        score = _recency_score(now, None)
        assert score > 0.9

    def test_old_doc_scores_low(self):
        """오래된 문서는 낮은 recency를 받는다."""
        from datetime import timedelta
        old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=730)
        score = _recency_score(old, None)
        assert score < 0.1


# ─── _richness_score 테스트 ─────────────────────────────────────────

class TestRichnessScore:
    def test_empty_body_scores_near_zero(self):
        """빈 본문은 richness 0에 가까운 값을 받는다."""
        score = _richness_score("")
        assert score < 0.05

    def test_rich_body_scores_high(self):
        """긴 본문·많은 heading은 높은 richness를 받는다."""
        body = "# 제목\n" * 30 + "긴 본문 내용입니다. " * 1000
        score = _richness_score(body)
        assert score > 0.7

    def test_richness_monotonic(self):
        """본문이 길수록 richness가 단조 증가해야 한다."""
        scores = [
            _richness_score("x" * n)
            for n in [0, 100, 1000, 10000, 50000]
        ]
        for i in range(len(scores) - 1):
            assert scores[i] <= scores[i + 1], f"단조 증가 위반: {scores}"
