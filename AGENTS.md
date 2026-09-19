# AGENTS.md

本文件是 `AgentKB` 的开发导航，供 Codex/Agent 在后续任务中优先读取。项目目标是构建面向 AI Agent 的 Markdown-first 知识库系统：Backend 负责知识库、来源摄取、wiki 文件维护、分块、向量化与检索；Frontend 提供 AgentKB 工作台；MCP Server 将同一套知识库工作流暴露为 Agent 工具。

## Wiki-first 约束

- 每个知识库都有独立工作区：`data/wiki/kb-<id>/`。
- `raw/` 是不可变来源快照；`wiki/` 是可维护页面；`index.md` 是内容索引；`log.md` 是 append-only 活动记录。
- 外部来源快照必须保留原文正文、来源 URL、平台、作者、抓取时间、保存策略和内容哈希；完整第三方正文只能进入被 Git 忽略的运行时目录。
- `full_text`、`excerpt`、`link_only` 必须如实标记；不能用已有摘要补写或伪造不可访问来源的原文。
- Markdown wiki 是长期事实载体，SQLite 是业务兼容索引，Chroma 是可选的检索加速层。
- 页面之间使用 `[[path/without-extension]]` 建立链接；图谱只能从页面链接派生，不能成为独立事实来源。
- 摄取来源必须同时维护 raw 来源页、主题页、索引和日志；查询可以把有价值的回答保存到 `wiki/queries/`。
- 主题页的归属与来源关系是确定性逻辑：**没有模型时**只有标题完全相同或两个独立标题词命中才并入已有主题页，`sources:` front matter 与 `## Sources` 列表由代码维护，模型只能改 `## Evolving synthesis` 段；**有模型时**模型从候选主题页中选定归属，但只能选择候选项或新建，不能创建/重命名文件。
- 模型相关的主题维护必须发生在数据库事务提交之后，且失败只记日志，不得回滚已提交的摄取。
- 模型失败或不启用时摄取仍要产出可用的来源页与来源列表。
- 页面查询命中后可以沿 `[[链接]]` 扩展一跳补充证据，扩展结果必须标记 `related=true`，且不得改变直接命中的排序；确定性回答与结晶页面只使用直接命中。
- 回答反馈是检索策略唯一的评估信号来源：它写入 SQLite 的 `query_feedback`，只做记录，不得写入 wiki 页面或 Markdown 工作区，也不得改变当次回答。
- 检索策略的集合是代码、选择是数据：新增策略必须改代码并配测试；查询通过 `strategy` 字段选择，响应必须如实回报 `strategy/hops/vectors/planned`，反馈必须记录 `strategy_id`，这样"哪个配置产生了哪个答案"才可追溯。
- 检索打分必须与查询长度无关（使用 BM25 与覆盖率，而不是裸词频），语义检索必须有相似度下限；embedding 或模型不可用时必须确定性退回，并如实报告实际用了什么。
- lint 至少检查断链、孤立页和缺少摘要；不要修改 `.obsidian/` 或其他第三方元数据。

如果本文件的阶段计划不足以判断产品边界、接口细节、演示路径或验收口径，优先参考根目录 `README.md`、`docs/ARCHITECTURE.md` 和 `docs/LOCAL_INSTALLATION.md`。阶段 0 至 7 是历史 MVP 交付记录；当前实现契约以架构文档和测试为准。

项目后续演进有两条主线：
- RAG 应用：Backend 已支持通过 OpenAI-compatible 接口，将检索 chunks 或 wiki 页面综合成带引用依据的回答；后续将补齐模型生成流式输出和更多 Provider 原生适配。
- MCP 外接知识库：MCP Server 将 Backend 检索能力暴露为 Agent 工具。`query_wiki` 和 `search_knowledge_base` 必须强制 `llm.enabled=false`，由 Codex/Claude Code/OpenClaw 等调用方 Agent 完成最终推理；仅 `synthesize_knowledge` 可显式请求 Backend 模型综合。

## 全局原则

