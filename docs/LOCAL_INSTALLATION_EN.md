# Local installation guide

[中文](LOCAL_INSTALLATION.md) · **English**

This document installs and runs `AgentKB` on one machine. It covers:

- the local knowledge base service: Backend + Frontend
- the MCP Server, so agents such as Codex and Claude Code can query the knowledge base
- basic verification and common problems

The project does not use Docker yet; Docker Compose stays a possible later improvement.

The workbench is built around the Markdown wiki: every knowledge base gets its own `kb-<id>/` workspace under `WIKI_ROOT_DIR`, holding `raw/`, `wiki/`, `index.md` and `log.md`. SQLite and Chroma remain in place for ordinary documents and the vector search path.

Architecture, query modes, model configuration precedence, key handling and API contracts live in [ARCHITECTURE_EN.md](ARCHITECTURE_EN.md).

## 1. Requirements

Suggested environment:

```text
Python: 3.11 or 3.12
Node.js: 20+
npm: 10+
Git: any recent version
```

Main components used by the project:

```text
Backend: FastAPI + SQLite + SQLAlchemy
Vector DB: ChromaDB
Embedding: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
Frontend: React + Vite + TypeScript
MCP Server: Python MCP SDK
```

The first full backend install downloads PyTorch, ChromaDB, sentence-transformers and the embedding model, so a long wait is normal. Once the model is downloaded it is served from the local cache; if Hugging Face is temporarily unavailable or rate-limits the request, AgentKB tries that cache automatically.

The workspace uses AgentKB storage names: the database is `data/agentkb.db` and the Chroma collection is `agentkb_chunks`. `data/`, `WIKI_ROOT_DIR` and Chroma data are local runtime files and must not be committed; `raw/sources/` and `documents.content` do hold source text that is usable for RAG.

## 2. Clone the project

```bash
git clone https://github.com/kanna12580/agentkb.git
cd agentkb
```

If you are already in a local checkout:

```bash
git pull
```

## 3. Install the local knowledge base Backend

Enter the backend directory:

```bash
cd backend
```

Create a virtual environment:

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Windows CMD:

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

If the official PyPI index is slow, point pip at a local mirror temporarily:

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

Copy the environment file:

Windows:

```powershell
Copy-Item .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

The default `.env.example` is already suitable for local development:

```text
APP_NAME=AgentKB API
DATABASE_URL=sqlite:///./data/agentkb.db
CHROMA_PERSIST_DIR=./chroma
CHROMA_COLLECTION_NAME=agentkb_chunks
EMBEDDING_MODEL_NAME=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
VECTOR_INDEX_ENABLED=true
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

### Optional: configure a model through `.env`

AgentKB does not call a generative model by default. Once enabled, both page-first and raw-source RAG retrieve first and then hand the matched content to the model as a bounded context for an answer citing `[1]`, `[2]`.

The interface follows the OpenAI-compatible Chat Completions protocol: `POST {LLM_BASE_URL}/chat/completions`. OpenAI, a compatible gateway, vLLM, LM Studio, Ollama's OpenAI-compatible endpoint or any other compatible service can be used.

Add this to `backend/.env`:

```dotenv
LLM_ENABLED=true
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your-api-key
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=1200
LLM_TIMEOUT_SECONDS=60
# Maximum characters of retrieval context handed to the model
LLM_CONTEXT_MAX_CHARS=12000
```

`LLM_BASE_URL` should be the API root, usually ending in `/v1`, and must not include `/chat/completions`. A local service might use `http://127.0.0.1:1234/v1`, or whatever compatible API root it actually exposes. Restart the Backend for a configuration change to take effect.

Without `LLM_ENABLED=true` the system keeps using local page ranking and vector recall: no model call happens and no model cost is incurred.

### Import complete external sources

Collecting text from external platforms is decoupled from the server. Use a locally logged-in read-only collection tool to produce JSON/JSONL, then import it into the current knowledge base:

```bash
cd backend
PYTHONPATH=. python scripts/import_source_snapshots.py \
  --knowledge-base-id 1 \
  --input /absolute/path/to/source-snapshots.json
```

