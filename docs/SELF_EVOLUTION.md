# Self-evolution without a self-modifying agent

What "self-evolution" means in AgentKB, who does each part of it, and why the project does
**not** put an agent inside the backend.

## The claim, stated precisely

AgentKB evolves on two axes, and they need different machinery:

| Axis | What improves | Signal | Status |
| --- | --- | --- | --- |
| **Knowledge** | the wiki: gaps filled, sources merged, contradictions flagged | lint issues, graph gaps, coverage | partially automated (ingest + topic synthesis), gap-driven research is not built |
| **Retrieval policy** | how a question is turned into evidence | explicit 👍/👎 per answer | the loop is closed end to end (signal → replay → proposal → review) |

This document is mostly about the second axis, because that is the one with a measurable
signal today.

**Evolution here means: the retrieval configuration changes because recorded outcomes say
it should, and every change is reviewable.** It does not mean a model rewriting its own
prompts nightly.

## Roles

The split is deliberate, and it is the same split the rest of the project uses.

| Step | Owner | Where it lives |
| --- | --- | --- |
| Answer a question | Backend | deterministic retrieval + optional single-shot synthesis |
| Rate an answer | **Human**, in the workbench | `POST .../feedback` → SQLite `query_feedback` |
| Multi-step exploration | **External agent** | Codex / Claude Code / OpenClaw over MCP, or a person using the graph, link panel and lint views |
| Compare strategies | Backend script | `backend/scripts/replay_queries.py` |
| Turn evidence into a proposal | Backend script | `backend/scripts/propose_strategy_change.py` |
| Decide and merge | **Human**, via git | a proposal file plus a strategy/config change |
| Read outcomes back | Backend + MCP | `GET .../feedback`, `list_answer_feedback` |

Nothing in that table requires an agent inside AgentKB, and two things in it require a human.
That is the point: the model is good at proposing, bad at knowing whether it improved
anything.

## The loop

```text
  apply the change                                       answer a question
  and merge the PR                                            │
        │                                                     ▼
        │                                             ┌───────────────┐
        └─────────────────────────────────────────────│  👍 / 👎 + reason
                                                      └───────┬───────┘
                                                              │
                       ┌──────────────────────────────────────┘
                       ▼
              record the signal
       query, answer, strategy_id, hops, vectors,
       planned, the cited pages, the rating
                       │
                       ▼
              replay: run the recorded questions
              through every strategy and diff
       ┌───────────────┴────────────────┐
       ▼                                ▼
  👍 cases: are the cited          👎 cases: did the result
  pages still retrieved?           actually change?
       │                                │
       └───────────────┬────────────────┘
                       ▼
            proposal file with the evidence,
            the exact change, and what the
            evidence cannot show
```

## Four things a loop needs, and where they are

1. **Signal** — `query_feedback` (SQLite). Append-only, so a change of mind is data too. It
   stores the retrieval configuration alongside the rating: `strategy_id`, plus the answer's
   `hops`, `vectors` and `planned` from the response. Without those, a 👍 could not be
   attributed to a configuration.
2. **Memory** — the strategy set in `app/services/retrieval_strategy.py` plus the
   configuration knobs in `docs/RETRIEVAL.md`. Deliberately code, not a runtime table:
   "changing what retrieval can do" stays a reviewed change.
3. **Replay** — `backend/scripts/replay_queries.py`. Compares strategies on recorded
   questions, reports which liked citations survive and what each strategy costs.
4. **Gate** — `backend/scripts/propose_strategy_change.py` writes a reviewable Markdown
   proposal, and promotion happens as a normal git change. Git is the audit log and the
   rollback mechanism at once.

## Procedure

```bash
# 0. make sure something was rated: use the workbench and click 👍 / 👎
cd backend

# 1. is the current default still safe? (exit 1 if it dropped a liked citation)
PYTHONPATH=. python scripts/replay_queries.py --knowledge-base-id 1 --rating 1

# 2. compare candidates
PYTHONPATH=. python scripts/replay_queries.py --knowledge-base-id 1

# 3. write a proposal from that evidence
PYTHONPATH=. python scripts/propose_strategy_change.py --knowledge-base-id 1

# 4. read proposals/…md, decide, apply the change, run the tests, open a PR
```

## What the evidence can and cannot say

This is the part that keeps the loop honest, and it is worth internalising before trusting
any number the scripts print.

**Can say**

- A strategy lost pages that a person marked as liked. That is a regression, full stop.
- A strategy changed the result for a question a person marked as bad. That is a candidate
  worth a human look — not a fix.
- A strategy costs more link hops or more linked pages. That is a price.

**Cannot say**

