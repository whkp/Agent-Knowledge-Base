# 本地安装部署指南

**中文** · [English](LOCAL_INSTALLATION_EN.md)

本文档用于在本机安装并运行 `AgentKB`，包含：

- 本地知识库服务安装：Backend + Frontend
- MCP Server 安装：供 Codex、Claude Code 等 Agent 调用外接知识库
- 基础验证与常见问题

当前项目暂不使用 Docker；Docker Compose 仅作为后续优化方向保留。

当前工作台以 Markdown wiki 为主线：每个知识库都会在 `WIKI_ROOT_DIR` 下创建独立的 `kb-<id>/` 工作区，包含 `raw/`、`wiki/`、`index.md` 和 `log.md`。SQLite/Chroma 仍用于兼容旧文档与向量搜索路径。

安装命令之外的架构、查询模式、模型配置优先级、密钥边界和 API 契约请参阅 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 1. 环境要求

建议环境：

```text
Python: 3.11 或 3.12
Node.js: 20+
npm: 10+
Git: 任意较新版本
```

项目使用的主要组件：

```text
Backend: FastAPI + SQLite + SQLAlchemy
Vector DB: ChromaDB
Embedding: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
Frontend: React + Vite + TypeScript
MCP Server: Python MCP SDK
```

首次安装完整后端依赖时会下载 PyTorch、ChromaDB、sentence-transformers 及 embedding 模型，耗时较长是正常的。模型成功下载后会使用本地缓存；如果 Hugging Face 临时不可用或限流，AgentKB 会自动尝试从该缓存加载模型。

当前工作区使用 AgentKB 的存储命名：数据库为 `data/agentkb.db`，Chroma 集合为 `agentkb_chunks`。`data/`、`WIKI_ROOT_DIR` 和 Chroma 数据属于本地运行时文件，不应提交到 Git；其中 `raw/sources/` 和 `documents.content` 会保留可用于 RAG 的来源正文。

## 2. 克隆项目

```bash
git clone https://github.com/kanna12580/agentkb.git
cd agentkb
```

如果已经在本地仓库中：

```bash
git pull
```

## 3. 本地知识库 Backend 安装

进入后端目录：

```bash
cd backend
```

创建虚拟环境：

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

安装依赖：

```bash
pip install -r requirements.txt
```

如果官方 PyPI 较慢，可以临时指定国内源，例如：

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

复制环境变量文件：

Windows:

```powershell
Copy-Item .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

默认 `.env.example` 内容已经适合本地开发：

```text
APP_NAME=AgentKB API
DATABASE_URL=sqlite:///./data/agentkb.db
CHROMA_PERSIST_DIR=./chroma
CHROMA_COLLECTION_NAME=agentkb_chunks
EMBEDDING_MODEL_NAME=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
VECTOR_INDEX_ENABLED=true
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

### 可选：通过 `.env` 配置大模型

AgentKB 默认不调用生成式模型。启用后，页面优先和原始资料 RAG 都会先检索，再把命中的内容作为受限上下文交给模型生成带 `[1]`、`[2]` 引用的回答。

接口遵循 OpenAI-compatible Chat Completions 协议：`POST {LLM_BASE_URL}/chat/completions`。可以接入 OpenAI、兼容网关、vLLM、LM Studio、Ollama 的 OpenAI-compatible 端点或其他兼容服务。

在 `backend/.env` 中加入：

```dotenv
LLM_ENABLED=true
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your-api-key
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0.2
LLM_MAX_TOKENS=1200
LLM_TIMEOUT_SECONDS=60
# 输入给模型的检索上下文最大字符数
LLM_CONTEXT_MAX_CHARS=12000
```

`LLM_BASE_URL` 应填写 API 根路径，通常以 `/v1` 结尾，不要包含 `/chat/completions`。例如本地服务可使用 `http://127.0.0.1:1234/v1` 或服务实际暴露的兼容 API 根路径。配置改动后重启 Backend 生效。

未设置 `LLM_ENABLED=true` 时，系统继续使用本地页面排序和向量召回，不会发生模型调用，也不会产生模型费用。

