from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pathlib import Path as _Path

from app.config import get_settings
from app.db.models import Document, DocumentChunk, KnowledgeBase
from app.db.schemas import DocumentUpdate, SourceSnapshotCreate, TextDocumentCreate
from app.services import embedding_service
from app.services.chunk_service import split_text
from app.services import wiki_service
from app.vector import chroma_client


class DocumentIndexingError(RuntimeError):
    pass


class DuplicateDocumentError(RuntimeError):
    """The same content from the same source is already in this knowledge base.

    Not an index failure: the caller asked for something that already exists, and the
    answer is to update that document rather than to create a second copy of it.
    """

    def __init__(self, document: Document) -> None:
        super().__init__(
            f"Document {document.id} ({document.title}) already holds this content from the same source; "
            f"update it with PUT /api/knowledge-bases/{document.knowledge_base_id}/documents/{document.id} "
            "instead of ingesting it again."
        )
        self.document = document


def create_text_document(db: Session, knowledge_base: KnowledgeBase, payload: TextDocumentCreate) -> Document:
    return _create_document(
        db=db,
        knowledge_base=knowledge_base,
        title=payload.title,
        content=payload.content,
        source_type="text",
        file_name=None,
        tags=payload.tags,
        synthesize_topic=payload.synthesize_topic,
    )


def create_file_document(
    db: Session,
    knowledge_base: KnowledgeBase,
    title: str,
    content: str,
    file_name: str,
    tags: list[str] | None = None,
    synthesize_topic: bool | None = None,
) -> Document:
    return _create_document(
        db=db,
        knowledge_base=knowledge_base,
        title=title,
        content=content,
        source_type="file",
        file_name=file_name,
        tags=tags or [],
        synthesize_topic=synthesize_topic,
    )


def create_source_snapshot(
    db: Session,
    knowledge_base: KnowledgeBase,
    payload: SourceSnapshotCreate,
) -> Document:
    return _create_document(
        db=db,
        knowledge_base=knowledge_base,
        title=payload.title,
        content=payload.content,
        source_type="source_snapshot",
        file_name=None,
        tags=payload.tags,
        source_snapshot=payload,
        synthesize_topic=payload.synthesize_topic,
    )


def list_documents(db: Session, knowledge_base_id: int, page: int, page_size: int) -> tuple[list[Document], int]:
    offset = (page - 1) * page_size
    total = db.scalar(
        select(func.count()).select_from(Document).where(Document.knowledge_base_id == knowledge_base_id)
    ) or 0
    items = db.scalars(
        select(Document)
        .where(Document.knowledge_base_id == knowledge_base_id)
        .order_by(Document.created_at.desc(), Document.id.desc())
        .offset(offset)
        .limit(page_size)
    ).all()
    return list(items), total


def get_document(db: Session, document_id: int) -> Document | None:
    return db.get(Document, document_id)


def delete_document(db: Session, document: Document) -> None:
    if get_settings().vector_index_enabled:
        try:
            chroma_client.delete_by_document_id(document.id)
        except Exception as exc:
            raise DocumentIndexingError("Failed to delete document vectors.") from exc

    db.delete(document)
    db.commit()
    wiki_service.delete_source(document.knowledge_base_id, document.id)


