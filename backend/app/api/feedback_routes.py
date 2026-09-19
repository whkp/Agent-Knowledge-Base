from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.schemas import QueryFeedbackCreate, QueryFeedbackPage, QueryFeedbackRead
from app.services import feedback_service, knowledge_service


router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/feedback", tags=["feedback"])


@router.post("", response_model=QueryFeedbackRead, status_code=status.HTTP_201_CREATED)
def create_query_feedback(
    knowledge_base_id: int,
    payload: QueryFeedbackCreate,
    db: Session = Depends(get_db),
):
    """Record whether one answer was useful. Ratings are append-only."""
    _ensure_kb(db, knowledge_base_id)
    return feedback_service.create_feedback(db, knowledge_base_id, payload)


@router.get("", response_model=QueryFeedbackPage)
def list_query_feedback(
    knowledge_base_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Read the signal back: what was rated, and how the totals look."""
    _ensure_kb(db, knowledge_base_id)
    items, total, positive, negative = feedback_service.list_feedback(db, knowledge_base_id, page, page_size)
    return QueryFeedbackPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        positive=positive,
        negative=negative,
    )


def _ensure_kb(db: Session, knowledge_base_id: int) -> None:
    if knowledge_service.get_knowledge_base(db, knowledge_base_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found.")
