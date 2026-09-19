from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Knowledge base name cannot be empty.")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Knowledge base name cannot be empty.")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class KnowledgeBaseRead(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class KnowledgeBasePage(BaseModel):
    items: list[KnowledgeBaseRead]
    total: int
    page: int
    page_size: int


class TextDocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list, max_length=12)
    # None follows the backend model configuration; False keeps ingest purely
    # deterministic, which is what the MCP ingest tools request.
    synthesize_topic: bool | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Document title cannot be empty.")
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Document content cannot be empty.")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        clean_tags = []
        for tag in value:
            clean_tag = tag.strip().lstrip("#")
            if clean_tag and clean_tag not in clean_tags:
                clean_tags.append(clean_tag[:60])
        return clean_tags


class SourceSnapshotCreate(BaseModel):
    """A locally retained snapshot of an externally published source."""

    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    source_url: str = Field(min_length=1, max_length=2_048)
    platform: str = Field(min_length=1, max_length=60)
    author: str | None = Field(default=None, max_length=255)
    account: str | None = Field(default=None, max_length=255)
    published_at: datetime | None = None
    captured_at: datetime | None = None
    source_policy: Literal["full_text", "excerpt", "link_only"] = "full_text"
    disclosures: list[str] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=12)
    synthesize_topic: bool | None = None

    @field_validator("title", "content")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Source snapshot text cannot be empty.")
        return value

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_url must be an absolute http(s) URL.")
        return value

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        value = value.strip().casefold()
        if not value:
            raise ValueError("platform cannot be empty.")
        return value

    @field_validator("author", "account")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("disclosures", "tags")
    @classmethod
    def clean_string_list(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            normalized = item.strip().lstrip("#")
            if normalized and normalized not in cleaned:
                cleaned.append(normalized[:240])
        return cleaned


class DocumentChunkRead(BaseModel):
    id: int
    document_id: int
    knowledge_base_id: int
    chunk_index: int
    content: str
    vector_id: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentUpdate(BaseModel):
    """Replacement content for an existing document.

    The document id, its chunk rows and its Markdown paths are preserved: this is not a
    delete-and-re-ingest. Omitted source fields keep their current value, so a client can
    correct the text without restating provenance it did not capture again.
    """

    content: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=12)
    source_url: str | None = Field(default=None, max_length=2_048)
    source_platform: str | None = Field(default=None, max_length=60)
    source_author: str | None = Field(default=None, max_length=255)
    source_account: str | None = Field(default=None, max_length=255)
    source_published_at: datetime | None = None
    source_captured_at: datetime | None = None
    source_policy: Literal["full_text", "excerpt", "link_only"] | None = None
    source_disclosures: list[str] | None = Field(default=None, max_length=20)
    synthesize_topic: bool | None = None

    @field_validator("title")
    @classmethod
    def validate_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Document title cannot be empty.")
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Document content cannot be empty.")
        return value


class DocumentRead(BaseModel):
    id: int
    knowledge_base_id: int
    title: str
    source_type: str
    file_name: str | None
    content: str
    source_url: str | None
    source_platform: str | None
    source_author: str | None
    source_account: str | None
    source_published_at: datetime | None
    source_captured_at: datetime | None
    source_policy: str
    source_disclosures: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    content_hash: str
    # Workspace paths, so a client can tell that an update rewrote the same pages.
    raw_path: str | None = None
    source_path: str | None = None
    topic_path: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentDetail(DocumentRead):
    chunks: list[DocumentChunkRead]


class DocumentPage(BaseModel):
    items: list[DocumentRead]
    total: int
    page: int
    page_size: int


class SearchRequest(BaseModel):
    knowledge_base_id: int = Field(gt=0)
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    llm: "LLMConfigurationInput | None" = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Search query cannot be empty.")
        return value


class SearchResult(BaseModel):
    document_id: int
    title: str
    chunk: str
    score: float
    chunk_id: int | None = None
    chunk_index: int | None = None
    source_url: str | None = None
    source_platform: str | None = None
    source_author: str | None = None
    source_account: str | None = None
    source_published_at: datetime | None = None
    source_captured_at: datetime | None = None
    source_policy: str | None = None
    content_hash: str | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    answer: str | None = None
    answer_mode: str = "retrieval"
    model: str | None = None
    model_error: str | None = None


class WikiPageRead(BaseModel):
    path: str
    title: str
    page_type: str
    summary: str
    content: str
    updated_at: datetime
    inbound_links: int = 0
    outbound_links: int = 0


