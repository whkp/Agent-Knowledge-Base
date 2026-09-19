"""Turn replay evidence into a reviewable proposal.

The loop this closes: recorded 👍/👎 → replay comparison → a Markdown proposal → a human
merges (or does not). Promotion stays a normal git change, so the audit trail and the
rollback mechanism are the same thing.

The recommendation is deliberately conservative. Replay compares, it does not score: there
are no gold answers, so "the result changed" is not "the result improved". This script
therefore recommends **no change** unless a candidate keeps every citation people liked and
also changes at least one case people marked bad — and even then it says a human should look,
not that the change is correct.

    PYTHONPATH=. python scripts/propose_strategy_change.py --knowledge-base-id 1
    PYTHONPATH=. python scripts/propose_strategy_change.py --check     # exit 1 on regression

`--check` is the CI-shaped guard: it replays only the *current default* against liked
feedback and fails if that default no longer retrieves a citation someone liked.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.services import retrieval_strategy
from app.services.retrieval_strategy import RetrievalStrategy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay_queries import load_rows, replay, summarise  # noqa: E402

# Proposals live at the repository root: they are review artefacts, not runtime data.
PROPOSAL_DIR = Path(__file__).resolve().parents[2] / "proposals"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a strategy proposal from replay evidence.")
    parser.add_argument("--knowledge-base-id", type=int, default=None)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--rating", type=int, choices=[1, -1], default=None, help="Only propose from 👍 or only from 👎 rows.")
    parser.add_argument("--check", action="store_true", help="Only verify the current default; exit 1 on regression.")
    parser.add_argument("--out", default=None, help="Proposal path. Defaults to proposals/<date>-<strategy>.md")
    return parser.parse_args()


def evaluate(rows, strategies: list[RetrievalStrategy], top_k: int) -> dict[str, dict]:
    return {
        strategy.id: {"rows": [replay(row, strategy.id, top_k) for row in rows]}
        for strategy in strategies
    }


def compare(default_id: str, results: dict[str, dict]) -> dict[str, dict]:
    """Compare each candidate against the current default, row by row.

    Comparing a candidate against the *recorded* result would credit it for changes the
    default already makes (a scoring change, for instance), which is how a strategy that
    only added a link hop can look like an improvement.
    """
    baseline = results[default_id]["rows"]
    comparisons: dict[str, dict] = {}
    for strategy_id, payload in results.items():
        if strategy_id == default_id:
            continue
        rows = payload["rows"]
        regressions = []
        improvements = []
        for index, row in enumerate(rows):
            base = baseline[index] if index < len(baseline) else None
            if base is None:
                continue
            if row["kept"] < base["kept"]:
                regressions.append(row)
            if row["rating"] == -1 and row["changed"] and not base["changed"]:
                improvements.append(row)
        comparisons[strategy_id] = {"regressions": regressions, "improvements": improvements}
    return comparisons


def recommendation(default_id: str, results: dict[str, dict]) -> tuple[str, str]:
    """Return (recommended strategy id, reason). Never recommends a losing candidate."""
    baseline_liked = [row for row in results[default_id]["rows"] if row["rating"] == 1]
    baseline_kept = sum(row["kept"] for row in baseline_liked)
    baseline_recorded = sum(row["recorded"] for row in baseline_liked)
    if not baseline_recorded:
        return default_id, "没有任何 👍 用例带引用记录，缺少可比对的证据。"

    comparisons = compare(default_id, results)
    safe = {
        strategy_id: item
        for strategy_id, item in comparisons.items()
        if not item["regressions"]
    }
    if not safe:
        losing = "、".join(f"`{sid}`" for sid in comparisons)
        return default_id, f"全部候选策略都丢掉了默认策略能找到的 👍 引用（{losing}），保持现状。"

    improving = {sid: item for sid, item in safe.items() if item["improvements"]}
    if not improving:
        return default_id, (
            f"候选策略（{'、'.join(safe)}）保留了全部 👍 引用，但没有改变任何默认策略没改变过的 👎 用例，"
            "换上它们只是多付成本。"
        )

    best_id, best = max(improving.items(), key=lambda item: len(item[1]["improvements"]))
    return best_id, (
        f"`{best_id}` 保留了全部 👍 引用，并且改变了 {len(best['improvements'])} 个默认策略未曾改变的 👎 用例。"
        "这值得人工判断，但证据本身只说明「首条证据变了」，不说明「变得对了」。"
    )


def render_proposal(kb_id: int | None, default_id: str, results: dict[str, dict], rows, chosen: str, reason: str) -> str:
    today = date.today().isoformat()
    scope = f"知识库 {kb_id}" if kb_id is not None else "所有知识库"
    lines = [
        f"# Retrieval strategy proposal — {today}",
        "",
        f"- 范围：{scope}",
        f"- 当前默认策略：`{default_id}`（`WIKI_QUERY_DEFAULT_STRATEGY`）",
        f"- 参与对比的评分样本：{len(rows)} 条（👍 {sum(1 for row in rows if row.rating == 1)} / 👎 {sum(1 for row in rows if row.rating == -1)}）",
        f"- 建议：**{chosen}** — {reason}",
        "",
        "## 证据",
        "",
        "| 策略 | 👍 引用保留 | 相对默认的回归 | 相对默认的新改变 | 平均关联页 | 平均跳数 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    comparisons = compare(default_id, results)
    for strategy_id, payload in results.items():
        summary = summarise(payload["rows"])
        keep = f"{summary['kept_on_liked']}/{summary['recorded_on_liked']}" if summary["recorded_on_liked"] else "n/a"
        marker = " ← 当前默认" if strategy_id == default_id else (" ← 建议" if strategy_id == chosen and chosen != default_id else "")
        item = comparisons.get(strategy_id)
        regressions = f"{len(item['regressions'])}" if item else "—"
        improvements = f"{len(item['improvements'])}" if item else "—"
        lines.append(
            f"| `{strategy_id}`{marker} | {keep} | {regressions} | {improvements} "
            f"| {summary['average_related']} | {summary['average_hops']} |"
        )

    lines += ["", "## 逐条差异（相对当前默认策略）", ""]
    for strategy_id, payload in results.items():
        if strategy_id == default_id:
            continue
        noteworthy = comparisons[strategy_id]["regressions"] + comparisons[strategy_id]["improvements"]
        if not noteworthy:
            continue
        lines.append(f"### `{strategy_id}`")
        lines.append("")
        for row in noteworthy:
            flag = "👍" if row["rating"] == 1 else "👎"
            lines.append(
                f"- {flag} `{row['query']}` — 引用保留 {row['kept']}/{row['recorded']}，"
                f"直接 {row['direct']} 关联 {row['related']}，跳数 {row['hops']}"
            )
            if row["recorded_top"] != row["top"]:
                lines.append(f"  - 首条证据：`{row['recorded_top']}` → `{row['top']}`")
        lines.append("")

    lines += [
        "## 这份证据不能说明什么",
        "",
        "- 没有标注答案，「首条证据变了」不等于「变得对了」。",
        "- 只覆盖有人评过的问题，而人们倾向于只评判那些感觉不对的问题。",
        "- 工作区里根本没有的材料，任何策略都召不回来，它们看起来会一样好。",
        "- 因此上面这一条建议只是「值得人工看一眼」，不是「应该合并」。",
        "",
        "## 如果决定推进",
        "",
        f"1. 改 `backend/app/config.py` 的默认值或 `WIKI_QUERY_DEFAULT_STRATEGY`，并同步 `backend/.env.example`。",
        f"2. 若涉及新行为，在 `backend/app/services/retrieval_strategy.py` 里补策略定义与测试。",
        f"3. 跑 `PYTHONPATH=. python -m pytest tests -q` 与 `PYTHONPATH=. python scripts/replay_queries.py --rating 1`。",
        f"4. 在本文件末尾记录结论并提交，让这次改动可回滚、可追溯。",
        "",
    ]
    return "\n".join(lines)


def run_check(rows, default_id: str, top_k: int) -> int:
    liked = [row for row in rows if row.rating == 1]
    if not liked:
        print("没有 👍 反馈可用于回归检查。")
        return 0
    replayed = [replay(row, default_id, top_k) for row in liked]
    regressions = [row for row in replayed if row["kept"] < row["recorded"]]
    if not regressions:
        kept = sum(row["kept"] for row in replayed)
        recorded = sum(row["recorded"] for row in replayed)
        print(f"默认策略 `{default_id}` 保留了全部被点赞的引用（{kept}/{recorded}）。")
        return 0
    print(f"默认策略 `{default_id}` 丢掉了被点赞的引用：", file=sys.stderr)
    for row in regressions:
        print(f"  👎? `{row['query']}` 保留 {row['kept']}/{row['recorded']}", file=sys.stderr)
    return 1


def main() -> int:
    args = parse_args()
    settings = get_settings()
    default_id = settings.wiki_query_default_strategy
    rows = load_rows(args)
    if not rows:
        print("没有可用的 wiki 反馈：先在回答下方点一次 👍/👎。")
        return 0

    if args.check:
        return run_check(rows, default_id, args.top_k)

    strategies = retrieval_strategy.list_strategies()
    results = evaluate(rows, strategies, args.top_k)
    chosen, reason = recommendation(default_id, results)
    proposal = render_proposal(args.knowledge_base_id, default_id, results, rows, chosen, reason)

    out = Path(args.out) if args.out else PROPOSAL_DIR / f"{date.today().isoformat()}-retrieval-strategy.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(proposal, encoding="utf-8")
    print(f"已写出提案：{out}")
    print(f"建议：{chosen} — {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
