# AgentKB Architecture

**中文** · [English](ARCHITECTURE_EN.md)

本文档描述 AgentKB 当前实现的架构、数据边界和接口约束。它是项目实现设计的依据；[llm-wiki.md](../llm-wiki.md) 保留为产品方法论和长期演进参考，不替代本文档的具体契约。

## 1. 目标与边界

AgentKB 是面向 AI Agent 的本地优先 Markdown 知识库。它不把向量库或模型对话记录当作长期事实来源，而是将可审阅、可编辑、可 Git 管理的 Markdown 工作区作为长期知识载体。

当前版本提供两种查询入口：

- 页面优先：在已维护的 wiki 页面中排序并回答，适合长期积累后的主题、结论和关联。
- 原始资料 RAG：在摄取时保存的文档分块中做向量检索，适合回看原始材料和补充依据。

Web 工作台的两种查询入口均可选用 OpenAI-compatible 模型做基于证据的综合回答。MCP 则默认提供不含模型生成的检索证据，由调用它的 Agent 进行任务推理和最终作答；只有显式调用 MCP 综合工具时才会请求 Backend 模型。模型不是系统可用性的前提：默认不开启模型；没有模型配置、模型调用失败或无检索证据时，系统仍返回本地确定性页面回答或原始检索结果。

## 2. 核心原则

1. Markdown 工作区是长期事实载体。`raw/` 保留不可变来源快照，`wiki/` 保存可维护页面；SQLite、ChromaDB 和模型输出都不能替代它。
2. 派生数据必须可重建。`index.md`、链接图谱、向量索引和查询结果都是从 Markdown、SQLite 文档记录或二者组合派生而来。
3. 原始来源不可被 wiki 摘要覆盖。摄取、总结和模型综合都只能新增或维护 wiki 层，不能改写 `raw/sources/`。外部来源的完整正文必须先落到 SQLite 和 raw 快照，再生成分块与向量。
4. 模型回答必须绑定检索证据。模型上下文只包含当前查询命中的页面或文档片段；提示词要求用 `[1]`、`[2]` 等来源编号标注事实依据。
5. 模型故障必须可见且可回退。响应通过 `answer_mode` 与 `model_error` 表示是否实际获得模型回答及回退原因。
6. 默认部署假设为受信任的本地工作台。模型配置接口不包含认证机制，公开部署前必须由部署层增加身份验证、访问控制和 HTTPS。

## 3. 系统结构

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

### 3.1 职责划分

| 组件 | 负责内容 | 不负责内容 |
| --- | --- | --- |
| Backend | 知识库、来源摄取、wiki 文件维护、检索、模型综合、配置状态 | 在 Frontend 或 MCP 中复制业务规则 |
| Markdown workspace | 可审阅的来源关系、页面、索引、日志 | API Key、运行时配置、向量数据 |
| SQLite | 知识库、文档、分块和向量标识等应用元数据 | 取代 wiki 作为知识正文 |
| ChromaDB | 文档分块 embedding 的相似度召回 | 唯一知识来源或长期事实存储 |
| Frontend | 浏览、编辑、摄取、查询和模型设置 | 保存 API Key 或重新实现检索 |
| MCP Server | 将 Backend HTTP 工作流暴露为 Agent 工具；基础查询强制检索模式；按需请求 Backend 综合 | 直接读写 wiki、调用模型 Provider 或替外部 Agent 完成最终推理 |

## 4. 知识工作区

每个知识库都位于 `WIKI_ROOT_DIR/kb-<id>/`，目录约定如下：

```text
kb-<id>/
├── .wiki-schema.md      # 页面和维护约定
├── purpose.md           # 知识库目标与关键问题
├── raw/
│   └── sources/         # 不可变 Markdown 来源快照
├── wiki/
│   ├── overview.md      # 入口页
│   ├── sources/         # 来源摘要页
│   ├── topics/          # 持续维护的主题页
│   ├── entities/        # 预留实体页
│   └── queries/         # 用户选择保存的查询结晶
├── index.md             # 从页面生成的导航索引
└── log.md               # append-only 活动日志
```

