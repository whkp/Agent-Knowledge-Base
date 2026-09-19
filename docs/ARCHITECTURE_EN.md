# AgentKB Architecture

This document describes the architecture, data boundaries and interface contracts of the
current AgentKB implementation. It is the reference for how the project is built;
[llm-wiki.md](../llm-wiki.md) remains the product methodology and a source for long-term
direction, and does not override the concrete contracts stated here.

[中文](ARCHITECTURE.md) · **English**

## 1. Goals and boundaries

AgentKB is a local-first Markdown knowledge base for AI agents. It does not treat a vector
store or a chat transcript as the long-lived source of truth; the reviewable, editable,
Git-managed Markdown workspace is the durable carrier of knowledge.

The current version offers two query entry points:

- **Page-first**: rank and answer from the maintained wiki pages. Best for topics,
  conclusions and relationships that have accumulated over time.
- **Raw-source RAG**: vector retrieval over the document chunks saved at ingest time. Best
  for re-reading original material and supporting evidence.

Both entry points in the web workbench can optionally use an OpenAI-compatible model to
synthesise an evidence-bound answer. MCP by default supplies retrieval evidence *without*
model generation, leaving task reasoning and the final answer to the calling agent; the
Backend model is only requested when an explicit MCP synthesis tool is invoked. The model is
never a precondition for availability: it is off by default, and when there is no
configuration, the call fails, or there is no evidence, the system still returns the local
deterministic page answer or the raw retrieval results.

## 2. Core principles

1. **The Markdown workspace is the durable source of truth.** `raw/` keeps immutable source
   snapshots and `wiki/` keeps maintained pages. SQLite, ChromaDB and model output cannot
   replace it.
2. **Derived data must be rebuildable.** `index.md`, the link graph, the vector index and
   query results are all derived from Markdown, from SQLite document records, or from both.
3. **A raw source cannot be overwritten by a wiki summary.** Ingest, summarisation and model
   synthesis may only add to or maintain the wiki layer; they must never rewrite
   `raw/sources/`. Complete third-party text is written to SQLite and the raw snapshot first,
   and chunks and vectors are produced from that.
4. **Model answers must be bound to retrieval evidence.** The model context contains only the
   pages or fragments matching the current query, and the prompt requires factual claims to
   carry source markers such as `[1]`, `[2]`.
5. **Model failure must be visible and reversible.** Responses report through `answer_mode`
   and `model_error` whether a model answer was actually obtained, and why not.
6. **The default deployment assumes a trusted local workstation.** The model configuration
   API has no authentication; a public deployment must add identity, access control and HTTPS
   at the deployment layer.

## 3. System structure

```text
                          +--------------------------+
                          | AgentKB Workbench         |
                          | React / Vite              |
                          +------------+-------------+
                                       | HTTP
                                       v
                          +--------------------------+
                          | AgentKB API               |
                          | FastAPI                   |
                          +---+-----------+------+---+
                              |           |      |
                   +----------+           |      +--------------------+
                   v                      v                           v
          +----------------+     +----------------+       +--------------------------+
          | Markdown Wiki  |     | SQLite         |       | ChromaDB + embeddings    |
          | durable truth  |     | app metadata   |       | optional retrieval index |
          +----------------+     +----------------+       +--------------------------+
                   |                                                    |
                   +--------------------+-------------------------------+
                                        | evidence
                                        v
                          +--------------------------+
                          | OpenAI-compatible LLM     |
                          | optional synthesis only   |
                          +--------------------------+

AI agents / MCP clients -> AgentKB MCP -> the same AgentKB API
```

### 3.1 Responsibility split

| Component | Owns | Does not own |
| --- | --- | --- |
| Backend | knowledge bases, source ingest, wiki file maintenance, retrieval, model synthesis, configuration state | duplicating business rules in the Frontend or MCP |
| Markdown workspace | reviewable source relationships, pages, index, log | API keys, runtime configuration, vector data |
| SQLite | application metadata: knowledge bases, documents, chunks, vector identifiers | replacing the wiki as knowledge text |
| ChromaDB | similarity recall over document chunk embeddings | being the only knowledge source or a long-term fact store |
| Frontend | browsing, editing, ingest, querying, model settings | storing API keys or reimplementing retrieval |
| MCP Server | exposing Backend HTTP workflows as agent tools; forcing retrieval mode for base queries; requesting Backend synthesis on demand | reading or writing the wiki directly, calling a model provider, or finishing the external agent's reasoning |

