# AGENTS.md

本文件是 `AgentKB` 的开发导航。新接手的 Agent / 工程师读完这一份就能开工：**项目概要 → 一分钟上手 → 硬性契约 → 已完成能力 → 已经踩过的坑 → 历史阶段**。

本文件里的数字是 2026-09-19 的记录，过时会很正常；命令与契约是稳定的。

> 根目录 `TODO.md` 是**本地工作清单**（`.gitignore` 已排除，克隆下来没有属正常）：写"接下来做什么、为什么、怎么验证"。
> 本文件写"现在是什么、不能破坏什么、哪里容易踩坑"。公开文档不要链接 `TODO.md`。

---

## 1. 项目概要

把 Markdown wiki 当长期事实载体、SQLite 当业务索引、Chroma 当可重建的检索加速层，面向**人和 AI Agent 同时**提供可摄取、可追溯的本地知识库。

| 模块 | 职责 | 入口 |
| --- | --- | --- |
| `backend/` | **唯一业务核心**：知识库 CRUD、来源摄取与 wiki 维护、分块、embedding、检索、可选模型综合、反馈与回放工具 | `backend/app/main.py` |
| `frontend/` | 工作台（"阅览室"）：四张纸＝页面阅读/编辑、派生链接图谱、wiki 检查、回答反馈 | `frontend/src/App.tsx` |
| `mcp-server/` | 把 Backend 的 HTTP 工作流暴露为 MCP 工具，供 Codex / Claude Code 等调用方 Agent 使用 | `mcp-server/tools.py` |
| `docs/` | 架构、检索、自演化、安装文档，**每份都有中英两版** | `docs/ARCHITECTURE.md` / `docs/ARCHITECTURE_EN.md` |
| `proposals/` | 由回放证据生成的策略提案（Markdown；晋升＝一次 git 改动） | — |

数据落盘（都已被 git 忽略）：

```text
backend/data/agentkb.db       SQLite：知识库、文档、chunk、反馈
backend/data/wiki/kb-<id>/    Markdown 工作区（WIKI_ROOT_DIR）
backend/chroma/               可选的 chunk 向量库
```

项目后续演进有两条主线：

- **RAG 应用**：Backend 可通过 OpenAI-compatible 接口，把检索 chunks 或 wiki 页面综合成带引用依据的回答；后续补齐模型生成流式输出与更多 Provider 原生适配。
- **MCP 外接知识库**：`query_wiki` 和 `search_knowledge_base` 必须强制 `llm.enabled=false`，最终推理由调用方 Agent 完成；仅 `synthesize_knowledge` 可显式请求 Backend 模型综合。

---

## 2. 一分钟上手

```bash
# 测试
cd backend    && python3.13 -m pytest tests -q      # 118 条
cd mcp-server && python3.13 -m pytest tests -q      # 19 条
cd frontend   && npx tsc -b && npm run build        # 类型检查 + 打包
cd frontend   && npm run bench:layout               # 图谱布局耗时与质量指标

# 起服务
cd backend  && python3.13 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
cd frontend && npx vite --port 5173
```

- **必须 `python3.13`**：本机 `python3.14` 没有 pytest。
- **改了 Backend 代码要重启**：启动命令没有 `--reload`，否则你测到的还是旧进程（尤其实机验证时容易被骗）。
- **从 `backend/` 启动**：`WIKI_ROOT_DIR` 与数据库路径都相对进程 CWD。仓库根目录下那份 `data/` 是历史遗留（在根目录跑过一次留下的另一套库与工作区），不是你正在用的那份。
- 端口：Backend `127.0.0.1:8000`、工作台 `localhost:5173`。
- CORS 默认只允许 `http://localhost:5173`。换端口或用生产构建预览（`vite preview`）时必须同步改 `CORS_ORIGINS`，否则前端拿不到数据、界面看起来像坏了。
- 环境变量在 `backend/.env`（从 `.env.example` 复制）；默认不开启模型。
- 网络：本机 `github.com:443` 的 HTTPS git 不通（HTTP2 framing error），远程用 SSH `git@github.com:whkp/Agent-Knowledge-Base.git`；`httpx` 会拾取系统代理，**新建出站客户端必须 `trust_env=False`**（`mcp-server/tools.py`、`llm_service` 都这么做）。macOS 没有 `timeout` 命令。

---