页面使用 `[[path/without-extension]]` 建立链接。链接图谱、入链数、孤立页和断链检查均由页面内容派生，不能单独修改图谱来改变事实关系。

### 4.1 摄取流程

```text
文本、.txt 或本地外部来源快照
  -> documents / document_chunks（SQLite）
  -> 外部快照元数据 + content_hash（SQLite）
  -> raw/sources/ 不可变快照
  -> wiki/sources/ 来源页
  -> wiki/topics/ 主题页更新
  -> index.md 重建 + log.md 追加
  -> 可选：embedding + ChromaDB 分块索引
```

当前摄取分两步。第一步是确定性的、在数据库事务内完成：写 `raw/sources/` 不可变快照、`wiki/sources/` 来源页、一个确定性主题页，并把来源追加到该页的 `## Sources` 列表，最后重建 `index.md` 并追加 `log.md`。第二步在事务提交后尽力而为地运行：

- 未配置模型时不做任何额外处理，第一步的结果就是最终结果。
- 配置了模型时，模型从最多 5 个候选主题页中选定归属（或选择新建），并重写该页的 `## Evolving synthesis`。模型只能返回候选项里的 slug 或 `NEW`，返回其他值一律当作新建，因此模型无法创建或重命名文件。
- 模型选择与第一步不同的归属时，来源链接会被搬到目标页；如果来源页只剩下空壳（最后一个来源被搬走），该页会被删除。
- 模型失败、超时或抛异常，都只写一条日志（`Skipped:` / `Skipped after an error:`），不会回滚已经提交的摄取。这也是把第二步放在事务之外的原因：先前的实现会在未提交事务里等待模型响应，最长阻塞 `LLM_TIMEOUT_SECONDS`。

`sources:` front matter 与 `## Sources` 列表始终由代码维护，模型只写 `## Evolving synthesis` 正文，`summary` 字段由该正文首行推导。

没有模型时的归属规则是刻意保守的：只有标题完全相同，或者新来源标题里有**两个**独立词命中已有主题页标题时，才并入该页；只共享一个通用词（`AI`、`ETF`）不会合并。所以「AI 制药进展」不会被并入「AI 服务器 MLCC 产能挤出效应」。

将模型生成的维护建议变成可审阅的页面变更，仍是后续能力。

### 4.2 外部来源快照契约

`POST /api/knowledge-bases/{knowledge_base_id}/documents/source-snapshot` 用于将已经在本地取得的外部来源纳入知识库。Backend 不负责模拟登录或抓取 X / 小红书；这样可以让服务端保持可部署、可测试，也避免把平台登录态混进应用进程。

快照会在 `documents` 表保留完整 `content`，并记录 `content_hash`、`source_url`、`source_platform`、作者、账号、发布时间、抓取时间、披露信息和保存策略。`source_policy` 有三种值：

| 策略 | 含义 | 是否适合原文 RAG |
| --- | --- | --- |
| `full_text` | 当前快照包含采集到的完整正文，不自动包含回复、引用帖或评论 | 是 |
| `excerpt` | 只保留明确标注为节选的正文 | 是，但模型和用户必须知道内容不完整 |
| `link_only` | 仅保留链接和人工说明，不声称拥有原文 | 否，不应把说明当作原帖正文 |

SQLite 是应用查询和一致性索引，`raw/sources/` 是可审阅的 Markdown 原文快照，Chroma 是可删除、可重建的分块索引。Chroma metadata 同步记录来源 URL、平台、作者、策略和内容哈希，因此 RAG 命中可以回到原始来源；向量库不能作为唯一事实来源。

仓库提供 `backend/scripts/import_source_snapshots.py`，输入 JSON 数组或 JSONL。它只读取本地文件或 stdin，不依赖 OpenCLI；外部采集工具由维护者在受控环境中单独运行。完整第三方正文只写入被 Git 忽略的运行时目录，不进入仓库。

## 5. 查询与回答

### 5.1 页面优先

`POST /api/knowledge-bases/{knowledge_base_id}/wiki/query`

