from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.schemas import (
    WikiGraphResponse,
    WikiLintResponse,
    WikiPagePage,
    WikiPageRead,
    WikiPageUpsert,
    WikiQueryRequest,
    WikiQueryResponse,
    WikiStatusResponse,
)
from app.services import knowledge_service, wiki_service


router = APIRouter(prefix="/knowledge-bases/{knowledge_base_id}/wiki", tags=["wiki"])


@router.get("/status", response_model=WikiStatusResponse)
def get_wiki_status(knowledge_base_id: int, db: Session = Depends(get_db)):
    _ensure_kb(db, knowledge_base_id)
    return wiki_service.status(knowledge_base_id)


@router.get("/pages", response_model=WikiPagePage)
def list_wiki_pages(
    knowledge_base_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    _ensure_kb(db, knowledge_base_id)
    items, total = wiki_service.list_pages(knowledge_base_id, page, page_size)
    return WikiPagePage(items=items, total=total, page=page, page_size=page_size)


@router.get("/pages/{page_path:path}", response_model=WikiPageRead)
def read_wiki_page(knowledge_base_id: int, page_path: str, db: Session = Depends(get_db)):
    _ensure_kb(db, knowledge_base_id)
    try:
        page = wiki_service.get_page(knowledge_base_id, page_path)
    except wiki_service.WikiWorkspaceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wiki page not found.")
    return page


@router.put("/pages/{page_path:path}", response_model=WikiPageRead)
def write_wiki_page(knowledge_base_id: int, page_path: str, payload: WikiPageUpsert, db: Session = Depends(get_db)):
    _ensure_kb(db, knowledge_base_id)
    if payload.path != page_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload path must match URL path.")
    try:
        return wiki_service.save_page(knowledge_base_id, page_path, payload.content)
    except wiki_service.WikiWorkspaceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/query", response_model=WikiQueryResponse)
def query_wiki(knowledge_base_id: int, payload: WikiQueryRequest, db: Session = Depends(get_db)):
    _ensure_kb(db, knowledge_base_id)
    return wiki_service.query_wiki(knowledge_base_id, payload.query, payload.top_k, payload.save_as, payload.llm)


@router.post("/lint", response_model=WikiLintResponse)
def lint_wiki(knowledge_base_id: int, db: Session = Depends(get_db)):
    _ensure_kb(db, knowledge_base_id)
    return wiki_service.lint_wiki(knowledge_base_id)


@router.get("/graph", response_model=WikiGraphResponse)
def get_wiki_graph(knowledge_base_id: int, db: Session = Depends(get_db)):
    _ensure_kb(db, knowledge_base_id)
    nodes, edges = wiki_service.graph(knowledge_base_id)
    return WikiGraphResponse(nodes=nodes, edges=edges)


@router.post("/rebuild-index", response_model=WikiStatusResponse)
def rebuild_wiki_index(knowledge_base_id: int, db: Session = Depends(get_db)):
    _ensure_kb(db, knowledge_base_id)
    wiki_service.rebuild_index(knowledge_base_id)
    return wiki_service.status(knowledge_base_id)


def _ensure_kb(db: Session, knowledge_base_id: int) -> None:
    if knowledge_service.get_knowledge_base(db, knowledge_base_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found.")