## 3. 硬性契约：Wiki-first 约束

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
- 回答反馈是检索策略唯一的评估信号来源：它写入 SQLite 的 `query_feedback`，只做记录，不得写入 wiki 页面或 Markdown 工作区，也不得改变当次回答。点「没用」时用 `bad_paths` 指认具体哪条引用不对；该字段只在 `rating=-1` 时有效（👍 携带它应被拒绝，而不是被静默丢弃），且它仍不是标注答案，回放与提案的措辞不得把「隐藏了被指认的引用」说成「答案对了」。
- 检索策略的集合是代码、选择是数据：新增策略必须改代码并配测试；查询通过 `strategy` 字段选择，响应必须如实回报 `strategy/hops/vectors/planned`，反馈必须记录 `strategy_id`，这样"哪个配置产生了哪个答案"才可追溯。
- 检索打分必须与查询长度无关（使用 BM25 与覆盖率，而不是裸词频），语义检索必须有相似度下限；embedding 或模型不可用时必须确定性退回，并如实报告实际用了什么。
- 同一来源的重复摄取必须被识别：相同内容 + 相同来源身份（URL / 文件名 / 标题）返回 `409` 并指向更新接口；内容相同但来源不同的两次摄取是**两份来源**，不得合并。
- 原地更新必须保留 document id、`raw/` 快照路径、来源页路径与主题页来源行；不得出现孤儿页面或重复的 `## Sources` 行。
- lint 至少检查断链、孤立页和缺少摘要；不要修改 `.obsidian/` 或其他第三方元数据。

## 4. 全局原则