1. 读取 `wiki/` 中的可查询页面。
2. 把问题切成检索词：拉丁词保持整词，连续汉字切成**重叠二元组**（`如何配置资产` → `如何/何配/配置/置资/资产`）。这一步解决语序与虚词差异，不解决同义词——同义词要靠向量。
3. 用 **BM25** 对候选页面打分：词频饱和 + 按页面长度归一化，再除以"全部检索词都命中的理想分"映射到 0..1。纯词频计数会让最长的页面永远赢。
4. `score = 0.15 + 0.55 × 词法相关度 + 0.30 × 标题覆盖度`，封顶 0.99。
5. 沿页面链接扩展：命中页面的出链与反链页面作为补充证据加入结果，按被多少个命中页面连到排序，并以 `related=true` 标记。跳数与关联页上限由**检索策略**决定（见 5.6）。直接命中始终排在前面，确定性回答与保存的 `wiki/queries/` 页面只使用直接命中。
6. 生成页面列表与确定性回答。
7. 若模型已启用且有匹配页面，将排序后的完整页面内容按上下文上限组装为证据，调用 OpenAI-compatible Chat Completions；扩展进来的页面在证据里标注为 linked page，`purpose.md` 的正文作为工作区意图写进 system prompt，不作为引用。
8. 若用户提供 `save_as`，将最终回答和引用页面保存到 `wiki/queries/`；无论最终回答来自模型还是本地规则，都保留来源列表。

返回约定：

- `answer_mode="llm"`：`answer` 是模型基于当前页面证据的综合结果，`model` 为实际模型名。
- `answer_mode="deterministic"`：`answer` 是本地页面排序与模板回答。
- `model_error` 非空：模型未能生成回答，本地回答仍有效。
- `results[].related=true`：该页面不是直接命中，而是沿命中页面的链接扩展进来的。

### 5.2 原始资料 RAG

`POST /api/search`

1. 使用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` 生成查询 embedding。
2. 在 ChromaDB 中按 `knowledge_base_id` 过滤并召回相似分块。
3. 返回文档、分块、相似度分数、检索顺序以及外部来源元数据。
4. 若模型已启用且有召回结果，按该顺序将分块作为证据交给模型综合。

返回约定：

- `answer_mode="llm"`：同时返回 `answer` 和完整 `results`，结果中的第 N 项对应模型引用 `[N]`。
- `answer_mode="retrieval"`：只返回原始检索结果；`answer` 为 `null`。
- `model_error` 非空：模型失败，但 `results` 仍可用于人工核验。

`POST /api/search/stream` 是历史兼容接口，当前仍只发送检索事件（`start`、`delta`、`result`、`error`、`done`），不提供模型 token 流。模型综合的流式响应需要单独设计事件契约后再实现。

### 5.3 证据与引用

模型服务接收的系统提示要求：

- 使用用户问题的语言回答。
- 仅根据本次注入的页面或分块证据陈述事实。
- 证据不足时明确说明不足。
- 用 `[1]`、`[2]` 等编号引用。

AgentKB 无法从技术上保证任意 Provider 完全不产生幻觉，因此引用是可追溯性的辅助机制，不是事实正确性的替代。前端会展示与编号相同顺序的页面或原始片段；对于外部快照，还会显示平台、作者、保存策略和可点击的原帖链接，用户仍应打开来源核对关键结论。

### 5.4 MCP 查询策略

MCP 的目标是让 Codex、Claude Code 等外部 Agent 接入可追溯知识，而非在每次工具调用中串联第二个生成模型。为避免额外延迟、费用、摘要信息损失和双重生成，工具分为两类：

| MCP 工具 | Backend 请求 | 模型行为 | 典型用途 |
| --- | --- | --- | --- |
| `query_wiki` | `POST .../wiki/query` + `llm.enabled=false` | 固定不调用 AgentKB LLM，返回确定性页面回答和排序页面 | Agent 获取长期维护的 Markdown 知识 |
| `search_knowledge_base` | `POST /api/search` + `llm.enabled=false` | 固定不调用 AgentKB LLM，返回原始分块和相似度 | Agent 查找原始资料依据 |
| `synthesize_knowledge` | 同上，根据 `source_mode` 选择页面或 RAG 入口 + `llm.enabled=true` | 显式尝试 Backend 已配置的模型；失败时仍返回本地回退结果和 `model_error` | 调用方明确委托 AgentKB 做受限证据综合 |

`synthesize_knowledge` 不接收 Provider 地址、模型名或 API Key。它只使用 Backend 的有效配置，仍遵循第 6 节的优先级和密钥边界。调用方 Agent 应优先使用基础查询工具并自行整合证据；只有任务需要单独的、可引用的知识库综合答案时才调用显式综合工具。

### 5.5 回答反馈

`POST /api/knowledge-bases/{knowledge_base_id}/feedback`

回答下方可以点「有用」或「没用」；选「没用」时展开一个可选的说明输入框。这是**检索策略唯一的评估信号来源**，因此每条记录都保留足够的信息，以便日后重放这次查询：

| 字段 | 用途 |
| --- | --- |
| `mode` | `wiki`（页面优先）或 `rag`（原始资料） |
| `query` / `answer` | 被评价的问题与当时的回答（回答截断保存，便于人工复核失败样例） |
| `rating` | 只接受 `1` 或 `-1` |
| `note` | 贬低时可选的原因 |
| `answer_mode` / `model` | 这次回答是确定性模板还是模型综合、用的哪个模型 |
| `strategy_id` | 这次回答用的是哪个检索策略，供回放对比 |
| `source_paths` | 当时展示的引用页面路径，或 `document:<id>#<chunk>` 片段 |
| `bad_paths` | 点「没用」时可以勾选**哪几条引用不对**（`source_paths` 的子集）；👍 时提交该字段会被拒绝，而不是被静默丢弃 |