def _create_document(
    db: Session,
    knowledge_base: KnowledgeBase,
    title: str,
    content: str,
    source_type: str,
    file_name: str | None,
    tags: list[str],
    source_snapshot: SourceSnapshotCreate | None = None,
    synthesize_topic: bool | None = None,
) -> Document:
    indexable = source_snapshot is None or source_snapshot.source_policy != "link_only"
    chunks = split_text(content) if indexable else []
    if indexable and not chunks:
        raise ValueError("Document content cannot be empty.")

    duplicate = _find_duplicate(db, knowledge_base.id, content_hash(content), source_snapshot, file_name, title)
    if duplicate is not None:
        raise DuplicateDocumentError(duplicate)

    document = Document(
        knowledge_base_id=knowledge_base.id,
        title=title,
        source_type=source_type,
        file_name=file_name,
        content=content,
        source_url=source_snapshot.source_url if source_snapshot else None,
        source_platform=source_snapshot.platform if source_snapshot else None,
        source_author=source_snapshot.author if source_snapshot else None,
        source_account=source_snapshot.account if source_snapshot else None,
        source_published_at=_as_utc(source_snapshot.published_at) if source_snapshot and source_snapshot.published_at else None,
        source_captured_at=_as_utc(source_snapshot.captured_at) if source_snapshot and source_snapshot.captured_at else (_utc_now() if source_snapshot else None),
        source_policy=source_snapshot.source_policy if source_snapshot else "full_text",
        source_disclosures=source_snapshot.disclosures if source_snapshot else [],
        tags=tags,
        content_hash=content_hash(content),
    )
    db.add(document)
    db.flush()

    chunk_models: list[DocumentChunk] = []
    for index, chunk in enumerate(chunks):
        chunk_model = DocumentChunk(
            document_id=document.id,
            knowledge_base_id=knowledge_base.id,
            chunk_index=index,
            content=chunk,
        )
        db.add(chunk_model)
        chunk_models.append(chunk_model)

    db.flush()

    try:
        paths = wiki_service.ingest_source(
            knowledge_base_id=knowledge_base.id,
            knowledge_base_name=knowledge_base.name,
            document_id=document.id,
            title=title,
            content=content,
            source_type=source_type,
            file_name=file_name,
            tags=tags,
            source_url=document.source_url,
            source_platform=document.source_platform,
            source_author=document.source_author,
            source_account=document.source_account,
            source_published_at=document.source_published_at,
            source_captured_at=document.source_captured_at,
            source_policy=document.source_policy,
            source_disclosures=document.source_disclosures,
            content_hash=document.content_hash,
        )
    except Exception as exc:
        db.rollback()
        wiki_service.delete_source(knowledge_base.id, document.id)
        raise DocumentIndexingError("Failed to maintain wiki workspace.") from exc

    document.raw_path = paths["raw_path"]
    document.source_path = paths["source_path"]
    document.topic_path = paths["topic_path"]

    if get_settings().vector_index_enabled and chunk_models:
        try:
            _index_document_chunks(knowledge_base, document, chunk_models)
        except Exception as exc:
            db.rollback()
            wiki_service.delete_source(knowledge_base.id, document.id)
            raise DocumentIndexingError("Failed to index document chunks.") from exc

    db.commit()
    db.refresh(document)
    # Model-backed topic maintenance runs after the transaction is closed: the source
    # page and its source list are already durable, so a slow or failing model can no
    # longer hold a write lock or roll back a successful ingest.
    wiki_service.complete_ingest(
        knowledge_base_id=knowledge_base.id,
        document_id=document.id,
        title=title,
        content=content,
        synthesize_topic=synthesize_topic,
    )
    return document


def _find_duplicate(
    db: Session,
    knowledge_base_id: int,
    digest: str,
    source_snapshot: SourceSnapshotCreate | None,
    file_name: str | None,
    title: str,
) -> Document | None:
    """Same bytes from the same source is a re-capture, not a second source.

    The source identity is part of the match on purpose: identical text published in two
    places is two sources, and collapsing them would throw away provenance.
    """
    query = db.query(Document).filter(
        Document.knowledge_base_id == knowledge_base_id,
        Document.content_hash == digest,
    )
    if source_snapshot is not None:
        query = query.filter(Document.source_url == source_snapshot.source_url)
    elif file_name is not None:
        query = query.filter(Document.file_name == file_name)
    else:
        query = query.filter(Document.title == title)
    return query.first()


