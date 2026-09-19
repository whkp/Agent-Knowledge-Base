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