存储与边界：

- 反馈是**业务数据**，写入 SQLite 的 `query_feedback`；它不是知识，**不得写入 wiki 页面或 Markdown 工作区**，也不改变当次回答。
- 记录是 append-only：用户改主意会产生新行，而不是覆盖旧行，这样"判断变化"本身也是可分析的数据。
- `GET .../feedback` 读回信号，并返回 `positive` / `negative` 汇总计数。工作台写入评分时会带上产生该回答的策略，MCP 通过 `list_answer_feedback` 暴露读取侧；消费方见 5.7 与 5.8。
- 工作台有两处读/写界面：回答下方（👍/👎 + 原因 + 「哪条引用不对」勾选）与**「反馈」纸**（时间倒序的评分列表、👍/👎 汇总、被指认的引用，可把问题填回查询框重跑）。两者都不写 wiki，反馈视图是只读的。
- `bad_paths` 让回放从**集合级**判断（"当时的引用还在吗"）升到**页面级**判断（"那条被指认的引用还在吗"）。它仍然不是标注答案，措辞上不能把"隐藏了被指认的引用"说成"答案对了"。



### 5.6 检索策略

检索行为的**集合**是代码，检索的**选择**是数据：

| 策略 | 跳数 | 关联页上限 | 向量 | 模型改写 |
| --- | --- | --- | --- | --- |
| `auto`（默认） | 自适应 | 3 | 否 | 否 |
| `local` | 1 | 0（不扩展） | 否 | 否 |
| `deep` | 2 | 6 | 否 | 否 |
| `hybrid` | 自适应 | 3 | 是 | 否 |
| `planned` | 自适应 | 3 | 是 | 是 |

- `auto` 的自适应规则：最高直接命中分低于 `WIKI_QUERY_DEEP_THRESHOLD`（默认 0.5）时允许走第二跳，否则只走一跳。每一跳把继承的置信度减半，所以两跳之外的页面永远不会压过一跳内的页面。
- `GET /api/retrieval-strategies` 只读列出策略与取舍。新增策略必须改代码、走评审和测试——这是防止"模型悄悄改掉检索方式"的闸门。
- `POST .../wiki/query` 的 `strategy` 字段选择策略；省略则用 `WIKI_QUERY_DEFAULT_STRATEGY`（默认 `auto`）。未知策略返回 400 并列出可用 id。
- 响应回报**实际发生了什么**：`strategy`、`hops`、`vectors`、`planned`。这四个字段会随反馈一起入库，因此"哪个配置产生了哪个答案"是可追溯的。
- 向量侧有相似度下限 `WIKI_QUERY_VECTOR_FLOOR`（默认 0.35）。没有下限时任何问题都会拖回"最不无关"的页面，看着像召回、实际是噪声，并且会掩盖"这个工作区回答不了这个问题"这一事实。
- 向量按工作区缓存，页面文件时间戳变化时重建；embedding 不可用时自动退回纯词法排序，并在响应里如实报告 `vectors=false`。
- `planned` 只让模型把问题改写成检索用词（一次调用，单行 `KEYWORDS:` 输出），排序仍然完全确定性；模型未启用或失败时退回原问题，并报 `planned=false`。