- Backend 是唯一业务核心，Frontend 和 MCP Server 不重复实现 wiki、检索或文件维护逻辑。
- SQLite 保存业务数据，ChromaDB 保存 chunk embedding。
- 删除知识库或文档时，必须同步删除对应 Chroma 向量。
- 外部来源删除时，必须同步删除 SQLite 记录、raw Markdown 快照、Wiki 来源关系和 Chroma 向量。
- 同一个 Backend 检索核心要同时服务普通用户路径和 Agent 工具路径。
- 中文语义检索默认使用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`。
- API 错误必须清晰：空 query、知识库不存在、空文档、非 txt、文件过大、embedding/向量库/检索失败。
- `POST /api/knowledge-bases/{id}/documents/source-snapshot` 必须复用普通摄取流程，并将来源元数据透传到 Chroma 和 RAG `SearchResult`。
- OpenAI-compatible 模型综合是可选能力：默认不开启，模型失败必须保留页面回答或检索结果，且不得将 API Key 写入 Markdown、SQLite、日志或 API 响应。
- 模型有效配置优先级为单次请求覆盖 > Backend 进程运行时配置 > `.env`/环境变量 > 默认值；网页配置只保存于 Backend 进程内存，重启后失效。
- MCP 基础检索工具必须传入单次请求覆盖 `{"enabled": false}`，不得继承网页或 `.env` 的模型启用状态；MCP 模型综合必须是名称和参数都明确的独立工具，且不接收 API Key。MCP 的摄取工具必须传 `synthesize_topic=false`，不得隐式触发主题页改写。
- `POST /api/search/stream` 仍是仅检索的兼容 SSE 契约。不要把模型 token 直接塞入旧事件格式；新增流式模型回答前必须先设计并记录新的事件契约。

---

## 5. 已经做完的能力（不要重做，先看这里）

| 能力 | 代码 | 测试 |
| --- | --- | --- |
| 知识库 CRUD 与分页 | `app/api/knowledge_routes.py`、`app/services/knowledge_service.py` | `tests/test_knowledge.py` |
| 文本 / `.txt` / 来源快照摄取，分块（500/80），`link_only` 不入库 | `app/services/document_service.py`、`chunk_service.py` | `tests/test_upload.py` |
| 来源重复摄取 `409` 去重；`PUT .../documents/{id}` 原地更新（保留 id 与三个路径） | `app/services/document_service.py`、`wiki_service.update_source` | `tests/test_document_update.py` |
| 工作区维护：来源页、主题页、`index.md`、`log.md`、确定性主题归属、事务外模型维护 | `app/services/wiki_service.py` | `tests/test_wiki.py` |
| 检索：CJK 二元组 + BM25 + 覆盖率打分、自适应链接扩展（`related=true`）、页面向量召回（带下限）、五条命名策略、响应回报 `strategy/hops/vectors/planned` | `app/services/retrieval_service.py`、`retrieval_strategy.py` | `tests/test_search.py`、`test_wiki.py` |
| 可选模型综合（页面 / 原始资料 / 显式 MCP 路径）、配置优先级、失败回落 | `app/services/llm_service.py` | `tests/test_llm.py` |
| 反馈闭环：👍/👎 + 原因 + `bad_paths` → 回放 → 提案 | `app/api/feedback_routes.py`、`scripts/replay_queries.py`、`scripts/propose_strategy_change.py` | `tests/test_feedback.py`、`test_wiki.py` |
| 工作台四张纸、引用脚注、Barnes-Hut 图谱布局、反馈纸 | `frontend/src/App.tsx`、`src/graph/layout.ts`、`src/styles.css` | 无自动化测试（见 §6.4） |
| MCP 工具：检索、策略、摄取、原地更新、页面、状态、lint、反馈、显式综合 | `mcp-server/tools.py`、`server.py` | `mcp-server/tests/test_mcp_tools.py` |
| `docs/` 中英成对文档 + `skills/agentkb-retrieval` | `docs/`、`skills/` | 人工核对（数字/标识符需一致） |

---

## 6. 已经踩过的坑

这一节是本文档最有价值的部分：每条都是实际发生过的，括号里是症状。

### 6.1 编辑代码时

- **`str.replace` 不带 count 会替换所有同名片段**（症状：只改 `update_source` 的编辑同时改了 `ingest_source`，摄取开始覆盖人工/模型整理的正文，测试变红）。改多处出现的模式时，用更长的上下文锚定或显式限制次数。
- **heredoc 与 `&&` 链会静默中断**（症状：下一次运行报 `NameError: 名字不存在`，其实文件压根没写进去——链中间那步失败了）。写文件后 `grep`/`wc -l` 确认，或把写入与运行分成两条命令。
- **中文字符串会咬人**：在 Python f-string / heredoc 里写中文引号（`"…"`）容易破坏字符串字面量。改用 「」。
- **迁移字典容易插出重名常量**（症状：新加的列生效了，同名的旧字典被覆盖、旧迁移列悄悄失效——实际发生在 `_DOCUMENT_COLUMN_MIGRATIONS` 上）。加字段前先 `grep` 同类字典，合并进去而不是新起一个同名常量。
- **替换时先确认锚点唯一**：本仓库同一模式常出现在 `ingest_source` 与 `update_source`、中英两份文档里。

### 6.2 数据一致性（真 bug 多在这里）

- **SQLite 会复用行号**（症状：原地更新后文档静默失去全部向量）。删旧 chunk 再插新 chunk，新行可能拿到旧行 id；若向量 id 由行 id 派生，"先写新向量、再删旧向量"会把刚写入的当作旧的删掉。现在向量 id 含内容哈希（`_vector_id`）。
- **跳过一步也要写派生字段**（症状：下一次更新找不到待删向量，向量库开始累积）。内容未变化时跳过重建索引后，新 chunk 行的 `vector_id` 是 NULL。跳过计算 ≠ 跳过记录。
- **事务回滚不会撤销 Markdown 与 Chroma 的写入**（症状：SQLite 与工作区各说各话）。更新路径因此在新向量写成功后才删旧向量，并在 wiki 写入失败时用旧内容重建页面（`_restore_wiki`）。
- **删除必须三处同步**：SQLite 记录、`raw/` 与 `wiki/` 文件、Chroma 向量。删完用 `ls` + `collection.get(where=...)` 双向确认（曾经确认过：删库后向量 0 条、工作区目录消失、库里无残留行）。
- **`_merge_topic` 追加内容时会把标题和 bullet 焊在一起**（症状：`## Sources- [[sources/...]]`）。`_merge_topic` 已加"确保前面有空行"的护栏；`## Sources` 是文件最后一行时最容易触发。
- **删除来源应成对删除"bullet + Tags 行"**：`_drop_source_line` 只删一行，更新路径已改用 `_drop_source_block`，删除路径仍只删一行（已知未修，见本地 TODO）。
- **去重不能只看内容**：同一篇文章被两个平台转载是两份来源，按内容合并会丢掉 URL、平台、作者、抓取时间。

### 6.3 测量与性能（别靠猜）

- **先 profile 再改**（症状：连猜两次都错）。我先怀疑"每条边的 `find` 查找"、再怀疑"React 渲染 800 个节点"，CDP profiler 显示 60% 时间在布局的力计算里。
- **dev 模式 React StrictMode 会把渲染跑两遍**（症状：浏览器测出 489ms，node 里同样代码只要 153ms）。性能结论必须在**生产构建**上取（`npm run build` + `vite preview`）。
- **`vite preview`（sirv）会缓存 `index.html`**（症状：改动前后测出几乎一样的数字，其实测的是旧 bundle）。重新构建后必须重启 preview。
- **`Math.hypot` 比 `Math.sqrt(a*a+b*b)` 慢得多**，热循环里别用（布局的斥力阶段每个 cell 都调一次）。
- **向量检索必须有相似度下限**（`WIKI_QUERY_VECTOR_FLOOR=0.35`），否则任何查询都返回"最不无关"的页面，把"这个库答不了"伪装成有结果。
- **引用数字必须能被复现**：写清构建方式、数据规模与命令；基准脚本要随仓库走（`npm run bench:layout`），别留在 `/tmp`。

