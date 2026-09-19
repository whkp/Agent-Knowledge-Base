"""Answer feedback: the signal retrieval strategies can be evaluated on.

Kept deliberately small. A rating is business data in SQLite, never a wiki page,
and every row carries enough context (query, cited pages, answer mode, model) to
replay the query later when comparing strategies.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import QueryFeedback
from app.db.schemas import QueryFeedbackCreate


def create_feedback(db: Session, knowledge_base_id: int, payload: QueryFeedbackCreate) -> QueryFeedback:
    feedback = QueryFeedback(
        knowledge_base_id=knowledge_base_id,
        mode=payload.mode,
        query=payload.query,
        rating=payload.rating,
        note=payload.note,
        answer=payload.answer,
        answer_mode=payload.answer_mode,
        model=payload.model,
        source_paths=payload.source_paths,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback


def list_feedback(
    db: Session,
    knowledge_base_id: int,
    page: int,
    page_size: int,
) -> tuple[list[QueryFeedback], int, int, int]:
    """Newest first, with the running totals so a caller sees the signal at a glance."""
    offset = (page - 1) * page_size
    where = QueryFeedback.knowledge_base_id == knowledge_base_id
    total = db.scalar(select(func.count()).select_from(QueryFeedback).where(where)) or 0
    positive = (
        db.scalar(
            select(func.count()).select_from(QueryFeedback).where(where, QueryFeedback.rating > 0)
        )
        or 0
    )
    items = db.scalars(
        select(QueryFeedback)
        .where(where)
        .order_by(QueryFeedback.created_at.desc(), QueryFeedback.id.desc())
        .offset(offset)
        .limit(page_size)
    ).all()
    return list(items), total, positive, total - positive