### 导入外部来源全文

外部平台正文的采集与服务端解耦。可以用本地已登录的只读采集工具取得 JSON/JSONL，再导入当前知识库：

```bash
cd backend
PYTHONPATH=. python scripts/import_source_snapshots.py \
  --knowledge-base-id 1 \
  --input /absolute/path/to/source-snapshots.json
```

导入器支持 JSON 数组和 JSONL，字段示例：

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

`full_text` 会同时写入 SQLite 原文、`WIKI_ROOT_DIR/kb-<id>/raw/sources/` 和 Chroma 分块；`excerpt` 与 `link_only` 会被保留策略标记，后者不应作为原文 RAG 依据。重复执行时，URL 和内容哈希相同的记录会跳过。完整第三方正文只放在本地运行时目录，不进入公开仓库。

### 网页端临时配置

工作台右上角的“配置模型”可在不改文件、不重启 Backend 的情况下设置服务地址、模型、温度、输出长度、超时和 API Key，并可先测试连接。该设置通过以下本地 API 生效：

```text
GET /api/llm/config    # 返回有效配置状态，不返回 API Key
PUT /api/llm/config    # 只保存到当前 Backend 进程内存
POST /api/llm/test     # 用填入的配置请求一次模型，验证连通性
```

网页端 API Key 只用于当前 Backend 进程：不会写入 Markdown、SQLite 或浏览器存储；Backend 重启后会丢失，并回退到 `.env` 配置。面向外网部署时，应使用 HTTPS、认证和服务器端 `.env` 管理密钥，不应把 API Key 填入公开网页；当前项目默认面向受信任的本地工作台使用。

启动后端：

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

访问：

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/health
```

健康检查应返回：

```json
{"status":"ok"}
```

## 4. 真实后端链路验证

项目提供了一个真实联调脚本，用临时 SQLite 和 Chroma 目录验证：

- 创建知识库
- 上传《春》《故乡》片段
- 写入真实 Chroma 向量
- 使用真实 embedding 模型检索
- 验证搜索排序

在仓库根目录运行：

```bash
python backend/scripts/validate_real_backend.py
```

默认存储标识：

```text
数据库：data/agentkb.db
Chroma 集合：agentkb_chunks
```

如果从旧版本迁移本地数据，可以在项目根目录执行：

```bash
python backend/scripts/migrate_storage_names.py
```

先查看迁移目标而不执行：

```bash
python backend/scripts/migrate_storage_names.py --dry-run
```

旧 Chroma 数据不会自动重命名集合。建议使用新集合名重新建立索引；如需暂时读取旧集合，可在 `.env` 中临时设置 `CHROMA_COLLECTION_NAME=livingwiki_chunks` 或 `CHROMA_COLLECTION_NAME=kk_knowledge_chunks`，完成迁移后再切回 `agentkb_chunks`。

预期检索结果：

```text
春天 -> 春
花草 -> 春
少年闰土 -> 故乡
小孩子 -> 故乡
乡下少年 -> 故乡
```

第一次运行会下载 embedding 模型，可能较慢。

## 5. Frontend 安装与启动

打开新终端，进入前端目录：

```bash
cd frontend
```

安装依赖：

```bash
npm install
```

启动开发服务：

```bash
npm run dev -- --host 127.0.0.1 --port 5173
```

访问：

```text
http://localhost:5173
```

前端 Demo 支持：

```text
1. 创建知识库和 Markdown 工作区
2. 摄取文本或 txt 来源，生成 raw 快照、来源页、主题页和 index
3. 浏览、编辑和保存 wiki Markdown 页面
4. 基于 wiki 页面查询，并把回答结晶到 `wiki/queries/`
5. 运行 lint 健康检查和查看派生图谱
6. 可选：在“配置模型”中接入 OpenAI-compatible 服务，再次查询并检查带引用的模型综合回答
```

构建检查：

```bash
npm run build
```

## 6. 本地知识库演示流程

推荐按这个顺序验证：

```text
1. 启动 Backend
2. 启动 Frontend
3. 打开 http://localhost:5173
4. 创建知识库：现代文学
5. 摄取文本《春》和《故乡》
6. 在页面目录中打开来源页和主题页
7. 查询：少年闰土，并选择“结晶到 wiki”
8. 运行 lint，查看断链和孤立页面
9. 打开 Wiki graph 查看页面链接派生的结构
```

注意：

- 上传文档时会触发 embedding 和 Chroma 写入。
- 第一次向量化可能较慢。
- 删除知识库/文档时，Backend 会同步删除对应 Chroma 向量。
- 原始素材保存在 `raw/`，系统不会用 wiki 摘要覆盖原始来源。

## 7. MCP Server 安装

MCP Server 是 Agent 适配层，只调用 Backend API，不重复实现检索逻辑。

打开新终端，进入 MCP 目录：

```bash
cd mcp-server
```

创建虚拟环境：

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

安装依赖：

```bash
pip install -r requirements.txt
```

复制环境变量文件：

Windows:

```powershell
Copy-Item .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