### 6.4 测试与验证

- **测试失败时先怀疑测试的前提**（症状：以为是代码 bug，其实是断言写错）。两次是我的断言不成立（分块数随文本长度变化；两个不同标题不会并入同一主题页）；一次是 fixture 算术写错（`good_kept = kept - dropped` 重复扣减），**掩盖**了真实的规则缺陷——那次必须改代码而不是改断言。
- **单元测试默认 `VECTOR_INDEX_ENABLED=false`**，会遮住整条向量路径。涉及向量的改动要在真实 Chroma 上跑一遍并核对条数（`collection.get(where={"document_id": ...})`）。
- **本机 FastAPI 用惰性 `_IncludedRouter`**：遍历 `app.routes` 看不到路径，路由用测试或 `/openapi.json` 验证。
- **读取路径是 `GET /api/documents/{id}`**（不在知识库下），写路径才是 `.../knowledge-bases/{id}/documents/...`。
- **人工验收别只信界面**：先用 `curl` 打 API 确认数据层，再看界面（我遇到过一次"界面 0 条结果"其实是脚本时序问题，API 是好的）。

### 6.5 文档与协作

- `docs/` 每份文档必须中英成对，数字、标识符、结构保持一致；改完用脚本核对，别靠人眼。
- 改能力时同步 README 的能力清单、MCP 工具列表、路线图；`demo/` 截图会漂移。
- `TODO.md` 不随仓库发布，公开文档不要链接它。
- **不顺手改范围外的缺陷**：记进 `TODO.md`（带实测证据与验证方式），而不是悄悄改。
- 提交信息说明"为什么"（尤其是与直觉相反的取舍），改动小、可回滚。

---

## 7. 历史阶段（MVP 交付记录，已完成）

阶段 0–7 是早期 MVP 的交付顺序，保留作为"哪一层是谁建的"的索引；当前实现契约以架构文档与测试为准。

| 阶段 | 交付 | 今天在代码里的位置 |
| --- | --- | --- |
| 0 仓库与环境 | 三模块骨架、`.gitignore`、README、`.env.example` | 仓库根目录 |
| 1 Backend 基础 API | FastAPI + SQLite + 知识库 CRUD | `app/api/knowledge_routes.py` |
| 2 文档上传与分块 | 文本 / `.txt` 上传、`chunk_size=500`、`chunk_overlap=80` | `app/services/document_service.py` |
| 3 Embedding 与 ChromaDB | `sentence-transformers`、向量写入与回写、删除同步 | `app/services/embedding_service.py`、`app/vector/chroma_client.py` |
| 4 语义检索与 SSE | `POST /api/search`、`/api/search/stream`（`start/delta/result/error/done`） | `app/api/search_routes.py` |
| 5 Frontend Demo | 单页工作台、POST SSE 消费 | `frontend/src/App.tsx` |
| 6 MCP Server | 只调 Backend HTTP，工具化检索 | `mcp-server/tools.py` |
| 7 测试与交付 | pytest 套件、README 启动与演示说明 | `backend/tests/`、`mcp-server/tests/` |

---

## 8. 参考

- 产品与用法：[README.md](README.md)（中文：[README_CN.md](README_CN.md)）
- 实现设计与数据归属：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)（EN：`docs/ARCHITECTURE_EN.md`）
- 检索如何打分与选策略：[docs/RETRIEVAL_CN.md](docs/RETRIEVAL_CN.md)（EN：`docs/RETRIEVAL.md`）
- 反馈闭环与失效模式：[docs/SELF_EVOLUTION_CN.md](docs/SELF_EVOLUTION_CN.md)（EN：`docs/SELF_EVOLUTION.md`）
- 本地部署与排错：[docs/LOCAL_INSTALLATION.md](docs/LOCAL_INSTALLATION.md)（EN：`docs/LOCAL_INSTALLATION_EN.md`）
- 外部 Agent 的操作流程：[skills/agentkb-retrieval/SKILL.md](skills/agentkb-retrieval/SKILL.md)