## 4. Knowledge workspace

Every knowledge base lives at `WIKI_ROOT_DIR/kb-<id>/` with this layout:

```text
kb-<id>/
├── .wiki-schema.md      # page and maintenance conventions
├── purpose.md           # knowledge base goals and key questions
├── raw/
│   └── sources/         # immutable Markdown source snapshots
├── wiki/
│   ├── overview.md      # entry page
│   ├── sources/         # source summary pages
│   ├── topics/          # continuously maintained topic pages
│   ├── entities/        # reserved for entity pages
│   └── queries/         # query crystallizations the user chose to save
├── index.md             # navigation index generated from the pages
└── log.md               # append-only activity log
```

Pages link to each other with `[[path/without-extension]]`. The link graph, inbound counts,
orphan detection and broken-link checks are all derived from page content; editing the graph
alone cannot change a factual relationship.

### 4.1 Ingest flow

```text
text, .txt, or a local external source snapshot
  -> documents / document_chunks (SQLite)
  -> external snapshot metadata + content_hash (SQLite)
  -> raw/sources/ immutable snapshot
  -> wiki/sources/ source page
  -> wiki/topics/ topic page update
  -> index.md rebuild + log.md append
  -> optional: embedding + ChromaDB chunk index
```

Ingest currently runs in two steps. The first is deterministic and happens inside the
database transaction: it writes the immutable `raw/sources/` snapshot, the
`wiki/sources/` page, one deterministic topic page, appends the source to that page's
`## Sources` list, then rebuilds `index.md` and appends to `log.md`. The second runs
best-effort after the transaction commits:

- With no model configured, nothing extra happens: step one is the final result.
- With a model configured, the model chooses a destination among at most five candidate
  topic pages (or asks for a new one) and rewrites that page's `## Evolving synthesis`. It may
  only return a slug from the candidates or `NEW`; anything else is treated as a new page, so
  the model cannot create or rename files.
- When the model chooses a different destination than step one did, the source link moves to
  the target page. A page left with no sources at all is deleted rather than kept as an empty
  shell.
- A model failure, timeout or exception only writes a log line (`Skipped:` /
  `Skipped after an error:`) and never rolls back the committed ingest. That is also why step
  two sits outside the transaction: an earlier implementation waited for the model inside an
  open transaction, blocking for up to `LLM_TIMEOUT_SECONDS`.

The `sources:` front matter and the `## Sources` list are always maintained by code; the
model only writes the `## Evolving synthesis` body, and the `summary` field is derived from
that body's first line.

Routing without a model is deliberately conservative: a source joins an existing topic page
only when the titles are identical, or when **two** independent words in the new source's
title match the topic page title. Sharing a single generic word (`AI`, `ETF`) never merges.
So "AI 制药进展" is not merged into "AI 服务器 MLCC 产能挤出效应".

Turning model-generated maintenance suggestions into reviewable page changes remains future
work.

### 4.2 External source snapshot contract

`POST /api/knowledge-bases/{knowledge_base_id}/documents/source-snapshot` brings an
externally obtained source into the knowledge base. The Backend does not simulate logins or
scrape X / XiaoHongShu: that keeps the service deployable and testable, and keeps platform
session state out of the application process.

A snapshot keeps the complete `content` in the `documents` table and records `content_hash`,
`source_url`, `source_platform`, author, account, publication time, capture time, disclosures
and retention policy. `source_policy` has three values:

| Policy | Meaning | Suitable for full-text RAG |
| --- | --- | --- |
| `full_text` | the snapshot contains the captured complete text; replies, quotes and comments are not included automatically | yes |
| `excerpt` | only text explicitly marked as an excerpt is kept | yes, but the model and the user must know the content is incomplete |
| `link_only` | only a link and a human note are kept; no claim to hold the text | no; the note must not be treated as the post body |

`PUT /api/knowledge-bases/{knowledge_base_id}/documents/{document_id}` is the matching
update path: it replaces the content in place (optionally refreshing source metadata) and
keeps the document id, the raw snapshot path, the source page path and the topic page's
source row. Without it, correcting a source means deleting and re-ingesting, which produces
a new document id, a new source page and a second row in `## Sources`. Fields the caller
does not send keep their current value, so a text correction cannot silently drop captured
provenance. The update runs in one transaction: new vectors are written before the stale
ones are dropped, vector ids carry the content hash (SQLite reuses row ids, so an id derived
from the row id alone would make the next step delete the vectors just written), and
re-indexing is skipped when the content did not change. Model-backed topic maintenance still
runs after the commit.