def update_document(
    db: Session,
    knowledge_base: KnowledgeBase,
    document: Document,
    payload: DocumentUpdate,
) -> Document:
    """Replace a document's content in place, keeping its id and its workspace paths."""
    title = payload.title or document.title
    content = payload.content
    source_policy = payload.source_policy or document.source_policy
    indexable = source_policy != "link_only"
    chunks = split_text(content) if indexable else []
    if indexable and not chunks:
        raise ValueError("Document content cannot be empty.")

    old_chunks = list(document.chunks)
    old_vector_ids = [chunk.vector_id for chunk in old_chunks if chunk.vector_id]
    # Only the values this update actually replaces are overwritten: an omitted field
    # means "unchanged", so a text correction cannot silently drop provenance.
    previous = {
        "title": document.title,
        "content": document.content,
        "content_hash": document.content_hash,
        "source_policy": document.source_policy,
    }
    document.title = title
    document.content = content
    document.content_hash = content_hash(content)
    document.source_policy = source_policy
    document.tags = payload.tags if payload.tags is not None else document.tags
    document.source_url = payload.source_url if payload.source_url is not None else document.source_url
    document.source_platform = payload.source_platform if payload.source_platform is not None else document.source_platform
    document.source_author = payload.source_author if payload.source_author is not None else document.source_author
    document.source_account = payload.source_account if payload.source_account is not None else document.source_account
    document.source_published_at = (
        _as_utc(payload.source_published_at) if payload.source_published_at is not None else document.source_published_at
    )
    document.source_captured_at = (
        _as_utc(payload.source_captured_at) if payload.source_captured_at is not None else document.source_captured_at
    )
    document.source_disclosures = (
        payload.source_disclosures if payload.source_disclosures is not None else document.source_disclosures
    )

    for chunk in old_chunks:
        db.delete(chunk)
    db.flush()

    chunk_models: list[DocumentChunk] = []
    for index, chunk in enumerate(chunks):
        chunk_model = DocumentChunk(
            document_id=document.id,
            knowledge_base_id=knowledge_base.id,
            chunk_index=index,
            content=chunk,
        )
        db.add(chunk_model)
        chunk_models.append(chunk_model)
    db.flush()

    # New vectors are written before the stale ones are dropped, so a failure while
    # embedding leaves the previously indexed document untouched. Re-indexing is skipped
    # when the content is unchanged, which keeps a metadata-only update cheap and avoids
    # rewriting vectors that are still exactly right.
    if get_settings().vector_index_enabled:
        reindex = bool(chunk_models) and (
            document.content_hash != previous["content_hash"] or not old_vector_ids
        )
        if reindex:
            try:
                _index_document_chunks(knowledge_base, document, chunk_models)
            except Exception as exc:
                db.rollback()
                raise DocumentIndexingError("Failed to index document chunks.") from exc
        elif chunk_models and old_vector_ids:
            # The content is unchanged, so the vectors in the index are still exactly the
            # ones these new rows describe: record their ids instead of re-embedding.
            for chunk in chunk_models:
                chunk.vector_id = _vector_id(knowledge_base.id, document, chunk.chunk_index)
        # A document that is no longer indexed (policy changed to link_only) has to lose
        # its vectors too, not just keep them unread.
        if old_vector_ids and (reindex or not chunk_models):
            try:
                chroma_client.delete_chunks(old_vector_ids)
            except Exception as exc:
                db.rollback()
                raise DocumentIndexingError("Failed to delete stale document vectors.") from exc

    try:
        paths = wiki_service.update_source(
            knowledge_base_id=knowledge_base.id,
            knowledge_base_name=knowledge_base.name,
            document_id=document.id,
            title=title,
            content=content,
            source_type=document.source_type,
            file_name=document.file_name,
            tags=document.tags,
            source_url=document.source_url,
            source_platform=document.source_platform,
            source_author=document.source_author,
            source_account=document.source_account,
            source_published_at=document.source_published_at,
            source_captured_at=document.source_captured_at,
            source_policy=document.source_policy,
            source_disclosures=document.source_disclosures,
            content_hash=document.content_hash,
            source_name=_Path(document.source_path or document.raw_path or "").stem or None,
            topic_name=_Path(document.topic_path or "").stem or None,
        )
    except Exception as exc:
        db.rollback()
        # The Markdown may be half-rewritten; put the previous content back rather than
        # leave a workspace that no longer matches the row it was derived from.
        _restore_wiki(document, knowledge_base, previous)
        raise DocumentIndexingError("Failed to maintain wiki workspace.") from exc

    document.raw_path = paths["raw_path"]
    document.source_path = paths["source_path"]
    document.topic_path = paths["topic_path"]
    db.commit()
    db.refresh(document)
    wiki_service.complete_ingest(
        knowledge_base_id=knowledge_base.id,
        document_id=document.id,
        title=title,
        content=content,
        synthesize_topic=payload.synthesize_topic,
    )
    return document


