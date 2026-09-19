# Retrieval strategy proposal — 2026-09-19

- 范围：知识库 1
- 当前默认策略：`auto`（`WIKI_QUERY_DEFAULT_STRATEGY`）
- 参与对比的评分样本：6 条（👍 4 / 👎 2）
- 建议：**auto** — 候选策略（deep、hybrid、planned）保留了全部 👍 引用，但没有改变任何默认策略没改变过的 👎 用例，换上它们只是多付成本。

## 证据

| 策略 | 👍 引用保留 | 相对默认的回归 | 相对默认的新改变 | 平均关联页 | 平均跳数 |
| --- | --- | --- | --- | --- | --- |
| `auto` ← 当前默认 | 14/14 | — | — | 0.33 | 1.17 |
| `local` | 12/14 | 1 | 0 | 0.0 | 1.0 |
| `deep` | 14/14 | 0 | 0 | 0.33 | 2.0 |
| `hybrid` | 14/14 | 0 | 0 | 0.0 | 1.17 |
| `planned` | 14/14 | 0 | 0 | 0.0 | 1.17 |

## 逐条差异（相对当前默认策略）

### `local`

- 👍 `怎么分散风险` — 引用保留 2/4，直接 2 关联 0，跳数 1

## 这份证据不能说明什么

- 没有标注答案，「首条证据变了」不等于「变得对了」。
- 只覆盖有人评过的问题，而人们倾向于只评判那些感觉不对的问题。
- 工作区里根本没有的材料，任何策略都召不回来，它们看起来会一样好。
- 因此上面这一条建议只是「值得人工看一眼」，不是「应该合并」。

## 如果决定推进

1. 改 `backend/app/config.py` 的默认值或 `WIKI_QUERY_DEFAULT_STRATEGY`，并同步 `backend/.env.example`。
2. 若涉及新行为，在 `backend/app/services/retrieval_strategy.py` 里补策略定义与测试。
3. 跑 `PYTHONPATH=. python -m pytest tests -q` 与 `PYTHONPATH=. python scripts/replay_queries.py --rating 1`。
4. 在本文件末尾记录结论并提交，让这次改动可回滚、可追溯。