Within one knowledge base, **identical content from the same source** returns
`409 Conflict`, and the message names the existing document id and the update endpoint to
use instead. Source identity is part of that match: the same URL (or the same file name, or
the same title) is what makes it a re-capture. Identical text from two different sources is
two sources, and collapsing them would discard provenance.

SQLite is the application query and consistency index, `raw/sources/` is the reviewable
Markdown snapshot, and Chroma is a deletable, rebuildable chunk index. Chroma metadata also
records source URL, platform, author, policy and content hash, so a RAG hit can be traced
back to its source; the vector store can never be the only source of truth.

The repository ships `backend/scripts/import_source_snapshots.py`, which accepts a JSON array
or JSONL. It only reads local files or stdin and does not depend on OpenCLI; external capture
tools are run separately by the maintainer in a controlled environment. Complete third-party
text is written only to the Git-ignored runtime directory and never enters the repository.

## 5. Query and answering

### 5.1 Page-first

`POST /api/knowledge-bases/{knowledge_base_id}/wiki/query`

1. Read the queryable pages under `wiki/`.
2. Split the question into search terms: Latin words stay whole, and a run of CJK characters
   becomes **overlapping bigrams** (`如何配置资产` → `如何/何配/配置/置资/资产`). This handles word
   order and particles, not synonyms — synonyms need the vector side.
3. Score candidate pages with **BM25**: saturated term frequency plus length normalisation,
   divided by the score a page would get if every query term appeared in it, mapped to 0..1.
   Plain term counts would let the longest page always win.
4. `score = 0.15 + 0.55 × lexical relevance + 0.30 × title coverage`, capped at 0.99.
5. Expand along page links: outlinks and backlinks of the matching pages join the results as
   supporting evidence, ordered by how many matching pages reach them, marked
   `related=true`. Hop count and the neighbour cap come from the **retrieval strategy**
   (section 5.6). Direct matches always sort first, and the deterministic answer and any
   saved `wiki/queries/` page use direct matches only.
6. Produce the page list and the deterministic answer.
7. If a model is enabled and there are matching pages, the ranked full page contents are
   assembled into evidence under the context limit and sent to OpenAI-compatible Chat
   Completions. Expanded pages are labelled as linked pages in the evidence, and the body of
   `purpose.md` is added to the system prompt as workspace intent, never as a citation.
8. If the caller passes `save_as`, the final answer and its cited pages are saved under
   `wiki/queries/`. Whether the answer came from the model or from local rules, the source
   list is preserved.

Response contract:

- `answer_mode="llm"`: `answer` is the model's synthesis over the page evidence, and `model`
  names the model actually used.
- `answer_mode="deterministic"`: `answer` is the local ranking plus the template answer.
- `model_error` non-empty: the model could not produce an answer; the local answer is still
  valid.
- `results[].related=true`: the page was not a direct match but was reached by following a
  link from a matching page.

### 5.2 Raw-source RAG

`POST /api/search`

