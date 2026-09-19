# Retrieval and strategies

How AgentKB turns a question into evidence, which knobs exist, and how to change them
without guessing. Numbers quoted here were measured on a local workspace of 25 pages with
the default multilingual embedding model; treat them as calibration, not constants.

## The two retrieval paths

| Path | Endpoint | Searches | Needs embeddings | Needs a model |
| --- | --- | --- | --- | --- |
| Page-first | `POST /api/knowledge-bases/{id}/wiki/query` | `wiki/` Markdown pages | only for the `hybrid`/`planned` strategies | only for answer synthesis and `planned` |
| Raw-source RAG | `POST /api/search` | `document_chunks` in Chroma | yes | only for answer synthesis |

They answer different questions. Page-first returns *maintained knowledge*; raw-source RAG
returns *what a source actually said*. The workbench exposes both; MCP exposes the
page-first path as `query_wiki` and the chunk path as `search_knowledge_base`.

## Page-first, step by step

1. **Read the workspace.** Every readable page under `wiki/` is loaded, with title, summary,
   body, and modification time.
2. **Tokenise the question** (`_search_tokens`):
   - Latin words stay whole and lowercased (`RAG`, `MLCC`).
   - A run of CJK characters becomes overlapping bigrams: `如何配置资产` → `如何 / 何配 / 配置 / 置资 / 资产`.
   - Repeated terms collapse; a single CJK character stays as itself.
3. **Score with BM25** (`_lexical_scores`), saturated term frequency and length
   normalisation, then divided by the score a page would get if every query term appeared
   in it — so the result is a 0..1 "coverage" number rather than an unbounded sum.
4. **Blend in vectors** when the strategy asks for them: `relevance = min(1, lexical + similarity × WIKI_QUERY_VECTOR_WEIGHT)`,
   where a page only counts as a semantic hit when its cosine similarity clears
   `WIKI_QUERY_VECTOR_FLOOR`.
5. **Final page score**: `0.15 + 0.55 × relevance + 0.30 × title coverage`, capped at 0.99.
6. **Follow links** from the direct matches, up to the strategy's hop count and neighbour
   limit. Each hop halves the inherited confidence. Every page added this way is marked
   `related: true` and sorted after the direct matches.
7. **Answer.** A deterministic template answers from the summaries. If a model is
   configured, the same evidence is sent as numbered sources and the answer gains inline
   `[n]` citations that resolve to page paths.
8. **Optionally crystallise** into `wiki/queries/<slug>.md` when the caller passes `save_as`.
   Crystallised pages and the deterministic answer use direct matches only.

### Why bigrams, and what they do not fix

Substring matching meant `如何配置资产` found **nothing** in a workspace full of pages about
`资产配置` — the words differ only in order and one particle. Bigrams fixed exactly that:

| Question | Before | After |
| --- | --- | --- |
| 资产配置 | 4 hits | 6 hits |
| 如何配置资产 | **0 hits** | 6 hits |
| 检索的策略 | **0 hits** | 2 hits |
| 怎么分散风险 | **0 hits** | 2 direct + 2 linked |
| 小孩子 (nothing about it in this workspace) | 0 hits | 0 hits — correct |

Bigrams are not a synonym model. `小孩子` will never find `闰土`; only the vector side can
do that, and only when the workspace actually holds related material.

### Why BM25 and not term counts

Term counts grow with the length of the question and with the length of the page, so the
longest page wins and short questions cannot discriminate. With counts, four pages about
asset allocation all scored 0.99 and the order collapsed to alphabetical. With BM25:

```text
0.9254  wiki/topics/四个木-2024-年度资产配置总结.md
0.9097  wiki/sources/10-四个木-2024-年度资产配置总结.md
0.8903  wiki/topics/小苏-想退休-资产配置入门.md
0.8668  wiki/sources/11-小苏-想退休-资产配置入门.md
0.3678  wiki/sources/5-ramit-sethi-净资产-流动性与现金流.md
```

Topic pages now rank above the raw source pages they were synthesised from, which is the
intended direction: the maintained page is the better answer.

### Why the vector floor exists

Embeddings are dense: every page has *some* positive similarity to every question. Without
a floor, `hybrid` returned the five least-unrelated pages for any question, which looks like
recall and is noise — and it hides the honest answer "this workspace cannot answer that".
Measured on the default model:

| Question | Best match | Median page | Conclusion |
| --- | --- | --- | --- |
| 资产配置 | 0.675 | 0.182 | real matches live above 0.5 |
| 小孩子 | 0.201 | 0.141 | unrelated pages sit in the 0.1–0.2 band |

Hence `WIKI_QUERY_VECTOR_FLOOR=0.35`. With it, `小孩子` returns 0 hits and the deterministic
answer says so, while `退休后怎么安排钱` improves from 2 hits / 0.61 to 4 hits / 0.74.