默认配置：

```text
BACKEND_API_URL=http://localhost:8000
BACKEND_TIMEOUT_SECONDS=10
```

如果 Backend 运行在 `127.0.0.1:8000`，可以将 `.env` 改为：

```text
BACKEND_API_URL=http://127.0.0.1:8000
BACKEND_TIMEOUT_SECONDS=10
```

## 8. 启动 MCP Server

确保 Backend 已启动，然后运行：

```bash
python server.py
```

MCP Server 提供工具：

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

工具职责：

```text
search_knowledge_base:
  输入 query、knowledge_base_id、top_k
  调用 Backend /api/search
  强制 llm.enabled=false，返回原始资料向量检索结果，不会因网页端或 .env 启用了模型而额外调用 LLM

list_knowledge_bases:
  调用 Backend /api/knowledge-bases
  返回知识库列表

add_text_document:
  调用 Backend /api/knowledge-bases/{id}/documents/text
  添加文本知识

add_source_to_wiki:
  调用 Backend 文本摄取接口
  同时维护 raw 快照、来源页、主题页、索引和活动日志

get_wiki_status / list_wiki_pages / read_wiki_page:
  读取知识库工作区状态和 Markdown 页面

query_wiki:
  调用 Backend wiki 查询接口
  强制 llm.enabled=false，返回页面优先的确定性回答和引用页面，不会因网页端或 .env 启用了模型而额外调用 LLM

synthesize_knowledge:
  显式请求 AgentKB Backend 已配置的模型综合
  参数 source_mode=wiki 使用维护的 Markdown 页面；source_mode=rag 使用原始资料向量检索
  不接收 API Key 或 Provider 配置；模型不可用时保留本地回退结果和 model_error

lint_wiki:
  检查断链、孤立页和缺少摘要
```

## 9. MCP 客户端配置示例

不同 Agent 客户端的配置文件位置不同，但核心配置类似。

推荐使用绝对路径，避免工作目录不一致导致找不到 `server.py`。

示例：

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

如果你的客户端支持在 MCP Server 目录中启动，也可以用相对路径：

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

## 10. MCP 调用示例

Agent 可以这样调用工具：

```text
search_knowledge_base(
  query="春天相关内容",
  knowledge_base_id=1,
  top_k=5
)
```

返回示例：

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

在外部 Agent 中，推荐先调用 `query_wiki` 或 `search_knowledge_base`，由该 Agent 的主模型结合当前任务上下文完成最终回答。两项基础工具默认且强制不调用 AgentKB LLM，因此不会产生双模型串联。

只有明确需要 AgentKB 基于自身配置模型生成带引用的独立回答时，再调用：

```text
synthesize_knowledge(
  knowledge_base_id=1,
  query="总结春天与花草的关系，并标注来源",
  source_mode="wiki",
  top_k=8
)
```

## 11. 测试

在仓库根目录运行完整测试：

```bash
python -m pytest
```

当前覆盖：