### 5.7 回放与评估

`backend/scripts/replay_queries.py` 用记录下来的反馈重放历史问题，横向比较策略：

- 对 👍 用例：新策略是否仍能找到当时的引用页面（丢了就是回归）。
- 对 👎 用例：新策略是否真的改变了结果（返回同样的错页面就不算改进）。
- 代价：平均关联页数与平均跳数。
- 回放传 `record_activity=False`，因此反复回放不会往 `log.md` 里灌几百行活动记录。

它**只做对比，不做打分**：没有标注答案时，任何"哪个策略更好"的结论都必须由人（或外部 Agent）看差异后判断。`--json` 输出给 Agent 消费。

### 5.8 提案与晋升

`backend/scripts/propose_strategy_change.py` 把回放证据写成一份可评审的 Markdown 提案，落在仓库根的 `proposals/`：

- 建议**相对当前默认策略**判断：候选只有在"不丢任何 👍 引用"且"改变了默认策略没改变过的 👎 用例首条证据"时才会被提出；否则建议保持现状。拿候选和"当时的记录"比会把默认策略早就做过的改变算成候选的功劳。
- `--check` 是给 CI/提交前用的：只回放当前默认策略，一旦它丢掉了被点赞的引用就以退出码 1 失败。
- 晋升就是一次普通的 git 改动（改 `wiki_query_default_strategy` 或策略定义 + 测试），因此审计与回滚都是 `git log` / `git revert`。

过程与理由见 [SELF_EVOLUTION.md](SELF_EVOLUTION.md)，检索机制细节见 [RETRIEVAL.md](RETRIEVAL.md)。

## 6. 模型配置

### 6.1 配置来源与优先级

模型配置为全局 Backend 运行配置，而不是单个知识库的属性。有效配置按以下优先级解析：

```text
单次 API 请求中的 llm 覆盖
  > 当前 Backend 进程的网页运行时配置
  > 环境变量或 backend/.env
  > 代码默认值
```

部署配置使用以下环境变量：

| 变量 | 含义 | 默认值 |
| --- | --- | --- |
| `LLM_ENABLED` | 是否尝试模型综合 | `false` |
| `LLM_BASE_URL` | OpenAI-compatible API 根路径，不含 `/chat/completions` | `https://api.openai.com/v1` |
| `LLM_API_KEY` | Provider 密钥 | 空 |
| `LLM_MODEL` | Chat Completions 模型名 | 空 |
| `LLM_TEMPERATURE` | 生成温度，范围 0 至 2 | `0.2` |
| `LLM_MAX_TOKENS` | 最大输出 token 数 | `1200` |
| `LLM_TIMEOUT_SECONDS` | Provider 请求超时秒数 | `60` |
| `LLM_CONTEXT_MAX_CHARS` | 注入模型的检索证据最大字符数 | `12000` |

### 6.2 网页端运行时配置

工作台的“配置模型”会调用：

| API | 用途 | 密钥行为 |
| --- | --- | --- |
| `GET /api/llm/config` | 读取有效的非敏感状态 | 只返回 `api_key_configured`，永不返回 key 本身 |
| `PUT /api/llm/config` | 替换当前 Backend 进程的运行时配置 | key 只留在该 Backend 进程内存 |
| `POST /api/llm/test` | 将提交字段叠加到当前有效配置后请求模型并验证连接 | 不改变已生效的运行时配置 |

网页不会将 API Key 写入 `localStorage`、Markdown 或 SQLite。进程重启后，运行时配置被清除，系统重新读取 `.env` 或环境变量。运行时配置的保存是进程级行为，因此同一 Backend 的其他本地客户端会共享该配置；多用户或公网部署必须改为有认证的用户/工作区配置模型。

