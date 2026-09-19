# Changelog

All notable changes to AgentKB will be documented here.

## Unreleased

- Renamed the public product identity from `kk-knowledge-agent` / `LivingWiki` to `AgentKB`.
- Reframed the repository around a local-first, Markdown-native knowledge base workflow.
- Added open-source contribution, security, conduct, and CI documentation.
- Added optional OpenAI-compatible evidence-bound synthesis for wiki and raw-source RAG queries, with deterministic retrieval fallback.
- Added process-local workbench model configuration and connection testing without persisting API keys in Markdown, SQLite, or the browser.
- Made MCP base retrieval tools model-free by default and added explicit `synthesize_knowledge` for opt-in Backend model synthesis.
- Added `docs/ARCHITECTURE.md` as the current implementation design and contract reference.
- Added `docs/RETRIEVAL.md`, `docs/SELF_EVOLUTION.md` and `skills/agentkb-retrieval`, describing how retrieval works and how recorded feedback turns into a reviewed change.
- Every document under `docs/` now exists in both English and Chinese, with a language switcher and verified parity of numbers, identifiers and structure.
- Added a local working plan (`TODO.md`, kept out of the repository) and shortened the README roadmap to a short inline list.
- Reworked page retrieval: CJK bigram query terms, BM25 scoring with length normalisation, and adaptive link expansion that only spends a second hop when the direct matches are weak.
- Added named retrieval strategies (`auto`, `local`, `deep`, `hybrid`, `planned`) that can be chosen per query from the API, the workbench, or MCP, and are reported back in the response and stored with feedback. Page-level vector recall is opt-in and falls back to lexical ranking when embeddings are unavailable.
- A thumbs down can now name **which citations were wrong** (`bad_paths`), and the workbench offers a "which citation was wrong" picker. Replay and the proposal script judge at page level: a flagged citation is excluded from the "should have been kept" set, and a candidate only counts as an improvement when it hides a flagged citation the default still returns.
- Ingesting the same content from the same source is now a `409 Conflict` naming the existing document and the update endpoint to use, instead of silently creating a second document for one source. Identical text from a *different* source still becomes a second source, because collapsing those would discard provenance.
- Added `PUT /api/knowledge-bases/{id}/documents/{document_id}`, which replaces a document's content in place: the document id, raw snapshot path, source page path and the topic page's source row all stay the same. Omitted fields keep their value, so a text correction cannot drop captured provenance. Vector ids now include the content hash (SQLite reuses row ids, which made re-indexing delete the vectors it had just written), and a metadata-only update no longer re-embeds unchanged content.
- Added the `update_source_in_wiki` MCP tool so an Agent that hits an ingest conflict can resolve it, with `synthesize_topic=false` like every other ingest tool.
- Replaced the page graph's exact O(n²) repulsion pass with a Barnes-Hut quadtree approximation, moved the layout into `src/graph/layout.ts`, and turned the per-edge `find` over every node into a map lookup. A production build now opens an 800-page graph in 315ms instead of 956ms (400 pages: 192ms instead of 322ms), with the same layout quality. `npm run bench:layout` reproduces the layout timings and quality metrics.
- Added a read-only **feedback sheet** to the workbench: ratings in reverse chronological order with totals, the strategy in force and the flagged citations.
- Added `backend/scripts/replay_queries.py`, which replays recorded feedback against every strategy to show which citations survive and what each strategy costs.
- Added per-answer feedback (thumbs up / thumbs down with an optional reason) as the signal future retrieval evaluation and strategy evolution will read. Ratings live in SQLite and are never written into the Markdown workspace.