1. Generate the query embedding with
   `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
2. Recall similar chunks from ChromaDB filtered by `knowledge_base_id`.
3. Return documents, chunks, similarity scores, retrieval order and external source metadata.
4. If a model is enabled and there are results, the chunks are handed to the model in that
   order as evidence.

Response contract:

- `answer_mode="llm"`: both `answer` and the full `results` are returned; the Nth result
  corresponds to the model's `[N]` citation.
- `answer_mode="retrieval"`: only raw retrieval results are returned, and `answer` is `null`.
- `model_error` non-empty: the model failed, but `results` remain usable for human review.

`POST /api/search/stream` is a legacy-compatible endpoint that still emits retrieval events
only (`start`, `delta`, `result`, `error`, `done`) and provides no model token stream. A
streaming model response needs its own event contract before it is implemented.

### 5.3 Evidence and citations

The system prompt given to the model requires it to:

- answer in the language of the user's question,
- state facts only from the page or chunk evidence injected for this request,
- say plainly when the evidence is insufficient,
- cite using markers such as `[1]`, `[2]`.

AgentKB cannot technically guarantee that any provider never hallucinates, so citations are
an aid to traceability, not a substitute for factual correctness. The frontend shows the pages
or raw fragments in the same order as the numbering, and for external snapshots it also shows
platform, author, retention policy and a clickable link to the original post; the user should
still open the source to verify key conclusions.

### 5.4 MCP query strategy

The goal of MCP is to let external agents such as Codex and Claude Code reach traceable
knowledge, not to chain a second generative model into every tool call. To avoid extra
latency, cost, lost information and double generation, the tools fall into two groups:

| MCP tool | Backend request | Model behaviour | Typical use |
| --- | --- | --- | --- |
| `query_wiki` | `POST .../wiki/query` + `llm.enabled=false` | never invokes the AgentKB model; returns the deterministic page answer and ranked pages | an agent wants long-term maintained Markdown knowledge |
| `search_knowledge_base` | `POST /api/search` + `llm.enabled=false` | never invokes the AgentKB model; returns raw chunks and scores | an agent wants original source evidence |
| `synthesize_knowledge` | same endpoints, chosen by `source_mode`, with `llm.enabled=true` | explicitly tries the Backend's configured model; on failure still returns local fallback results and `model_error` | the caller explicitly delegates a bounded evidence synthesis to AgentKB |

`synthesize_knowledge` accepts no provider URL, model name or API key. It uses only the
Backend's effective configuration and still follows the precedence and key boundaries in
section 6. A calling agent should prefer the base query tools and assemble evidence itself;
the explicit synthesis tool is for tasks that need a separate, citable knowledge-base answer.

### 5.5 Answer feedback

`POST /api/knowledge-bases/{knowledge_base_id}/feedback`

Under every answer the workbench offers "useful" or "not useful"; choosing "not useful"
expands an optional reason box. This is the **only evaluation signal for retrieval
strategies**, so each record keeps enough information to replay the query later:

| Field | Purpose |
| --- | --- |
| `mode` | `wiki` (page-first) or `rag` (raw source) |
| `query` / `answer` | the question that was judged and the answer at the time (the answer is truncated when stored, to make failed cases reviewable) |
| `rating` | accepts only `1` or `-1` |
| `note` | optional reason for a thumbs down |
| `answer_mode` / `model` | whether this answer was a deterministic template or model synthesis, and which model |
| `strategy_id` | which retrieval strategy produced this answer, so replays can compare |
| `source_paths` | the cited page paths shown at the time, or `document:<id>#<chunk>` fragments |
| `bad_paths` | on a thumbs down, **which of those citations were wrong** (a subset of `source_paths`); sending it with a thumbs up is rejected rather than silently dropped |

Storage and boundaries:

- Feedback is **business data** written to SQLite's `query_feedback` table. It is not
  knowledge and **must never be written into wiki pages or the Markdown workspace**, and it
  never changes the answer it rates.
- Records are append-only: a change of mind creates a new row instead of overwriting the old
  one, so the change of judgement is itself analysable data.
- `GET .../feedback` reads the signal back and returns `positive` / `negative` totals. The
  workbench writes ratings with the strategy that produced the answer, and MCP exposes the
  read side as `list_answer_feedback`; see sections 5.7 and 5.8 for what consumes it.
- The workbench has two surfaces for this: under the answer (useful / not useful, an optional
  reason, and a "which citation was wrong" picker) and a **feedback sheet** (ratings in reverse
  chronological order, 👍/👎 totals, the flagged citations, and a way to put the question back
  into the composer and re-run it). Neither writes to the wiki, and the feedback sheet is
  read-only.
- `bad_paths` moves replay from a **set-level** judgement ("is the evidence still retrievable")
  to a **page-level** one ("is the citation that person called wrong still retrievable"). It is
  still not a gold label: "hid a flagged citation" must never be phrased as "got it right".

### 5.6 Retrieval strategies

The **set** of retrieval behaviours is code; the **choice** is data:

| Strategy | Hops | Neighbour limit | Vectors | Model rewriting |
| --- | --- | --- | --- | --- |
| `auto` (default) | adaptive | 3 | no | no |
| `local` | 1 | 0 (no expansion) | no | no |
| `deep` | 2 | 6 | no | no |
| `hybrid` | adaptive | 3 | yes | no |
| `planned` | adaptive | 3 | yes | yes |

