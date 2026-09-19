from fastapi import APIRouter

from app.services import retrieval_strategy


router = APIRouter(prefix="/retrieval-strategies", tags=["retrieval"])


@router.get("")
def list_retrieval_strategies() -> dict[str, list[dict[str, str | int]]]:
    """The named retrieval strategies a query may ask for.

    Read-only by design: the set is code, so changing it goes through review and tests,
    while choosing one is data that travels with the request, the response, and feedback.
    """
    return {
        "items": [
            {
                "id": strategy.id,
                "label": strategy.label,
                "description": strategy.description,
                "hops": strategy.hops,
                "neighbour_limit": strategy.neighbour_limit,
            }
            for strategy in retrieval_strategy.list_strategies()
        ]
    }