### 6.3 OpenAI-compatible 契约

Backend 对 Provider 发起：

```http
POST {LLM_BASE_URL}/chat/completions
Authorization: Bearer {LLM_API_KEY}
Content-Type: application/json
```

请求体包含 `model`、`messages`、`temperature`、`max_tokens` 和 `stream: false`。当前实现读取标准的 `choices[0].message.content` 响应字段。Provider 超时、网络错误、HTTP 4xx/5xx、空回答或格式不兼容都会转化为本地检索回退，而不是让查询整体失败。

## 7. 对外 API 契约

| 区域 | 主要接口 | 说明 |
| --- | --- | --- |
| 知识库 | `/api/knowledge-bases` | CRUD；创建时初始化 Markdown 工作区 |
| 来源 | `/api/knowledge-bases/{id}/documents/text`、`/file`、`/source-snapshot` | 摄取文本、`.txt` 或本地外部来源快照，维护工作区和可选向量索引 |
| 页面 | `/api/knowledge-bases/{id}/wiki/pages` | 浏览、读取和保存 Markdown 页面 |
| Wiki 查询 | `/api/knowledge-bases/{id}/wiki/query` | 页面排序、可选模型综合、可选保存为 query 页面 |
| RAG 查询 | `/api/search` | 向量召回、可选模型综合，始终返回检索证据 |
| 模型设置 | `/api/llm/config`、`/api/llm/test` | 有效状态、进程级设置和连接验证 |
| MCP | `mcp-server/tools.py` | 基础查询明确禁用模型；`synthesize_knowledge` 才明确启用 Backend 模型综合 |

响应字段新增时应保持本地模式兼容：`SearchResponse.answer`、`model`、`model_error` 均允许为空；旧客户端只读取 `results` 时仍可工作。

## 8. 失败处理与可观测性

- 来源、知识库或向量检索失败：HTTP API 返回明确的 4xx/5xx 错误。
- 外部来源重复导入：本地导入脚本以 URL + `content_hash` 跳过完全相同的快照；正文变化会生成新的文档记录，保留不同版本的可追溯性。
- 模型未启用：不发起 Provider 请求，正常返回本地结果。
- 模型配置缺失、超时、连接失败、HTTP 错误或格式错误：查询成功返回，带 `model_error`，并以本地结果回退。
- wiki lint：检查断链、孤立页和缺少摘要；结果写入 API 响应，活动写入 `log.md`。
- 查询与摄取：写入 `log.md`，但日志不得写入 API Key 或模型请求正文。

## 9. 当前非目标与后续方向

当前不包含：

- 模型生成的 token 流、增量回答事件或取消协议。
- 模型自动修改多页 wiki、变更差异预览、审批和回滚工作流。
- 多用户的 Provider 密钥管理、权限模型、审计和租户隔离。
- PDF、DOCX、URL、图片等来源适配器。
- rerank、原生 Provider SDK 适配和模型目录。

已经先行交付并在上文记录：带自适应链接扩展与可选向量召回的命名检索策略（5.6）、作为评估信号的回答反馈（5.5）、以及把信号变成受评审改动的回放与提案闭环（5.7、5.8）。

余下部分的推荐顺序：先定义模型流式 API 事件契约，且不要让旧 SSE 路径吞掉模型回答；再加入可审阅的 wiki 变更提案；最后扩展检索、来源适配和多用户部署能力。

## 10. 变更要求

涉及下列区域的改动必须同步更新本文档和对应测试：

- 工作区目录、页面格式或 Markdown 链接规则。
- 查询响应字段、引用排序、回退行为或 SSE 事件。
- 模型 Provider 请求格式、配置优先级、密钥处理或外部部署假设。
- MCP 与 Backend API 之间的契约，包括基础工具必须禁用模型、模型综合必须显式调用的边界。

提交前至少运行：

```bash
cd backend && PYTHONPATH=. python -m pytest -q
cd ../mcp-server && PYTHONPATH=. python -m pytest -q
cd ../frontend && npm run build
git diff --check
```