## Strategies

The **set** of strategies is code. The **choice** is data: it travels in the request, comes
back in the response, is recorded on feedback, and is what the replay tool compares.

| id | Hops | Neighbour limit | Vectors | Model rewriting | Use when |
| --- | --- | --- | --- | --- | --- |
| `auto` (default) | adaptive | 3 | no | no | normal workbench use; no model, no embeddings |
| `local` | 1 | 0 | no | no | ablation baseline: what do the direct matches alone say? |
| `deep` | 2 | 6 | no | no | an agent exploring, willing to trade precision for recall |
| `hybrid` | adaptive | 3 | yes | no | questions phrased differently from the pages |
| `planned` | adaptive | 3 | yes | yes | the caller has a model and the question is vague |

`auto`'s adaptive rule: follow a second hop only when the best direct score is below
`WIKI_QUERY_DEEP_THRESHOLD` (default 0.5). A confident query answers from what it found
instead of wandering; a weak one spends one more hop rather than answering from thin evidence.

### Seeing what actually ran

Every response reports what happened, not what was asked for:

| Field | Meaning |
| --- | --- |
| `strategy` | the strategy id that ran |
| `hops` | link hops actually followed |
| `vectors` | whether page vectors contributed (false when the floor rejected everything or embeddings were unavailable) |
| `planned` | whether the model supplied extra search terms |

These four fields are what make feedback analysable: a 👍 or 👎 without them says nothing
about *which* configuration was being judged.

### Choosing from the workbench

The query dock has a strategy picker. Whatever it is set to is sent with the question and
recorded with the rating, so a person can try `hybrid` on a question that `auto` fumbled and
have that comparison land in the feedback table.

### Choosing from MCP

```
list_retrieval_strategies()                     # ids, labels, trade-offs
search_with_strategy(kb_id, query, "hybrid")    # retrieval only, never the Backend model
```

MCP retrieval tools still force `llm.enabled=false`: the calling Agent does the reasoning,
AgentKB supplies the evidence.

## Adding a strategy

A strategy is code on purpose — this is the gate that stops a model from quietly rewriting
how retrieval works.

1. Add a `RetrievalStrategy(...)` entry in `backend/app/services/retrieval_strategy.py`.
   `hops` is an int or `"auto"`; `neighbour_limit` caps linked pages; `use_vectors` and
   `plan_query` switch the two optional stages on.
2. If it needs a new behaviour (not just different numbers), implement the flag in
   `query_wiki` and keep it deterministic: the model may only supply search terms, never
   rankings.
3. Add a test that shows the new strategy differs from the existing ones in the intended way.
4. Run `backend/scripts/replay_queries.py --strategies <old>,<new>` against recorded
   feedback before changing any default. A strategy that loses citations people liked is a
   regression, whatever its aggregate numbers look like.

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `WIKI_QUERY_DEFAULT_STRATEGY` | `auto` | strategy used when a request names none |
| `WIKI_QUERY_NEIGHBOUR_LIMIT` | `3` | `auto`'s cap on linked pages (0 disables expansion) |
| `WIKI_QUERY_DEEP_THRESHOLD` | `0.5` | below this score `auto` may take a second hop |
| `WIKI_QUERY_VECTOR_WEIGHT` | `0.35` | how much similarity may add on top of lexical score |
| `WIKI_QUERY_VECTOR_FLOOR` | `0.35` | minimum cosine similarity to count as a semantic hit |

Changing any of these changes retrieval quality, so change them the same way as a strategy:
with a replay comparison attached. See [SELF_EVOLUTION.md](SELF_EVOLUTION.md).

## Costs and failure behaviour

- **Default path loads no model.** `auto` and `local` never touch embeddings, so a fresh
  install answers without downloading anything.
- **Embeddings are cached per workspace** and rebuilt only when a page's file timestamp
  moves. First use pays the model load; later queries pay a dot product.
- **Everything degrades.** No embeddings → `hybrid` behaves like `auto` and reports
  `vectors=false`. No model → `planned` searches with the original question and reports
  `planned=false`. Neither raises.
- **Scoring is linear in the number of pages** and BM25 needs the whole corpus in memory,
  which is fine at the scale this project targets (tens to low hundreds of pages) and is
  exactly why the index file, not a service, is the navigation layer.

## Known limits

- No synonym handling without embeddings, and no cross-lingual matching without a
  multilingual model.
- No learned ranking, no click-through signal; the only signal is the explicit 👍/👎.
- One hop of expansion is cheap; two is the current ceiling. Deeper traversal has not been
  shown to help more than better recall at hop one.
- The raw-source path is not covered by the replay tool: replaying it needs the vector store
  and a different citation format (`document:<id>#<chunk>`).