- `auto`'s adaptive rule: a second hop is allowed when the best direct match scores below
  `WIKI_QUERY_DEEP_THRESHOLD` (default 0.5), otherwise only one hop is followed. Each hop
  halves the inherited confidence, so a page two hops away never outranks one hop away.
- `GET /api/retrieval-strategies` lists the strategies and their trade-offs, read-only.
  Adding a strategy requires a code change with review and tests — that is the gate that
  stops a model from quietly changing how retrieval works.
- The `strategy` field of `POST .../wiki/query` selects one; omitted means
  `WIKI_QUERY_DEFAULT_STRATEGY` (default `auto`). An unknown strategy returns 400 listing the
  available ids.
- Responses report **what actually happened**: `strategy`, `hops`, `vectors`, `planned`.
  These four fields are stored with feedback, so which configuration produced which answer
  stays traceable.
- The vector side has a similarity floor, `WIKI_QUERY_VECTOR_FLOOR` (default 0.35). Without
  it every question drags in the least-unrelated pages, which looks like recall but is noise,
  and it hides the fact that the workspace cannot answer the question.
- Vectors are cached per workspace and rebuilt when a page's file timestamp changes; when
  embeddings are unavailable the system falls back to lexical ranking and honestly reports
  `vectors=false`.
- `planned` only lets the model rewrite the question into search terms (one call, a single
  `KEYWORDS:` line); ranking stays fully deterministic. When the model is off or fails it
  falls back to the original question and reports `planned=false`.

### 5.7 Replay and evaluation

`backend/scripts/replay_queries.py` replays recorded feedback against every strategy:

- For 👍 cases: does the strategy still retrieve the pages that answer cited? Losing them is
  a regression.
- For 👎 cases: did the strategy actually change the leading evidence? Returning the same
  wrong answer is not an improvement.
- Cost: average linked pages and average hops.
- Replays pass `record_activity=False`, so replaying repeatedly never floods `log.md`.

It **compares, it does not score**: with no labelled answers, any conclusion about "which
strategy is better" must be drawn by a human (or an external agent) reading the differences.
`--json` output is meant for agent consumption.

### 5.8 Proposals and promotion

`backend/scripts/propose_strategy_change.py` turns replay evidence into a reviewable Markdown
proposal under the repository's `proposals/` directory:

- Recommendations are judged **relative to the current default**: a candidate is only
  proposed when it loses none of the liked citations *and* changes leading evidence in a 👎
  case that the default did not already change. Comparing a candidate against the *recorded*
  results would credit it for changes the default already made.
- `--check` is for CI and pre-commit use: it replays only the current default and exits 1 as
  soon as that default drops a citation someone liked.
- Promotion is an ordinary git change (edit `wiki_query_default_strategy` or a strategy
  definition, plus tests), so the audit trail and the rollback mechanism are `git log` and
  `git revert`.

The reasoning is in [SELF_EVOLUTION.md](SELF_EVOLUTION.md); the retrieval mechanics are in
[RETRIEVAL.md](RETRIEVAL.md).

## 6. Model configuration

### 6.1 Sources and precedence

Model configuration is Backend runtime configuration, not a per-knowledge-base property. The
effective configuration resolves in this order:

```text
per-request llm override
  > the current Backend process's web runtime configuration
  > environment variables or backend/.env
  > code defaults
```

Deployment configuration uses these environment variables:

| Variable | Meaning | Default |
| --- | --- | --- |
| `LLM_ENABLED` | whether to attempt model synthesis | `false` |
| `LLM_BASE_URL` | OpenAI-compatible API root, without `/chat/completions` | `https://api.openai.com/v1` |
| `LLM_API_KEY` | provider key | empty |
| `LLM_MODEL` | chat completions model name | empty |
| `LLM_TEMPERATURE` | sampling temperature, 0 to 2 | `0.2` |
| `LLM_MAX_TOKENS` | maximum output tokens | `1200` |
| `LLM_TIMEOUT_SECONDS` | provider request timeout in seconds | `60` |
| `LLM_CONTEXT_MAX_CHARS` | maximum characters of retrieval evidence injected into the model | `12000` |

### 6.2 Web runtime configuration

The workbench's model settings call:

| API | Purpose | Key behaviour |
| --- | --- | --- |
| `GET /api/llm/config` | read the effective non-sensitive state | returns only `api_key_configured`, never the key |
| `PUT /api/llm/config` | replace the current Backend process's runtime configuration | the key stays in that Backend process's memory |
| `POST /api/llm/test` | overlay the submitted fields on the current effective configuration and verify the connection | does not change the configuration in effect |

