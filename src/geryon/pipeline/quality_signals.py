"""Bronze 원본 정제로 자체 계산하는 품질 신호 모듈.

외부 메타데이터 DB(별도 confluence_metadata.db 등) 의존 없이,
confluence_db/{space}/{pageId}/content.html 만을 이용해
page_links 및 static_score를 계산한다.

계산 항목:
  - page_links: content.html Storage-Format의 <ac:link>/<ri:page ri:content-title="..."/>
    에서 outgoing 링크 추출 → 같은 space 내 title→source_id 매핑으로 dst 결정.
  - static_score: recency(updated_at) × 0.3 + richness(본문 길이·heading 수) × 0.3
    + backlink_centrality(역집계) × 0.4. 모두 [0,1] 정규화.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from geryon.domain.models import Document

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# 1. page_links: Bronze HTML에서 outgoing 링크 추출
# ─────────────────────────────────────────────────────────────────

def extract_outgoing_titles(html: str) -> list[str]:
    """content.html(Confluence storage-format)에서 outgoing 링크의 page title 목록 반환.

    - <ac:link> 안의 <ri:page ri:content-title="..."/> 만 처리
    - ri:content-entity-id(현재 실데이터에 없음)는 무시
    - 결과는 중복 포함 (caller에서 처리)
    """
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
        titles: list[str] = []
        for link in soup.find_all("ac:link"):
            ri_page = link.find("ri:page")
            if ri_page is None:
                continue
            title = ri_page.get("ri:content-title")  # type: ignore[arg-type]
            if isinstance(title, list):
                title = title[0] if title else None
            if title and isinstance(title, str):
                titles.append(title.strip())
        return titles
    except Exception as exc:
        logger.debug("extract_outgoing_titles 파싱 오류: %s", exc)
        return []



def build_page_links_from_docs(
    docs_info: list[tuple[str, str, str, str]],  # (source_id, space, title, raw_html)
) -> list[tuple[str, str]]:
    """문서 정보에서 page_links(src_page_id, dst_page_id) 를 자체 계산·반환.

    Args:
        docs_info: (source_id, space, title, raw_html) 튜플 목록.
                   title은 Silver에 저장된 doc.title (best-effort 포함).
                   raw_html은 content.html 원본.

    Returns:
        [(src_page_id, dst_page_id), ...] 중복 없는 링크 목록.
    """
    # 1. title → source_id 매핑 구성
    #    - space별 우선, 없으면 전체 fallback
    space_title_map: dict[str, dict[str, str]] = {}
    global_title_map: dict[str, str] = {}

    for source_id, space, title, _ in docs_info:
        if not title or title == source_id:
            # page_id가 그대로 title인 legacy — title 매핑에서 제외
            # (숫자만인 title은 ri:content-title과 매칭 불가)
            continue
        if space not in space_title_map:
            space_title_map[space] = {}
        # 중복 시 첫 번째 유지(best-effort)
        if title not in space_title_map[space]:
            space_title_map[space][title] = source_id
        if title not in global_title_map:
            global_title_map[title] = source_id

    # 2. 각 page 링크 추출
    links: set[tuple[str, str]] = set()

    for source_id, space, _, raw_html in docs_info:
        out_titles = extract_outgoing_titles(raw_html)
        for title in out_titles:
            # 같은 space에서 먼저 탐색
            dst = space_title_map.get(space, {}).get(title)
            if dst is None:
                # 전체 fallback
                dst = global_title_map.get(title)
            if dst and dst != source_id:
                links.add((source_id, dst))

    logger.info("page_links 자체 계산 완료: %d건", len(links))
    return list(links)


# ─────────────────────────────────────────────────────────────────
# 2. static_score: Bronze 정제로 자체 계산
# ─────────────────────────────────────────────────────────────────

_RICHNESS_MAX_CHARS = 50_000   # 이 이상은 포화 처리
_RICHNESS_MAX_HEADINGS = 50    # heading 수 포화값

def _recency_score(updated_at: datetime | None, created_at: datetime | None) -> float:
    """updated_at(또는 created_at) 기반 신선도 [0,1].

    최근 30일: 1.0 → 2년: ~0.0 (반감기 180일, 지수 감쇠).
    날짜 정보 없으면 0.3 (중립값).
    """
    dt = updated_at or created_at
    if dt is None:
        return 0.3
    # naive → UTC 취급
    if dt.tzinfo is None:
        now = datetime.now()
    else:
        now = datetime.now(timezone.utc)
    age_days = max(0.0, (now - dt).total_seconds() / 86400)
    half_life = 180.0  # 일
    return math.exp(-age_days * math.log(2) / half_life)


def _richness_score(body_markdown: str) -> float:
    """본문 풍부도 [0,1].

    log-normalized: 짧은 문서 낮음, 긴 문서 높음.
    heading 수(# 로 시작하는 줄)도 반영.
    """
    length = len(body_markdown)
    heading_count = sum(1 for line in body_markdown.splitlines() if line.startswith("#"))

    # log 정규화
    length_score = math.log1p(min(length, _RICHNESS_MAX_CHARS)) / math.log1p(_RICHNESS_MAX_CHARS)
    heading_score = math.log1p(min(heading_count, _RICHNESS_MAX_HEADINGS)) / math.log1p(_RICHNESS_MAX_HEADINGS)

    return 0.7 * length_score + 0.3 * heading_score


def compute_static_scores(
    docs: list["Document"],
    backlink_counts: dict[str, int],  # source_id → 받은 backlink 수
) -> dict[str, float]:
    """문서 목록의 static_score(source_id → [0,1])를 자체 계산·반환.

    공식:
      static_score = recency × 0.3 + richness × 0.3 + backlink_centrality × 0.4

    backlink_centrality:
      log(1 + backlink_count) / log(1 + max_backlinks) — 포화 log-normalization.
      모든 문서의 backlink=0이면 0.0.
    """
    if not docs:
        return {}

    # backlink 최댓값 결정
    max_bl = max(backlink_counts.values(), default=0)
    if max_bl <= 0:
        max_bl = 1  # 0-division 방지, 결과는 0.0

    scores: dict[str, float] = {}
    for doc in docs:
        sid = doc.source_id
        bl_count = backlink_counts.get(sid, 0)

        recency = _recency_score(doc.updated_at, doc.created_at)
        richness = _richness_score(doc.body_markdown)
        bl_centrality = math.log1p(bl_count) / math.log1p(max_bl)

        raw = recency * 0.3 + richness * 0.3 + bl_centrality * 0.4
        # clamp [0,1]
        scores[sid] = max(0.0, min(1.0, raw))

    return scores


def compute_backlink_counts(links: list[tuple[str, str]]) -> dict[str, int]:
    """page_links에서 각 page_id의 backlink(수신 링크) 수를 역집계."""
    counts: dict[str, int] = {}
    for _, dst in links:
        counts[dst] = counts.get(dst, 0) + 1
    return counts