- That a candidate is *better*. There are no gold answers; "changed" is not "correct".
- Anything about questions nobody rated. The signal only covers what people actually asked
  and judged, which is biased towards questions that felt wrong.
- Anything about recall that the workspace cannot support at all. If the material is not
  there, every strategy returns nothing and they all look equal.

Because of that, the proposal script's default recommendation is **no change**, and it only
proposes a promotion when a candidate keeps every liked citation *and* changes at least one
disliked case. Even then the proposal says "worth a human look", not "merge this".

## Worked example (real numbers)

Recorded feedback on a 25-page workspace, six wiki ratings (four 👍, two 👎), replaying
all five strategies through `propose_strategy_change.py`:

| Strategy | 👍 citations retained | Regressions vs default | New changes vs default | avg linked pages | avg hops |
| --- | --- | --- | --- | --- | --- |
| `auto` ← current default | 14/14 | — | — | 0.33 | 1.17 |
| `local` | **12/14** | **1** | 0 | 0.00 | 1.00 |
| `deep` | 14/14 | 0 | 0 | 0.33 | **2.00** |
| `hybrid` | 14/14 | 0 | 0 | 0.00 | 1.17 |
| `planned` | 14/14 | 0 | 0 | 0.00 | 1.17 |

What a careful reader takes from this, and what the tool concluded:

- **Keep `auto`.** No candidate changed a case the default had not already changed, so
  promoting one would buy nothing and `deep` would double the link hops.
- `local` **loses a liked citation relative to the default** — link expansion is earning its
  cost, and that is now evidence rather than taste.
- `hybrid` and `planned` look equal here because the rated questions were worded close to the
  pages that answer them. That is a limitation of the evidence, not a verdict on vectors: the
  measurements in [RETRIEVAL.md](RETRIEVAL.md) show vectors helping on paraphrased questions
  that nobody has rated yet.

### The mistake this example caught

The first version of the proposal script compared each candidate against *the recorded
results* rather than against the current default, and it recommended `deep` — because `deep`
"changed" both disliked cases, even though the default already changed them too (a scoring
change from earlier work), and the only real difference was one extra link hop.

That is the reward-hacking failure mode this document warns about, produced by the tool meant
to detect it. The fix is now a rule: **a candidate is only credited with a change the default
did not already make**, and a regression against the default is disqualifying. It is worth
noting that the human reviewing the first proposal would have had to catch it by reading the
per-case detail — which is exactly why the proposal prints that detail instead of a score.

## Failure modes and the countermeasures in place

| Failure | Why it happens | Countermeasure |
| --- | --- | --- |
| **Reward hacking** | optimisation targets a proxy (`more citations`, `cleaner lint`) instead of usefulness | replay reports *retention of liked citations* and *which* cases changed, never a single score; promotion needs a human |
| **Drift** | prompts and skills get rewritten repeatedly until they are unusable | strategy set is code with tests; proposals are files under review; rollback is `git revert` |
| **Cost explosion** | agentic retrieval with N tool calls per question | the default path loads no model; `planned` adds exactly one call; hops and neighbour counts are bounded per strategy |
| **Non-reproducibility** | a full agentic path makes regressions hard to attribute | the deterministic core stays the default and is covered by the test suite; every response reports `strategy/hops/vectors/planned` |
| **Provenance dilution** | automatic ingestion of web material into the wiki | `full_text` / `excerpt` / `link_only` enforced in code, source metadata required, complete third-party text never leaves the runtime directory |
| **Silent improvement** | "the model got better" with nothing recorded | every strategy change must cite a replay comparison in the proposal |

## Deliberately not built

- **An agent inside the backend.** It would duplicate what the calling Agent already does
  better, contradict the MCP contract in `AGENTS.md` (`query_wiki` forces
  `llm.enabled=false`), and push cost, drift and non-reproducibility into the layer that is
  hardest to roll back.
- **Automatic promotion.** Even a small parameter change ships without a human only if the
  replayed evidence keeps every liked citation; that is a policy decision, not a default.
- **A review queue service.** Proposals as Markdown files plus git review cover the need at
  this size. A queue is warranted when several people triage changes in parallel.
- **Learning from clicks.** Opening a citation is not an endorsement; only an explicit rating
  is treated as signal.

## Extending the loop

The natural next steps, in the order the evidence would justify them:

1. **More signal per question**: let the rating carry which citation was wrong, so replay can
   score page-level retention instead of set retention.
2. **A frozen evaluation set**: promote the most-discussed recorded questions into
   hand-labelled cases, which turns the diff tool into a real scorer for those cases.
3. **Knowledge-axis evolution**: drive work from lint and graph gaps into a research step
   that ingests what is missing — the same loop, pointed at content instead of policy.
