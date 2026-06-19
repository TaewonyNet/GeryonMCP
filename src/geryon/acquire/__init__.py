"""Bronze 수집 — 외부 API 직접 호출(코어 적재와 분리). connector(ingest)와 대칭 플러그인."""
from geryon.acquire.base import Acquirer
from geryon.acquire.confluence import ConfluenceAcquirer
from geryon.acquire.git import GitAcquirer
from geryon.acquire.jira import JiraAcquirer

ACQUIRER_REGISTRY: dict[str, type[Acquirer]] = {
    "confluence": ConfluenceAcquirer,
    "git": GitAcquirer,
    "jira": JiraAcquirer,
}

__all__ = ["Acquirer", "ConfluenceAcquirer", "GitAcquirer", "JiraAcquirer", "ACQUIRER_REGISTRY"]