The importer accepts a JSON array or JSONL. Example fields:

```json
{
  "title": "来源标题",
  "content": "采集到的完整正文",
  "source_url": "https://x.com/example/status/123",
  "platform": "x",
  "author": "作者显示名",
  "account": "账号",
  "published_at": "2026-08-17T08:00:00Z",
  "source_policy": "full_text",
  "disclosures": ["公开社交内容，仅作研究与检索，不构成投资建议"],
  "tags": ["research"]
}
```

`full_text` is written to SQLite, to `WIKI_ROOT_DIR/kb-<id>/raw/sources/` and to Chroma chunks at the same time; `excerpt` and `link_only` are marked by retention policy, and the latter must not be used as full-text RAG evidence. On repeat runs, records with the same URL and content hash are skipped. Complete third-party text stays in the local runtime directory and does not enter the public repository.

### Temporary configuration in the web UI

The model settings in the top right of the workbench set the service address, model, temperature, output length, timeout and API key without editing files or restarting the Backend, and can test the connection first. The settings take effect through these local APIs:

```text
GET /api/llm/config    # effective configuration state, never the API key
PUT /api/llm/config    # stored only in the current Backend process memory
POST /api/llm/test     # makes one model request with the submitted configuration to verify reachability
```

A key entered in the web UI is used only by the current Backend process: it is never written to Markdown, SQLite or browser storage, and it is lost when the Backend restarts, falling back to `.env`. A deployment exposed to the internet should use HTTPS, authentication and server-side `.env` key management rather than entering keys into a public web page; the project currently assumes a trusted local workstation.

Start the backend:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Visit:

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/health
```

The health check should return:

```json
{"status":"ok"}
```

## 4. Verify the real backend path

The project ships an end-to-end script that uses temporary SQLite and Chroma directories to verify:

- creating a knowledge base
- uploading fragments of two sample texts
- writing real Chroma vectors
- retrieving with the real embedding model
- checking the search ranking

Run it from the repository root:

```bash
python backend/scripts/validate_real_backend.py
```

Default storage identifiers:

```text
Database: data/agentkb.db
Chroma collection: agentkb_chunks
```

To migrate local data from an older version, run this at the repository root:

```bash
python backend/scripts/migrate_storage_names.py
```

Inspect the migration target without applying it first:

```bash
python backend/scripts/migrate_storage_names.py --dry-run
```

Old Chroma data does not rename its collection automatically. Prefer rebuilding the index under the new collection name; if you need to read the old collection temporarily, set `CHROMA_COLLECTION_NAME=livingwiki_chunks` or `CHROMA_COLLECTION_NAME=kk_knowledge_chunks` in `.env`, then switch back to `agentkb_chunks` when the migration is done.

Expected retrieval results:

```text
春天 -> 春
花草 -> 春
少年闰土 -> 故乡
小孩子 -> 故乡
乡下少年 -> 故乡
```

The first run downloads the embedding model, which can be slow.

## 5. Install and start the Frontend

Open a new terminal and enter the frontend directory:

```bash
cd frontend
```

Install dependencies:

```bash
npm install
```

Start the dev server:

```bash
npm run dev -- --host 127.0.0.1 --port 5173
```

Visit:

```text
http://localhost:5173
```

The frontend demo supports:

```text
1. create a knowledge base and its Markdown workspace
2. ingest text or a .txt source, producing the raw snapshot, source page, topic page and index
3. browse, edit and save wiki Markdown pages
4. query the wiki pages and crystallise an answer into `wiki/queries/`
5. run the lint health check and look at the derived graph
6. optionally connect an OpenAI-compatible service in the model settings, query again and inspect the cited synthesis
```

Build check:

```bash
npm run build
```

## 6. Local demo walkthrough

Recommended verification order:

```text
1. start the Backend
2. start the Frontend
3. open http://localhost:5173
4. create a knowledge base, for example 现代文学
5. ingest the two sample texts 《春》 and 《故乡》
6. open the generated source page and topic page from the page list
7. query 少年闰土 and choose to crystallise the answer into the wiki
8. run lint and look at broken links and orphan pages
9. open the Wiki graph to see the structure derived from page links
```

Notes:

- Uploading a document triggers embedding and a Chroma write.
- The first embedding can be slow.
- When a knowledge base or document is deleted, the Backend deletes the matching Chroma vectors at the same time.
- Raw material stays in `raw/`; the system never overwrites a source with a wiki summary.

## 7. Install the MCP Server

The MCP Server is the agent adapter layer. It only calls the Backend API and never reimplements retrieval.

Open a new terminal and enter the MCP directory:

```bash
cd mcp-server
```

Create a virtual environment:

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Copy the environment file:

Windows:

```powershell
Copy-Item .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