class WikiPagePage(BaseModel):
    items: list[WikiPageRead]
    total: int
    page: int
    page_size: int


class WikiPageUpsert(BaseModel):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1)


class WikiQueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=8, ge=1, le=30)
    save_as: str | None = Field(default=None, max_length=160)
    # Named retrieval strategy; omitted means the default one.
    strategy: str | None = Field(default=None, max_length=40)
    llm: "LLMConfigurationInput | None" = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Wiki query cannot be empty.")
        return value

    @field_validator("save_as")
    @classmethod
    def validate_save_as(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class WikiQueryResult(BaseModel):
    path: str
    title: str
    page_type: str
    summary: str
    snippet: str
    score: float
    citations: list[str] = Field(default_factory=list)
    # True when the page was pulled in by following a wiki link from a direct
    # match rather than matching the query itself.
    related: bool = False


class WikiQueryResponse(BaseModel):
    query: str
    answer: str
    results: list[WikiQueryResult]
    saved_path: str | None = None
    answer_mode: str = "deterministic"
    # Which retrieval strategy ran, how many link hops it used, and whether page
    # vectors contributed. Reported because feedback and replays need to know what
    # actually happened, not what was requested.
    strategy: str = "auto"
    hops: int = 1
    vectors: bool = False
    planned: bool = False
    model: str | None = None
    model_error: str | None = None


class LLMConfigurationInput(BaseModel):
    """Optional per-request OpenAI-compatible settings.

    API keys are accepted only to make the request and are never included in a
    response model or written to the Markdown workspace.
    """

    enabled: bool | None = None
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=2_000)
    model: str | None = Field(default=None, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=64, le=16_384)
    timeout_seconds: float | None = Field(default=None, ge=5, le=300)

    @field_validator("base_url", "api_key", "model")
    @classmethod
    def strip_optional_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class LLMConfigStatusResponse(BaseModel):
    provider: str = "openai-compatible"
    enabled: bool
    ready: bool
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    api_key_configured: bool


class LLMTestResponse(BaseModel):
    ready: bool
    model: str
    message: str


class WikiIssue(BaseModel):
    severity: str
    code: str
    path: str
    message: str


class WikiLintResponse(BaseModel):
    healthy: bool
    checked_pages: int
    issues: list[WikiIssue]


class WikiGraphNode(BaseModel):
    id: str
    label: str
    path: str
    page_type: str
    inbound_links: int
    outbound_links: int


class WikiGraphEdge(BaseModel):
    source: str
    target: str


class WikiGraphResponse(BaseModel):
    nodes: list[WikiGraphNode]
    edges: list[WikiGraphEdge]


class WikiStatusResponse(BaseModel):
    workspace_path: str
    initialized: bool
    source_count: int
    page_count: int
    topic_count: int
    orphan_count: int
    broken_link_count: int
    recent_activity: list[str]


class QueryFeedbackCreate(BaseModel):
    """A rating of one answer. Only a thumbs up or a thumbs down is accepted."""

    mode: Literal["wiki", "rag"] = "wiki"
    query: str = Field(min_length=1, max_length=2_000)
    rating: Literal[-1, 1]
    note: str | None = Field(default=None, max_length=2_000)
    answer: str | None = Field(default=None, max_length=8_000)
    answer_mode: str | None = Field(default=None, max_length=20)
    model: str | None = Field(default=None, max_length=200)
    strategy_id: str | None = Field(default=None, max_length=40)
    source_paths: list[str] = Field(default_factory=list, max_length=50)
    # Only meaningful with a thumbs down; rejected with a thumbs up rather than dropped.
    bad_paths: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Query cannot be empty.")
        return value

    @field_validator("bad_paths")
    @classmethod
    def validate_bad_paths(cls, value: list[str], info) -> list[str]:
        paths = [item.strip() for item in value if item.strip()]
        if paths and info.data.get("rating") == 1:
            raise ValueError("A thumbs up cannot mark a citation as wrong.")
        return list(dict.fromkeys(paths))

    @field_validator("note")
    @classmethod
    def validate_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class QueryFeedbackRead(BaseModel):
    id: int
    knowledge_base_id: int
    mode: str
    query: str
    rating: int
    note: str | None
    answer: str | None
    answer_mode: str | None
    model: str | None
    strategy_id: str | None
    source_paths: list[str]
    bad_paths: list[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class QueryFeedbackPage(BaseModel):
    items: list[QueryFeedbackRead]
    total: int
    page: int
    page_size: int
    positive: int
    negative: int
