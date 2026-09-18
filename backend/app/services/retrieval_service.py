from datetime import datetime
from typing import Any

from app.db.schemas import SearchRequest, SearchResponse, SearchResult
from app.services import embedding_service, llm_service
from app.vector import chroma_client


class RetrievalError(RuntimeError):
    pass


def search_knowledge_base(payload: SearchRequest) -> SearchResponse:
    try:
        query_embedding = embedding_service.embed_texts([payload.query])[0]
        raw_results = chroma_client.query_chunks(
            query_embedding=query_embedding,
            knowledge_base_id=payload.knowledge_base_id,
            top_k=payload.top_k,
        )
    except Exception as exc:
        raise RetrievalError("Failed to search knowledge base.") from exc

    results = _parse_chroma_results(raw_results)
    synthesis = llm_service.synthesize_answer(
        question=payload.query,
        evidence=[
            llm_service.Evidence(
                reference=str(index + 1),
                title=result.title or f"来源 #{result.document_id}",
                content=result.chunk,
            )
            for index, result in enumerate(results)
        ],
        mode="rag",
        override=payload.llm,
    )
    return SearchResponse(
        query=payload.query,
        results=results,
        answer=synthesis.answer,
        answer_mode="llm" if synthesis.answer else "retrieval",
        model=synthesis.model,
        model_error=synthesis.error,
    )


def _parse_chroma_results(raw_results: dict[str, Any]) -> list[SearchResult]:
    documents = _first_result_list(raw_results.get("documents"))
    metadatas = _first_result_list(raw_results.get("metadatas"))
    distances = _first_result_list(raw_results.get("distances"))

    results: list[SearchResult] = []
    for index, chunk in enumerate(documents):
        metadata = metadatas[index] if index < len(metadatas) and isinstance(metadatas[index], dict) else {}
        distance = distances[index] if index < len(distances) else None
        results.append(
            SearchResult(
                document_id=int(metadata.get("document_id", 0)),
                title=str(metadata.get("title", "")),
                chunk=str(chunk),
                score=_distance_to_score(distance),
                chunk_id=_optional_int(metadata.get("chunk_id")),
                chunk_index=_optional_int(metadata.get("chunk_index")),
                source_url=_optional_str(metadata.get("source_url")),
                source_platform=_optional_str(metadata.get("source_platform")),
                source_author=_optional_str(metadata.get("source_author")),
                source_account=_optional_str(metadata.get("source_account")),
                source_published_at=_optional_datetime(metadata.get("source_published_at")),
                source_captured_at=_optional_datetime(metadata.get("source_captured_at")),
                source_policy=_optional_str(metadata.get("source_policy")),
                content_hash=_optional_str(metadata.get("content_hash")),
            )
        )

    return results


def _first_result_list(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, list) and value and isinstance(value[0], list):
        return value[0]
    if isinstance(value, list):
        return value
    return []


def _distance_to_score(distance: Any) -> float:
    if distance is None:
        return 0.0
    try:
        numeric_distance = float(distance)
    except (TypeError, ValueError):
        return 0.0
    if numeric_distance < 0:
        return 0.0
    return round(1.0 / (1.0 + numeric_distance), 4)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _optional_datetime(value: Any) -> datetime | None:
    cleaned = _optional_str(value)
    if not cleaned:
        return None
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