- Backend 是唯一业务核心，Frontend 和 MCP Server 不重复实现 wiki、检索或文件维护逻辑。
- SQLite 保存业务数据，ChromaDB 保存 chunk embedding。
- 删除知识库或文档时，必须同步删除对应 Chroma 向量。
- 外部来源删除时，必须同步删除 SQLite 记录、raw Markdown 快照、Wiki 来源关系和 Chroma 向量。
- 同一个 Backend 检索核心要同时服务普通用户路径和 Agent 工具路径。
- 优先交付可演示 MVP，再补测试和部署优化项。
- 中文语义检索默认使用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`。
- API 错误必须清晰：空 query、知识库不存在、空文档、非 txt、文件过大、embedding/向量库/检索失败。
- `POST /api/knowledge-bases/{id}/documents/source-snapshot` 必须复用普通摄取流程，并将来源元数据透传到 Chroma 和 RAG `SearchResult`。
- OpenAI-compatible 模型综合是可选能力：默认不开启，模型失败必须保留页面回答或检索结果，且不得将 API Key 写入 Markdown、SQLite、日志或 API 响应。
- 模型有效配置优先级为单次请求覆盖 > Backend 进程运行时配置 > `.env`/环境变量 > 默认值；网页配置只保存于 Backend 进程内存，重启后失效。
- MCP 基础检索工具必须传入单次请求覆盖 `{"enabled": false}`，不得继承网页或 `.env` 的模型启用状态；MCP 模型综合必须是名称和参数都明确的独立工具，且不接收 API Key。MCP 的摄取工具必须传 `synthesize_topic=false`，不得隐式触发主题页改写。
- `POST /api/search/stream` 仍是仅检索的兼容 SSE 契约。不要把模型 token 直接塞入旧事件格式；新增流式模型回答前必须先设计并记录新的事件契约。

## 阶段 0：仓库与环境

目标：让项目结构稳定，后续任务能直接进入开发。

交付：
- `backend/`、`frontend/`、`mcp-server/` 基础目录。
- `.gitignore`、`README.md`。
- Backend 和 MCP 的 `.env.example`。
- Frontend 的 Vite/React/TypeScript 配置文件。

验收：
- `git status` 能清楚展示初始化文件。
- README 包含本地启动方式和模块说明。

## 阶段 1：Backend 基础 API

目标：完成 FastAPI 服务、SQLite 模型和知识库 CRUD。

建议文件：
- `backend/app/main.py`
- `backend/app/config.py`
- `backend/app/db/database.py`
- `backend/app/db/models.py`
- `backend/app/db/schemas.py`
- `backend/app/api/knowledge_routes.py`
- `backend/app/services/knowledge_service.py`

任务：
1. 初始化 FastAPI app，挂载 `/api` 路由。
2. 建立 SQLAlchemy engine/session/base。
3. 定义 `knowledge_bases` 表。
4. 实现创建、分页查询、详情、更新、删除。
5. 为不存在资源返回 404。

验收：
- `POST /api/knowledge-bases` 可创建知识库。
- `GET /api/knowledge-bases?page=1&page_size=10` 可分页。
- CRUD 单元测试通过。

## 阶段 2：文档上传与分块

目标：支持文本输入和 txt 上传，并保存 chunks。

建议文件：
- `backend/app/api/document_routes.py`
- `backend/app/services/document_service.py`
- `backend/app/services/chunk_service.py`

任务：
1. 定义 `documents`、`document_chunks` 表。
2. 实现文本上传接口：`POST /api/knowledge-bases/{kb_id}/documents/text`。
3. 实现 txt 上传接口：`POST /api/knowledge-bases/{kb_id}/documents/file`。
4. 实现文档列表、详情、删除。
5. 分块策略：`chunk_size=500`，`chunk_overlap=80`；优先按段落，段落过长再固定长度切分。

验收：
- 空文本失败。
- 非 txt 文件失败。
- 不存在知识库失败。
- 上传后能看到 chunk 记录。

## 阶段 3：Embedding 与 ChromaDB

目标：上传文档后自动向量化并写入 ChromaDB。

建议文件：
- `backend/app/services/embedding_service.py`
- `backend/app/vector/chroma_client.py`

任务：
1. 封装 sentence-transformers embedding。
2. 封装 ChromaDB collection 初始化。
3. 上传文档后为 chunks 生成向量。
4. 写入 ChromaDB，metadata 至少包含 `knowledge_base_id`、`document_id`、`title`、`chunk_id`。
5. 将 Chroma vector id 回写到 `document_chunks.vector_id`。
6. 删除知识库/文档时同步删除向量。

验收：
- 上传《春》《故乡》后 Chroma 中有对应向量。
- 删除文档后对应向量不可再被检索。

## 阶段 4：语义检索与流式接口

目标：完成普通搜索和 SSE 流式搜索。

建议文件：
- `backend/app/api/search_routes.py`
- `backend/app/services/retrieval_service.py`
- `backend/app/services/stream_service.py`

任务：
1. 实现 `POST /api/search`。
2. 校验 query 非空、知识库存在、top_k 合理。
3. query embedding 后按 `knowledge_base_id` 过滤 Chroma。
4. 返回 document_id、title、chunk、score。
5. 实现 `POST /api/search/stream`，返回 `text/event-stream`。
6. 流式事件类型：`start`、`delta`、`result`、`error`、`done`。

验收：
- 搜索“春天”返回《春》。
- 搜索“少年闰土”返回《故乡》。
- 搜索“小孩子”返回《故乡》。
- 流式接口能逐步返回事件。

## 阶段 5：Frontend Demo

目标：用单页完成面试演示路径。

布局：
- 左侧：知识库列表与创建。
- 中间：文本输入和 txt 上传。
- 右侧：搜索框与流式结果。

建议文件：
- `frontend/src/api/client.ts`
- `frontend/src/api/knowledge.ts`
- `frontend/src/api/documents.ts`
- `frontend/src/api/search.ts`
- `frontend/src/components/KnowledgeBaseList.tsx`
- `frontend/src/components/DocumentUploader.tsx`
- `frontend/src/components/SearchPanel.tsx`
- `frontend/src/components/StreamingResult.tsx`
- `frontend/src/App.tsx`

任务：
1. 创建和选择知识库。
2. 上传文本和 txt 文件。
3. 普通搜索和流式搜索。
4. 使用 `fetch + ReadableStream` 处理 POST SSE。

验收：
- 浏览器中可完成创建知识库、上传《春》《故乡》、搜索和流式展示。

## 阶段 6：MCP Server

目标：让 Agent 能通过 MCP Tool 查询知识库。

建议文件：
- `mcp-server/server.py`
- `mcp-server/tools.py`

必做工具：
- `search_knowledge_base(query: str, knowledge_base_id: int, top_k: int = 5)`

加分工具：
- `list_knowledge_bases`
- `add_text_document`

任务：
1. MCP Server 只调用 Backend HTTP API。
2. 默认 Backend 地址从 `BACKEND_API_URL` 读取。
3. HTTP timeout 设置为 10 秒。
4. 处理后端不可用、404、超时、空结果。

验收：
- Agent 可配置并调用 `search_knowledge_base`。
- Backend 未启动时返回明确错误。

## 阶段 7：测试与交付

目标：项目可验证、可演示、可讲解。

Backend 测试：
- `test_knowledge.py`
- `test_upload.py`
- `test_search.py`

MCP 测试：
- `test_mcp_tool.py`

文档：
- README 增加启动方式、API 示例、MCP 配置、面试演示流程、后续优化方向。
- 后续优化必须保留两条方向：升级 LLM RAG 为流式回答；封装 MCP Server 做 Agent 外接知识库。

MVP 验收标准：
- 可以创建、查询、更新、删除知识库。
- 知识库列表支持分页。
- 可以直接输入文本和上传 txt 文件。
- 上传后自动分块和向量化。
- 可以语义搜索并流式返回结果。
- MCP Server 提供 `search_knowledge_base`。
- 常见错误均有明确响应。
