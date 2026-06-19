from enum import Enum
from datetime import datetime
from pydantic import BaseModel, Field

from typing import Any

class SourceType(str, Enum):
    CONFLUENCE = "confluence"
    WEB = "web"
    JIRA = "jira"
    GITLAB = "gitlab"
    GITHUB = "github"
    BITBUCKET = "bitbucket"

class Attachment(BaseModel):
    filename: str
    media_type: str | None = None
    local_path: str | None = None
    url: str | None = None

class Document(BaseModel):
    doc_id: str
    source: SourceType
    source_id: str
    space_or_repo: str | None = None
    url: str | None = None
    title: str
    body_markdown: str
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    category: list[str] = Field(default_factory=list) # 자유 문자열 다중 분류
    doc_type: str | None = None # 소스 무관 문서 유형: issue·spec·guide·meeting…
    hierarchy: list[str] = Field(default_factory=list)
    author: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    raw_meta: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
    ingested_at: datetime

class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    ordinal: int
    text: str

class RawRecord(BaseModel):
    source: SourceType
    source_id: str
    raw_body: str | None = None
    raw_format: str
    title: str | None = None
    url: str | None = None
    space_or_repo: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    attachments: list[Attachment] = Field(default_factory=list)

class SearchHit(BaseModel):
    doc_id: str
    title: str
    url: str | None
    source: SourceType
    space_or_repo: str | None
    snippet: str
    score: float
    passage: str = ""  # 쿼리 매칭 본문 구절(rerank용 best-passage). UI snippet과 별개.
    author: str = ""   # 작성자(표시 시 노이즈 정규화 적용)

class SearchFilter(BaseModel):
    sources: list[SourceType] | None = None
    spaces_or_repos: list[str] | None = None
    tags: list[str] | None = None
    categories: list[str] | None = None # 자유 문자열 다중 분류 필터
    authors: list[str] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None

class DictionaryEntry(BaseModel):
    term: str
    synonyms: list[str] = Field(default_factory=list)
    domain: str | None = None
    scope: str = "global"
    boost: float = 1.0

class UserProfile(BaseModel):
    user_id: str
    role: str | None = None
    interests: list[str] = Field(default_factory=list)
    preferred_spaces: list[str] = Field(default_factory=list)
    preferred_tags: list[str] = Field(default_factory=list)
    language: str | None = None
    weights: dict[str, float] = Field(default_factory=dict)

class SyncState(BaseModel):
    source: SourceType
    last_synced_at: datetime | None = None
    cursor: str | None = None
    last_seen_doc_ids: list[str] = Field(default_factory=list)
    doc_count: int = 0
