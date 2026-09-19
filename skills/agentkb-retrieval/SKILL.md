---
name: agentkb-retrieval
description: >
  USE when the user wants AgentKB's retrieval to be evaluated or improved — e.g.
  "检索不准 / 为什么搜不到 / 让知识库查得更准 / 评估检索策略 / 换一个检索策略 /
  把默认策略改成 X / AgentKB 检索质量", or when they ask to work through recorded
  👍/👎 feedback on answers.

  Also USE when a task touches AgentKB retrieval configuration: named strategies
  (`auto`, `local`, `deep`, `hybrid`, `planned`), the knobs in
  `docs/RETRIEVAL.md`, or the replay/proposal scripts.

  NOT for: adding new content to a knowledge base (that is an ingest task), writing
  the wiki by hand, or tuning a model provider's settings. NOT for questions that
  merely need an answer from the wiki — use `query_wiki` directly for those.
triggers:
  - evaluate: 评估检索/检索质量/召回/检索不准/搜索不到/搜不到
  - improve: 改进检索/优化检索/换策略/默认策略/自进化/调参
  - feedback: 反馈/点赞/点踩/👍/👎/评分记录
---

# AgentKB retrieval evaluation

AgentKB never lets a model rewrite how retrieval works. It lets a model (you) read recorded
outcomes, run comparisons, and propose a change that a human reviews. Your job is to produce
evidence and a proposal, not to silently change behaviour.

Read `docs/SELF_EVOLUTION.md` for the reasoning and `docs/RETRIEVAL.md` for the mechanics
before recommending anything.

## Preconditions

- Backend running (`GET /health` on `BACKEND_API_URL`, default `http://127.0.0.1:8000`).
- Recorded feedback exists. If it does not, say so and stop — there is nothing to evaluate.
  Feedback is produced by a person clicking 👍/👎 under an answer in the workbench; you cannot
  generate it, and you must never infer it.

## Procedure

```bash
cd backend

# 1. Is the current default still safe? Fails (exit 1) if it dropped a citation someone liked.
PYTHONPATH=. python scripts/replay_queries.py --knowledge-base-id <id> --rating 1
PYTHONPATH=. python scripts/propose_strategy_change.py --knowledge-base-id <id> --check

# 2. Compare every strategy on the recorded questions.
PYTHONPATH=. python scripts/replay_queries.py --knowledge-base-id <id>
PYTHONPATH=. python scripts/replay_queries.py --knowledge-base-id <id> --json   # for parsing

# 3. Write the review artefact.
PYTHONPATH=. python scripts/propose_strategy_change.py --knowledge-base-id <id>
```

The proposal lands in `proposals/<date>-retrieval-strategy.md` with the evidence table, the
per-case differences relative to the current default, and the exact change to make.

## Rules

1. **A regression is decisive.** If a strategy keeps fewer liked citations than the current
   default, do not propose it, whatever its other numbers say. `local` dropping liked
   citations is the clearest example: link expansion is earning its cost.
2. **Relative to the default, not to the recording.** A candidate only counts as improving a
   case when it hides a citation that was flagged as wrong *and* the default still returns it.
   Otherwise a scoring change made months ago makes every candidate look like an improvement.
   Reordering the result or adding a link hop is not an improvement on its own.
3. **Never write a recommendation the evidence cannot support.** "The top citation changed"
   is not "the answer got better", and neither is "a flagged citation disappeared": the flag
   is one person's judgement at one moment. There are no gold answers.
4. **Never edit retrieval to work around the gate.** The strategy set is code so that changes
   are reviewed and tested. Do not add a hidden flag, a runtime override, or an
   environment-only tweak that bypasses `retrieval_strategy.py` and its tests.
5. **Do not invent feedback.** If the user has not rated answers, the correct answer is that
   there is no signal yet; suggest using the workbench, and offer to improve something that
   does not need signal (a bug, a missing test, an obvious scoring defect).
6. **Quote the numbers you relied on**, with the command that produced them, in whatever you
   report back.

## MCP tools you will need

| Tool | Use |
| --- | --- |
| `list_retrieval_strategies` | ids and trade-offs, without guessing |
| `search_with_strategy` | run one question through one strategy, retrieval only |
| `list_answer_feedback` | read the recorded 👍/👎 and their totals |
| `query_wiki` | the default retrieval path, for reproducing what a user saw |
| `lint_wiki` | structural health, useful when a bad answer is really a broken wiki |

Two contracts to respect while calling them: retrieval tools force the Backend model off
(`llm.enabled=false`) so the reasoning stays with you, and ingest tools pass
`synthesize_topic=false` so an ingest never rewrites topic pages through a model.

## Reporting back

Give the user, in this order:

1. What the recorded signal says (how many ratings, what they were).
2. The table from the proposal, with the current default marked.
3. Which cases changed, how the top citation moved, and whether the citations people
   flagged are still coming back.
4. What the evidence cannot show.
5. A recommendation that is either a specific change with its file and line, or "keep the
   default".

## Installing this skill

The skill lives in the repository so it is versioned with the retrieval it describes. Point
your agent's skill directory at it instead of copying, so improvements travel automatically:

```bash
ln -s "$(pwd)/skills/agentkb-retrieval" ~/.agents/skills/agentkb-retrieval
```