Default configuration:

```text
BACKEND_API_URL=http://localhost:8000
BACKEND_TIMEOUT_SECONDS=10
```

If the Backend runs on `127.0.0.1:8000`, change `.env` to:

```text
BACKEND_API_URL=http://127.0.0.1:8000
BACKEND_TIMEOUT_SECONDS=10
```

## 8. Start the MCP Server

Make sure the Backend is running, then run:

```bash
python server.py
```

Tools the MCP Server provides:

```text
search_knowledge_base
search_with_strategy
list_retrieval_strategies
list_answer_feedback
list_knowledge_bases
add_text_document
add_source_to_wiki
get_wiki_status
list_wiki_pages
read_wiki_page
query_wiki
synthesize_knowledge
lint_wiki
```

Tool responsibilities:

```text
search_knowledge_base:
  inputs: query, knowledge_base_id, top_k
  calls Backend /api/search
  forces llm.enabled=false and returns raw-source vector hits; it will not call an LLM because the web UI or .env enabled a model

list_knowledge_bases:
  calls Backend /api/knowledge-bases
  returns the knowledge base list

add_text_document:
  calls Backend /api/knowledge-bases/{id}/documents/text
  adds text knowledge

add_source_to_wiki:
  calls the Backend text ingest endpoint
  maintains the raw snapshot, source page, topic page, index and activity log together

get_wiki_status / list_wiki_pages / read_wiki_page:
  reads the workspace status and the Markdown pages

query_wiki:
  calls the Backend wiki query endpoint
  forces llm.enabled=false and returns the deterministic page-first answer with its cited pages; it will not call an LLM because the web UI or .env enabled a model

synthesize_knowledge:
  explicitly requests synthesis from the model configured in the AgentKB Backend
  source_mode=wiki uses the maintained Markdown pages; source_mode=rag uses raw-source vector retrieval
  accepts no API key or provider configuration; when the model is unavailable it keeps the local fallback result and model_error

lint_wiki:
  checks broken links, orphan pages and missing summaries
```

## 9. MCP client configuration examples

Different agent clients keep their configuration in different places, but the core settings are similar.

Absolute paths are recommended, so an inconsistent working directory cannot hide `server.py`.

Example:

```json
{
  "mcpServers": {
    "agentkb": {
      "command": "python",
      "args": [
        "/path/to/agentkb/mcp-server/server.py"
      ],
      "env": {
        "BACKEND_API_URL": "http://127.0.0.1:8000",
        "BACKEND_TIMEOUT_SECONDS": "10"
      }
    }
  }
}
```

If your client supports starting in the MCP Server directory, a relative path also works:

```json
{
  "mcpServers": {
    "agentkb": {
      "command": "python",
      "args": ["mcp-server/server.py"]
    }
  }
}
```

## 10. MCP call examples

An agent can call the tools like this:

```text
search_knowledge_base(
  query="春天相关内容",
  knowledge_base_id=1,
  top_k=5
)
```

Example response:

```json
{
  "query": "春天相关内容",
  "results": [
    {
      "document_id": 1,
      "title": "春",
      "chunk": "盼望着，盼望着，东风来了...",
      "score": 0.89
    }
  ]
}
```

Inside an external agent, prefer calling `query_wiki` or `search_knowledge_base` first and let that agent's own model finish the answer with its task context. Both base tools always force the AgentKB model off, so no second model is chained in.

Only when a separate, cited answer generated by AgentKB's own configured model is genuinely needed, call:

