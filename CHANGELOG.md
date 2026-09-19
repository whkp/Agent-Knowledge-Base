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
- Added `TODO.md` as the working plan, and shortened the README roadmap to point at it.
- Reworked page retrieval: CJK bigram query terms, BM25 scoring with length normalisation, and adaptive link expansion that only spends a second hop when the direct matches are weak.
- Added named retrieval strategies (`auto`, `local`, `deep`, `hybrid`, `planned`) that can be chosen per query from the API, the workbench, or MCP, and are reported back in the response and stored with feedback. Page-level vector recall is opt-in and falls back to lexical ranking when embeddings are unavailable.
- Added `backend/scripts/replay_queries.py`, which replays recorded feedback against every strategy to show which citations survive and what each strategy costs.
- Added per-answer feedback (thumbs up / thumbs down with an optional reason) as the signal future retrieval evaluation and strategy evolution will read. Ratings live in SQLite and are never written into the Markdown workspace.
