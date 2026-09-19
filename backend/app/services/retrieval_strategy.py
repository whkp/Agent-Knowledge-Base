"""Named retrieval strategies.

Two different things live here, and the split is the point:

- The **set** of strategies is code. Adding or changing one is a reviewed, tested change,
  which is the gate that stops a model from quietly rewriting how retrieval works.
- The **choice** of strategy is data. It travels in the request, comes back in the
  response, and is stored on the feedback row, so a later replay can tell which
  configuration produced which answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings


class UnknownStrategyError(ValueError):
    """Raised when a caller asks for a strategy that does not exist."""


@dataclass(frozen=True)
class RetrievalStrategy:
    id: str
    label: str
    description: str
    # Number of link hops to follow, or "auto" to decide from the best match score.
    hops: int | str
    neighbour_limit: int
    # Add page embeddings to the lexical ranking, so a paraphrase can still match.
    use_vectors: bool = False
    # Let the model rewrite the question into search terms before the deterministic search.
    plan_query: bool = False


DEFAULT_STRATEGY_ID = "auto"
AUTO = "auto"


def list_strategies() -> list[RetrievalStrategy]:
    settings = get_settings()
    return [
        RetrievalStrategy(
            id=AUTO,
            label="自动",
            description="按最好命中的把握程度决定跳数：有把握只走一跳，证据不足再走一跳。",
            hops=AUTO,
            neighbour_limit=settings.wiki_query_neighbour_limit,
        ),
        RetrievalStrategy(
            id="local",
            label="只用直接命中",
            description="不沿链接扩展，只返回直接命中的页面。适合作为对比基线。",
            hops=1,
            neighbour_limit=0,
        ),
        RetrievalStrategy(
            id="deep",
            label="深挖",
            description="总是走两跳并放宽关联页上限，用精确度换召回。",
            hops=2,
            neighbour_limit=6,
        ),
        RetrievalStrategy(
            id="hybrid",
            label="语义补充",
            description="在词法排序上叠加页面向量相似度，让换个说法的提问也能命中；需要本地 embedding 模型。",
            hops=AUTO,
            neighbour_limit=settings.wiki_query_neighbour_limit,
            use_vectors=True,
        ),
        RetrievalStrategy(
            id="planned",
            label="模型改写后检索",
            description="先用模型把问题改写成检索用词，再走语义补充检索；需要已启用模型，失败时退回原问题。",
            hops=AUTO,
            neighbour_limit=settings.wiki_query_neighbour_limit,
            use_vectors=True,
            plan_query=True,
        ),
    ]


def default_strategy_id() -> str:
    return get_settings().wiki_query_default_strategy


def get_strategy(strategy_id: str | None) -> RetrievalStrategy:
    wanted = (strategy_id or default_strategy_id()).strip() or default_strategy_id()
    for strategy in list_strategies():
        if strategy.id == wanted:
            return strategy
    available = ", ".join(item.id for item in list_strategies())
    raise UnknownStrategyError(f"未知的检索策略：{wanted}。可用：{available}")


def resolve_hops(strategy: RetrievalStrategy, best_score: float | None, deep_threshold: float) -> int:
    """How many link hops this strategy should follow for this particular query."""
    if strategy.hops != AUTO:
        return int(strategy.hops)
    if best_score is None:
        return 1
    return 2 if best_score < deep_threshold else 1
