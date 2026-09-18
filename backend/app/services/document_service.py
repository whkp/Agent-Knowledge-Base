from datetime import datetime, timezone
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Document, DocumentChunk, KnowledgeBase
from app.db.schemas import SourceSnapshotCreate, TextDocumentCreate
from app.services import embedding_service
from app.services.chunk_service import split_text
from app.services import wiki_service
from app.vector import chroma_client


class DocumentIndexingError(RuntimeError):
    pass


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
        wiki_service.ingest_source(
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


def _index_document_chunks(
    knowledge_base: KnowledgeBase,
    document: Document,
    chunks: list[DocumentChunk],
) -> None:
    chunk_texts = [chunk.content for chunk in chunks]
    embeddings = embedding_service.embed_texts(chunk_texts)
    vector_ids = [f"kb-{knowledge_base.id}:doc-{document.id}:chunk-{chunk.id}" for chunk in chunks]
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