```text
synthesize_knowledge(
  knowledge_base_id=1,
  query="总结春天与花草的关系，并标注来源",
  source_mode="wiki",
  top_k=8
)
```

## 11. Tests

Run the full test suite from the repository root:

```bash
python -m pytest
```

Current coverage:

```text
Backend knowledge base CRUD
Backend document upload and deletion
Backend chunk splitting
Backend embedding / Chroma orchestration
Backend plain search and streaming search
MCP tool happy paths and error paths
Backend wiki workspace, page queries, lint and graph
Backend OpenAI-compatible model configuration, synthesis and local fallback
```

Frontend build check:

```bash
cd frontend
npm run build
```

## 12. Common problems

### 12.1 The frontend reports Failed to fetch

Usually the Backend is not running, or the CORS origin is not on the allowed list.

Check:

```text
is the Backend running on http://127.0.0.1:8000
is VITE_API_BASE_URL correct in frontend/.env
does CORS_ORIGINS in backend/.env include the frontend origin
```

Suggestions:

```text
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

### 12.2 The first document upload is slow

The first upload loads the sentence-transformers model and writes to ChromaDB.

If the model has not been downloaded yet, it is also fetched from Hugging Face.

### 12.3 Installing Chroma or model dependencies is slow

Try a different pip index:

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

If you hit SSL or proxy problems, switch back to the official index:

```bash
pip install -r requirements.txt -i https://pypi.org/simple
```

### 12.4 An MCP tool returns Backend service unavailable

Check:

```text
1. is the Backend running
2. is BACKEND_API_URL correct in the MCP .env
3. is Backend /health reachable
```

### 12.5 An MCP tool returns Backend request timed out

Possible causes:

```text
1. the embedding model loads slowly on first use
2. the uploaded or retrieved data is large
3. the Backend is writing vectors
```

You can raise it temporarily:

```text
BACKEND_TIMEOUT_SECONDS=30
```

## 13. Current development status

Delivered:

```text
1. knowledge base CRUD and pagination
2. text upload
3. .txt file upload
4. chunk splitting
5. embedding and ChromaDB writes
6. deleting a document or knowledge base deletes its vectors too
7. plain semantic search
8. the SSE streaming search endpoint
9. the React frontend demo
10. MCP Server tools
11. Backend and MCP tests
12. the local real-backend verification script
13. the Markdown wiki workspace: source snapshots, source pages, topic pages, index and activity log
14. optional model synthesis for topic pages, run after the transaction commits, with log-only failure
15. named retrieval strategies with adaptive link expansion and optional page vector recall
16. answer feedback, the replay comparison and the strategy proposal script
```

Next priorities:

```text
1. upgrade the current OpenAI-compatible synthesis to streaming answers
2. add more native provider adapters and model management
3. add PDF/DOCX upload
4. add rerank
5. add one-command Docker Compose deployment
```

## 14. Codex MCP configuration example

Where Codex keeps its configuration depends on the operating system and installation method. Use `agentkb` as the MCP server id:

```toml
[mcp_servers.agentkb]
command = 'python'
args = ['/path/to/agentkb/mcp-server/server.py']
startup_timeout_sec = 120

[mcp_servers.agentkb.env]
BACKEND_API_URL = 'http://127.0.0.1:8000'
BACKEND_TIMEOUT_SECONDS = '10'
```

Verification points:

```text
1. the Backend must be running on http://127.0.0.1:8000 before calling MCP tools
2. use `codex mcp list` to check that `agentkb` is loaded
3. the Python command must point at an interpreter with the `mcp` SDK installed
4. after changing MCP Server code or configuration, restart Codex or re-establish the MCP session
```

Local call notes:

```text
1. if MCP returns Bad Gateway while Backend /api/search works when called directly, check whether the local HTTP client inherits proxy environment variables
2. AgentKB MCP already sets `trust_env=False` in `mcp-server/tools.py`, so local Backend requests do not go through a system proxy
3. prefer `BACKEND_API_URL=http://127.0.0.1:8000` to avoid differences in how localhost resolves
```