```text
Backend 知识库 CRUD
Backend 文档上传和删除
Backend chunk 切分
Backend embedding/Chroma 编排
Backend 普通搜索和流式搜索
MCP 工具正常与错误路径
Backend Wiki 工作区、页面查询、lint 和图谱
Backend OpenAI-compatible 模型配置、模型综合与本地回退
Backend 检索打分、策略、反馈与回放/提案工具
```

前端构建测试：

```bash
cd frontend
npm run build
```

## 12. 常见问题

### 12.1 访问前端时报 Failed to fetch

通常是 Backend 未启动，或 CORS origin 不在允许列表中。

检查：

```text
Backend 是否运行在 http://127.0.0.1:8000
frontend/.env 中 VITE_API_BASE_URL 是否正确
backend/.env 中 CORS_ORIGINS 是否包含前端地址
```

建议：

```text
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

### 12.2 第一次上传文档很慢

第一次上传会加载 sentence-transformers 模型，并写入 ChromaDB。

如果模型尚未下载，还会从 Hugging Face 下载模型文件。

### 12.3 Chroma 或模型依赖安装慢

可以尝试更换 pip 源：

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

如果遇到 SSL 或代理问题，切回官方源：

```bash
pip install -r requirements.txt -i https://pypi.org/simple
```

### 12.4 MCP Tool 返回 Backend service unavailable

检查：

```text
1. Backend 是否启动
2. MCP .env 中 BACKEND_API_URL 是否正确
3. Backend /health 是否可访问
```

### 12.5 MCP Tool 返回 Backend request timed out

可能原因：

```text
1. 首次加载 embedding 模型较慢
2. 上传或检索数据较大
3. Backend 正在执行向量写入
```

可以临时调大：

```text
BACKEND_TIMEOUT_SECONDS=30
```

## 13. 当前基础开发状态

基础 MVP 已完成：

```text
1. 知识库 CRUD 与分页
2. 文本上传
3. txt 文件上传
4. chunk 切分
5. embedding 和 ChromaDB 写入
6. 文档/知识库删除时同步删除向量
7. 普通语义搜索
8. SSE 流式搜索接口
9. React 前端 Demo
10. MCP Server 工具
11. Backend 和 MCP 测试
12. 本地真实后端验证脚本
13. Markdown wiki 工作区：来源快照、来源页、主题页、索引和活动日志
14. 可选的主题页模型综合：在事务提交后执行，失败只记日志
15. 命名检索策略、自适应链接扩展与可选的页面向量召回
16. 回答反馈、回放对比与策略提案脚本
```

后续重点优化：

```text
1. 将当前 OpenAI-compatible 模型综合升级为流式回答生成
2. 增加更多 Provider 原生适配与模型管理
3. 增加 PDF/DOCX 上传
4. 增加 rerank
5. 增加 Docker Compose 一键部署
```

## 14. Codex MCP 配置示例

Codex 配置文件的位置取决于操作系统和安装方式。配置项可以使用 `agentkb` 作为 MCP server id：

```toml
[mcp_servers.agentkb]
command = 'python'
args = ['/path/to/agentkb/mcp-server/server.py']
startup_timeout_sec = 120

[mcp_servers.agentkb.env]
BACKEND_API_URL = 'http://127.0.0.1:8000'
BACKEND_TIMEOUT_SECONDS = '10'
```

验证要点：

```text
1. 调用 MCP 工具前，Backend 必须运行在 http://127.0.0.1:8000。
2. 使用 `codex mcp list` 检查 `agentkb` 是否已加载。
3. Python 命令必须指向安装了 `mcp` SDK 的解释器。
4. 修改 MCP Server 代码或配置后，需要重启 Codex 或重新建立 MCP 会话。
```

本地调用注意事项：

```text
1. 如果 MCP 返回 Bad Gateway 但 Backend /api/search 直接访问正常，检查本地 HTTP client 是否继承了代理环境变量。
2. AgentKB MCP 已在 `mcp-server/tools.py` 中设置 `trust_env=False`，本地 Backend 请求不会经过系统代理。
3. 建议使用 `BACKEND_API_URL=http://127.0.0.1:8000`，避免 localhost 解析差异。
```
