"""Replay recorded questions against the retrieval strategies.

This is a **diff tool, not a scorer**. Nothing here has gold labels, so it cannot tell you
that one strategy is better. What it can tell you is concrete and checkable:

- For questions a person marked 👍: does the strategy still retrieve the pages that
  answer was built on? Losing them is a regression.
- For questions a person marked 👎: does the strategy actually change the result? A
  strategy that returns the same wrong pages is not an improvement.
- What each strategy costs in linked pages and link hops.

Replays never write to `log.md`, so running this a hundred times leaves the workspace's
activity history intact. Only page-first (`wiki`) feedback can be replayed: the raw-source
path needs the vector store, which is not part of this script.

    PYTHONPATH=. python scripts/replay_queries.py --knowledge-base-id 1
    PYTHONPATH=. python scripts/replay_queries.py --strategies auto,deep --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.db import models  # noqa: F401 - registers SQLAlchemy metadata
from app.db.database import SessionLocal, ensure_schema_compatibility, engine
from app.db.models import QueryFeedback
from app.db.schemas import LLMConfigurationInput
from app.services import retrieval_strategy, wiki_service


NO_MODEL = LLMConfigurationInput(enabled=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay recorded questions against retrieval strategies.")
    parser.add_argument("--knowledge-base-id", type=int, default=None, help="Only replay one knowledge base.")
    parser.add_argument("--strategies", default=None, help="Comma separated ids. Defaults to every strategy.")
    parser.add_argument("--top-k", type=int, default=8, help="Pages to retrieve per replay.")
    parser.add_argument("--limit", type=int, default=50, help="How many recent feedback rows to replay.")
    parser.add_argument("--rating", type=int, choices=[1, -1], default=None, help="Only replay 👍 or only 👎 rows.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable output instead of a table.")
    return parser.parse_args()


def load_rows(args: argparse.Namespace) -> list[QueryFeedback]:
    ensure_schema_compatibility(engine)
    with SessionLocal() as session:
        statement = select(QueryFeedback).where(QueryFeedback.mode == "wiki")
        if args.knowledge_base_id is not None:
            statement = statement.where(QueryFeedback.knowledge_base_id == args.knowledge_base_id)
        if args.rating is not None:
            statement = statement.where(QueryFeedback.rating == args.rating)
        statement = statement.order_by(QueryFeedback.created_at.desc(), QueryFeedback.id.desc()).limit(args.limit)
        return list(session.scalars(statement).all())


def replay(row: QueryFeedback, strategy_id: str, top_k: int) -> dict:
    response = wiki_service.query_wiki(
        row.knowledge_base_id,
        row.query,
        top_k=top_k,
        llm=NO_MODEL,
        strategy_id=strategy_id,
        record_activity=False,
    )
    retrieved = [item.path for item in response.results]
    recorded = [path for path in row.source_paths if path.endswith(".md")]
    kept = [path for path in recorded if path in retrieved]
    top = retrieved[0] if retrieved else None
    return {
        "query": row.query,
        "rating": row.rating,
        "strategy": response.strategy,
        "hops": response.hops,
        "direct": len([item for item in response.results if not item.related]),
        "related": len([item for item in response.results if item.related]),
        "recorded": len(recorded),
        "kept": len(kept),
        # "Changed" means the top piece of evidence changed, which is what a reader would
        # notice. Comparing whole result sets flags almost everything once recall improves,
        # and would let a strategy that only added a hop look like an improvement.
        "changed": top_changed(recorded, top),
        "top": top,
        "recorded_top": recorded[0] if recorded else None,
    }


def top_changed(recorded: list[str], top: str | None) -> bool:
    """Whether the leading citation differs from the one that was rated."""
    if not recorded or top is None:
        return False
    return top != recorded[0]


def summarise(rows: list[dict]) -> dict:
    liked = [row for row in rows if row["rating"] == 1]
    disliked = [row for row in rows if row["rating"] == -1]
    recorded_total = sum(row["recorded"] for row in liked)
    kept_total = sum(row["kept"] for row in liked)
    return {
        "cases": len(rows),
        "liked": len(liked),
        "disliked": len(disliked),
        "kept_on_liked": kept_total,
        "recorded_on_liked": recorded_total,
        "changed_on_disliked": sum(1 for row in disliked if row["changed"]),
        "average_related": round(sum(row["related"] for row in rows) / len(rows), 2) if rows else 0.0,
        "average_hops": round(sum(row["hops"] for row in rows) / len(rows), 2) if rows else 0.0,
    }


def main() -> int:
    args = parse_args()
    ids = [item.strip() for item in args.strategies.split(",")] if args.strategies else [
        strategy.id for strategy in retrieval_strategy.list_strategies()
    ]
    for strategy_id in ids:
        try:
            retrieval_strategy.get_strategy(strategy_id)
        except retrieval_strategy.UnknownStrategyError as exc:
            print(str(exc), file=sys.stderr)
            return 2

    feedback_rows = load_rows(args)
    if not feedback_rows:
        print("没有可回放的 wiki 反馈。先在回答下方点一次 👍/👎。")
        return 0
    skipped = 0
    with SessionLocal() as session:
        skipped = session.query(QueryFeedback).filter(QueryFeedback.mode != "wiki").count()

    results: dict[str, dict] = {}
    for strategy_id in ids:
        replayed = [replay(row, strategy_id, args.top_k) for row in feedback_rows]
        results[strategy_id] = {"rows": replayed, "summary": summarise(replayed)}

    if args.json:
        print(json.dumps({"strategies": results, "skipped_non_wiki": skipped}, ensure_ascii=False, indent=2))
        return 0

    print(f"回放 {len(feedback_rows)} 条 wiki 反馈（跳过的原始资料反馈：{skipped}）\n")
    for strategy_id, payload in results.items():
        summary = payload["summary"]
        keep = (
            f"{summary['kept_on_liked']}/{summary['recorded_on_liked']}"
            if summary["recorded_on_liked"]
            else "n/a"
        )
        print(f"== 策略 {strategy_id} ==")
        print(f"   👍 用例仍找到原引用：{keep}    👎 用例结果发生变化：{summary['changed_on_disliked']}/{summary['disliked']}")
        print(f"   平均关联页 {summary['average_related']}   平均跳数 {summary['average_hops']}")
        for row in payload["rows"]:
            flag = "👍" if row["rating"] == 1 else "👎"
            print(
                f"     {flag} {row['query'][:24]:<26} 直接 {row['direct']} 关联 {row['related']} "
                f"跳数 {row['hops']} 引用保留 {row['kept']}/{row['recorded']}"
            )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
