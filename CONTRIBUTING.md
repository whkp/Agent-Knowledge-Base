# Contributing To AgentKB

Thanks for helping improve AgentKB. The project is still in alpha, so changes that clarify the workspace format, preserve local data, and keep the API and MCP contracts aligned are especially valuable.

## Before Opening A Pull Request

- Read [AGENTS.md](AGENTS.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and the relevant operational notes in `docs/`.
- Keep Markdown workspace files as the durable source of truth.
- Preserve existing API and environment variable compatibility unless a migration is included.
- Add or update focused tests for behavior changes.
- Update documentation when a user-facing workflow changes.
- Update `docs/ARCHITECTURE.md` when a change affects data ownership, query behavior, model configuration, credential handling, API contracts, or MCP boundaries.

## Local Verification

```bash
cd backend
PYTHONPATH=. python -m pytest -q
python -m compileall app

cd ../mcp-server
PYTHONPATH=. python -m pytest -q

cd ../frontend
npm run build
```

Please run `git diff --check` before submitting. Do not commit local databases, Chroma data, virtual environments, `node_modules`, or generated build output.

## Pull Requests

Describe the user-facing behavior, the workspace or API contract affected, and the verification commands you ran. Keep unrelated refactors out of feature pull requests.