def _restore_wiki(document: Document, knowledge_base: KnowledgeBase, previous: dict[str, str]) -> None:
    """Best effort rollback of the Markdown, so files and rows agree again."""
    try:
        wiki_service.update_source(
            knowledge_base_id=knowledge_base.id,
            knowledge_base_name=knowledge_base.name,
            document_id=document.id,
            title=previous["title"],
            content=previous["content"],
            source_type=document.source_type,
            file_name=document.file_name,
            tags=document.tags,
            source_url=document.source_url,
            source_platform=document.source_platform,
            source_author=document.source_author,
            source_account=document.source_account,
            source_published_at=document.source_published_at,
            source_captured_at=document.source_captured_at,
            source_policy=previous["source_policy"],
            source_disclosures=document.source_disclosures,
            content_hash=previous["content_hash"],
            source_name=_Path(document.source_path or "").stem or None,
            topic_name=_Path(document.topic_path or "").stem or None,
        )
    except Exception:  # noqa: BLE001 - the caller is already raising the real failure
        pass


def _vector_id(knowledge_base_id: int, document: Document, chunk_index: int) -> str:
    """Deterministic vector id for one chunk.

    The content hash is part of it because SQLite reuses row ids: after an in-place update
    the new chunk rows can carry the ids of the rows they replaced, so an id derived from
    the row id alone would make re-indexing write vectors that the next step deletes as
    "stale" — leaving the document unindexed with no error anywhere.
    """
    return f"kb-{knowledge_base_id}:doc-{document.id}:{document.content_hash[:12]}:chunk-{chunk_index}"


def _index_document_chunks(
    knowledge_base: KnowledgeBase,
    document: Document,
    chunks: list[DocumentChunk],
) -> None:
    chunk_texts = [chunk.content for chunk in chunks]
    embeddings = embedding_service.embed_texts(chunk_texts)
    vector_ids = [_vector_id(knowledge_base.id, document, chunk.chunk_index) for chunk in chunks]
    metadatas = [
        {
            "knowledge_base_id": knowledge_base.id,
            "document_id": document.id,
            "chunk_id": chunk.id,
            "chunk_index": chunk.chunk_index,
            "title": document.title,
            "source_type": document.source_type,
            "source_url": document.source_url or "",
            "source_platform": document.source_platform or "",
            "source_author": document.source_author or "",
            "source_account": document.source_account or "",
            "source_published_at": _iso_datetime(document.source_published_at),
            "source_captured_at": _iso_datetime(document.source_captured_at),
            "source_policy": document.source_policy,
            "content_hash": document.content_hash,
        }
        for chunk in chunks
    ]

    chroma_client.add_chunks(
        vector_ids=vector_ids,
        embeddings=embeddings,
        documents=chunk_texts,
        metadatas=metadatas,
    )

    for chunk, vector_id in zip(chunks, vector_ids, strict=True):
        chunk.vector_id = vector_id


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_datetime(value: datetime | None) -> str:
    return _as_utc(value).isoformat() if value else ""


def content_hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()