The web UI never writes an API key to `localStorage`, Markdown or SQLite. A process restart
clears the runtime configuration and the system reads `.env` or environment variables again.
Because saving is process-wide, other local clients of the same Backend share it; a
multi-user or public deployment must move to an authenticated per-user or per-workspace
configuration model.

### 6.3 OpenAI-compatible contract

The Backend issues:

```http
POST {LLM_BASE_URL}/chat/completions
Authorization: Bearer {LLM_API_KEY}
Content-Type: application/json
```

The request body carries `model`, `messages`, `temperature`, `max_tokens` and
`stream: false`. The current implementation reads the standard
`choices[0].message.content` response field. Provider timeouts, network errors, HTTP 4xx/5xx,
empty answers and incompatible formats all become a local retrieval fallback instead of
failing the whole query.

## 7. External API contract

| Area | Main endpoints | Notes |
| --- | --- | --- |
| Knowledge bases | `/api/knowledge-bases` | CRUD; creation initialises the Markdown workspace |
| Sources | `/api/knowledge-bases/{id}/documents/text`, `/file`, `/source-snapshot` | ingest text, `.txt` or a local external source snapshot, maintaining the workspace and the optional vector index |
| Source update | `PUT /api/knowledge-bases/{id}/documents/{document_id}` | replace content in place, keeping the id and the page paths; identical content from the same source is a 409 on ingest |
| Pages | `/api/knowledge-bases/{id}/wiki/pages` | browse, read and save Markdown pages |
| Wiki query | `/api/knowledge-bases/{id}/wiki/query` | page ranking, optional model synthesis, optional save as a query page |
| RAG query | `/api/search` | vector recall, optional model synthesis, always returns retrieval evidence |
| Model settings | `/api/llm/config`, `/api/llm/test` | effective state, process-level settings and connection verification |
| MCP | `mcp-server/tools.py` | base queries explicitly disable the model; only `synthesize_knowledge` explicitly enables Backend synthesis |

Adding response fields must stay compatible with local mode: `SearchResponse.answer`,
`model`, and `model_error` are all allowed to be empty, and an older client that reads only
`results` keeps working.

## 8. Failure handling and observability

- Source, knowledge base or vector retrieval failures: the HTTP API returns an explicit
  4xx/5xx error.
- Duplicate external imports: the local import script skips identical snapshots by URL plus
  `content_hash`; changed text produces a new document record, preserving version
  traceability.
- Model disabled: no provider request is made, and local results are returned normally.
- Missing model configuration, timeout, connection failure, HTTP error or malformed
  response: the query still succeeds, carries `model_error`, and falls back to local results.
- Wiki lint: checks broken links, orphan pages and missing summaries; the result is written
  into the API response and the activity into `log.md`.
- Queries and ingests write to `log.md`, but the log must never contain API keys or model
  request bodies.

## 9. Current non-goals and next directions

Not included today:

- Model-generated token streaming, incremental answer events or a cancellation protocol.
- Automatic multi-page wiki modification by a model, with diff preview, approval and rollback
  workflows.
- Multi-user provider key management, permission models, auditing and tenant isolation.
- PDF, DOCX, URL and image source adapters.
- Native provider SDK adapters and a model catalogue.

Delivered ahead of this list and documented above: named retrieval strategies with adaptive
link expansion and optional vector recall (5.6), recorded answer feedback as the evaluation
signal (5.5), and the replay-plus-proposal loop that turns that signal into a reviewed change
(5.7, 5.8).

Recommended order for what remains: define the streaming model API event contract first,
without letting the legacy SSE path swallow model answers; then add reviewable wiki change
proposals; then extend retrieval, source adapters and multi-user deployment.

## 10. Change requirements

Changes touching any of the following must update this document and the corresponding tests:

- Workspace directories, page format or Markdown link rules.
- Query response fields, citation ordering, fallback behaviour or SSE events.
- Model provider request format, configuration precedence, key handling or deployment
  assumptions.
- The contract between MCP and the Backend API, including base tools disabling the model and
  synthesis requiring an explicit call.

Before committing, at minimum run:

```bash
cd backend && PYTHONPATH=. python -m pytest -q
cd ../mcp-server && PYTHONPATH=. python -m pytest -q
cd ../frontend && npm run build
git diff --check
```
