from fastapi.testclient import TestClient

from pathlib import Path

from app.config import get_settings
from app.db.schemas import LLMConfigurationInput
from app.services import llm_service
from tests.conftest import create_kb


def test_create_and_ingest_builds_wiki_workspace(client: TestClient):
    kb = create_kb(client, "AI 研究")

    response = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={
            "title": "检索增强生成",
            "content": "检索增强生成把外部来源带入模型上下文。它需要可靠的来源和可追溯的引用。",
            "tags": ["RAG", "研究"],
        },
    )

    assert response.status_code == 201
    workspace = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/status").json()
    assert workspace["initialized"] is True
    assert workspace["source_count"] == 1
    assert workspace["topic_count"] == 1

    pages = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages?page_size=50").json()
    paths = {item["path"] for item in pages["items"]}
    assert "wiki/sources/1-检索增强生成.md" in paths
    assert "wiki/topics/检索增强生成.md" in paths


def test_wiki_query_can_crystallize_answer(client: TestClient):
    kb = create_kb(client)
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "春天", "content": "春天带来花草和新的生长。"},
    )

    response = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "花草", "save_as": "花草主题总结"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["results"][0]["title"] == "春天"
    assert data["saved_path"] == "queries/花草主题总结.md"
    saved = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/queries/花草主题总结.md")
    assert saved.status_code == 200
    assert "花草" in saved.json()["content"]


def test_wiki_lint_reports_broken_links(client: TestClient):
    kb = create_kb(client)
    response = client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/test.md",
        json={"path": "wiki/topics/test.md", "content": "# Test\n\n[[missing-page]]\n"},
    )
    assert response.status_code == 200

    lint = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/lint")
    assert lint.status_code == 200
    assert any(issue["code"] == "broken_link" for issue in lint.json()["issues"])


def test_wiki_page_path_cannot_escape_workspace(client: TestClient):
    kb = create_kb(client)
    response = client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/../outside.md",
        json={"path": "wiki/../outside.md", "content": "# nope"},
    )
    assert response.status_code in {400, 404}


# ---------------------------------------------------------------------------
# Link-following retrieval
# ---------------------------------------------------------------------------


def test_wiki_query_follows_links_one_hop(client: TestClient):
    kb = create_kb(client, "现代文学")
    base = f"/api/knowledge-bases/{kb['id']}/wiki/pages"
    client.put(
        f"{base}/wiki/topics/春天.md",
        json={"path": "wiki/topics/春天.md", "content": "# 春天\n\n春天带来花草和新的生长。\n\n[[wiki/topics/物候笔记]]\n"},
    )
    client.put(
        f"{base}/wiki/topics/物候笔记.md",
        json={"path": "wiki/topics/物候笔记.md", "content": "# 物候笔记\n\n记录气温与候鸟的观测方法。\n"},
    )

    response = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "花草"})

    assert response.status_code == 200
    results = response.json()["results"]
    direct = [item for item in results if not item["related"]]
    related = [item for item in results if item["related"]]
    assert direct[0]["path"] == "wiki/topics/春天.md"
    neighbour = next(item for item in related if item["path"] == "wiki/topics/物候笔记.md")
    assert neighbour["score"] == round(direct[0]["score"] * 0.5, 4)


def test_wiki_query_link_expansion_can_be_disabled(client: TestClient, monkeypatch):
    monkeypatch.setenv("WIKI_QUERY_NEIGHBOUR_LIMIT", "0")
    get_settings.cache_clear()
    kb = create_kb(client, "现代文学")
    base = f"/api/knowledge-bases/{kb['id']}/wiki/pages"
    client.put(f"{base}/wiki/topics/春天.md", json={"path": "wiki/topics/春天.md", "content": "# 春天\n\n春天带来花草。\n\n[[wiki/topics/物候笔记]]\n"})
    client.put(f"{base}/wiki/topics/物候笔记.md", json={"path": "wiki/topics/物候笔记.md", "content": "# 物候笔记\n\n记录观测方法。\n"})

    results = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "花草"}).json()["results"]

    assert results
    assert all(not item["related"] for item in results)


# ---------------------------------------------------------------------------
# Deterministic topic routing (no model configured)
# ---------------------------------------------------------------------------


def test_same_title_source_joins_the_existing_topic_page(client: TestClient):
    kb = create_kb(client, "AI 研究")
    for content in ("检索增强生成把外部来源带入上下文。", "同一主题的第二篇材料。"):
        client.post(
            f"/api/knowledge-bases/{kb['id']}/documents/text",
            json={"title": "RAG 检索策略", "content": content, "synthesize_topic": False},
        )

    pages = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages?page_size=50").json()["items"]
    topics = [item for item in pages if item["page_type"] == "topic"]
    assert len(topics) == 1
    assert 'sources: ["1-rag-检索策略", "2-rag-检索策略"]' in topics[0]["content"]
    assert topics[0]["content"].count("- [[sources/") == 2


def test_two_matching_title_tokens_join_one_topic_page(client: TestClient):
    """A revised edition of the same topic repeats two title tokens, which is evidence enough."""
    kb = create_kb(client, "AI 研究")
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略", "content": "检索增强生成把外部来源带入上下文。", "synthesize_topic": False},
    )
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略：2026 补充", "content": "新增了重排与评测部分。", "synthesize_topic": False},
    )

    pages = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages?page_size=50").json()["items"]
    topics = [item for item in pages if item["page_type"] == "topic"]
    assert len(topics) == 1
    assert topics[0]["content"].count("- [[sources/") == 2


def test_generic_shared_word_does_not_merge_topics(client: TestClient):
    """The regression that motivated model-side routing: "AI" alone is not a topic match."""
    kb = create_kb(client, "研究笔记")
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "Serenity：AI 服务器 MLCC 产能挤出效应", "content": "被动元件产能紧张。", "synthesize_topic": False},
    )
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "AI 制药进展", "content": "小分子设计进入临床。", "synthesize_topic": False},
    )

    pages = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages?page_size=50").json()["items"]
    topic_paths = {item["path"] for item in pages if item["page_type"] == "topic"}
    assert topic_paths == {
        "wiki/topics/serenity-ai-服务器-mlcc-产能挤出效应.md",
        "wiki/topics/ai-制药进展.md",
    }


def test_unrelated_source_gets_its_own_topic_page(client: TestClient):
    kb = create_kb(client, "AI 研究")
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略", "content": "检索增强生成把外部来源带入上下文。"},
    )
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "资产配置入门", "content": "先建立应急金，再考虑权益类资产。"},
    )

    pages = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages?page_size=50").json()["items"]
    topic_paths = {item["path"] for item in pages if item["page_type"] == "topic"}
    assert topic_paths == {"wiki/topics/rag-检索策略.md", "wiki/topics/资产配置入门.md"}


# ---------------------------------------------------------------------------
# Model-backed topic maintenance
# ---------------------------------------------------------------------------


def configure_model(monkeypatch, reply) -> list[str]:
    """Enable a stub model and return the list of prompts it receives."""
    prompts: list[str] = []

    def fake_chat(config, messages):
        # The whole exchange is captured: system messages carry the workspace intent.
        prompts.append("\n".join(message["content"] for message in messages))
        return reply(config, messages) if callable(reply) else reply

    llm_service.clear_runtime_config()
    monkeypatch.setattr(llm_service, "_chat_completion", fake_chat)
    llm_service.update_runtime_config(
        LLMConfigurationInput(
            enabled=True,
            base_url="http://model.test/v1",
            api_key="test-key",
            model="test-model",
            temperature=0,
            max_tokens=256,
            timeout_seconds=5,
        )
    )
    return prompts


def test_model_files_a_source_into_an_existing_topic_page(client: TestClient, monkeypatch):
    kb = create_kb(client, "AI 研究")
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略", "content": "检索增强生成把外部来源带入上下文。", "synthesize_topic": False},
    )

    prompts = configure_model(
        monkeypatch,
        lambda config, messages: "TARGET: rag-检索策略\n\n# 不该保留的标题\n\nRAG 用外部来源补足上下文，代价是可追溯的引用。\n\n## Sources\n\n- 模型不应维护这一段",
    )
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 评测方法", "content": "实践中需要评估召回率与重排效果。"},
    )

    assert "slug: rag-检索策略" in prompts[0]
    pages = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages?page_size=50").json()["items"]
    topics = [item for item in pages if item["page_type"] == "topic"]
    assert len(topics) == 1, "the model asked for the existing topic page, so the new one must be gone"
    content = topics[0]["content"]
    assert "RAG 用外部来源补足上下文" in content
    assert "不该保留的标题" not in content
    assert "模型不应维护这一段" not in content
    assert content.count("## Sources") == 1
    assert 'sources: ["1-rag-检索策略", "2-rag-评测方法"]' in content
    assert "[[sources/1-rag-检索策略]]" in content and "[[sources/2-rag-评测方法]]" in content
    assert topics[0]["summary"] == "RAG 用外部来源补足上下文，代价是可追溯的引用。"

    llm_service.clear_runtime_config()


def test_model_can_start_a_new_topic_page(client: TestClient, monkeypatch):
    kb = create_kb(client, "AI 研究")
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略", "content": "检索增强生成把外部来源带入上下文。", "synthesize_topic": False},
    )

    configure_model(monkeypatch, "TARGET: NEW\n\n资产配置先建立应急金。相关主题见 [[topics/rag-检索策略]]。")
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "资产配置入门", "content": "先建立应急金，再考虑权益类资产。"},
    )

    topic = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/资产配置入门.md").json()
    assert "资产配置先建立应急金" in topic["content"]
    graph = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/graph").json()
    edges = {(edge["source"], edge["target"]) for edge in graph["edges"]}
    assert ("wiki/topics/资产配置入门.md", "wiki/topics/rag-检索策略.md") in edges

    results = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "应急金"}).json()["results"]
    assert any(item["related"] and item["path"] == "wiki/topics/rag-检索策略.md" for item in results)

    llm_service.clear_runtime_config()


def test_model_cannot_file_a_source_outside_the_candidates(client: TestClient, monkeypatch):
    kb = create_kb(client, "AI 研究")
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略", "content": "检索增强生成把外部来源带入上下文。", "synthesize_topic": False},
    )

    configure_model(monkeypatch, "TARGET: ../../etc/passwd\n\n试图写到候选项之外。")
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "资产配置入门", "content": "先建立应急金。"},
    )

    pages = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages?page_size=50").json()["items"]
    topic_paths = {item["path"] for item in pages if item["page_type"] == "topic"}
    assert topic_paths == {"wiki/topics/rag-检索策略.md", "wiki/topics/资产配置入门.md"}

    llm_service.clear_runtime_config()


def test_synthesis_failure_keeps_the_ingest_and_the_source_list(client: TestClient, monkeypatch):
    def failing_chat(config, messages):
        raise llm_service.LLMRequestError("模型服务请求超时，已保留本地检索结果。")

    configure_model(monkeypatch, failing_chat)
    kb = create_kb(client, "AI 研究")
    response = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略", "content": "检索增强生成把外部来源带入上下文。"},
    )

    assert response.status_code == 201
    topic = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/rag-检索策略.md").json()
    assert "[[sources/1-rag-检索策略]]" in topic["content"]
    log = (Path(get_settings().wiki_root_dir) / f"kb-{kb['id']}" / "log.md").read_text(encoding="utf-8")
    assert "Skipped: 模型服务请求超时" in log

    llm_service.clear_runtime_config()


def test_ingest_survives_an_unexpected_synthesis_crash(client: TestClient, monkeypatch):
    """Topic maintenance runs after the commit, so it can never undo an ingest."""

    def exploding(**kwargs):
        raise RuntimeError("unexpected bug in topic maintenance")

    kb = create_kb(client, "AI 研究")
    monkeypatch.setattr(llm_service, "synthesize_topic", exploding)
    response = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略", "content": "检索增强生成把外部来源带入上下文。"},
    )

    assert response.status_code == 201
    assert response.json()["title"] == "RAG 检索策略"
    assert client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/sources/1-rag-检索策略.md").status_code == 200
    log = (Path(get_settings().wiki_root_dir) / f"kb-{kb['id']}" / "log.md").read_text(encoding="utf-8")
    assert "Skipped after an error: unexpected bug in topic maintenance" in log


def test_synthesis_keeps_user_sections_and_source_block_shape(client: TestClient, monkeypatch):
    path = "wiki/topics/rag-检索策略.md"
    kb = create_kb(client, "AI 研究")
    client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/{path}",
        json={
            "path": path,
            "content": "---\ntype: topic\ntitle: RAG 检索策略\nsummary: 手写的摘要\n---\n\n# RAG 检索策略\n\n## Evolving synthesis\n\n旧的综合。\n\n## 我的笔记\n\n这一段不能被模型重写吞掉。\n\n## Sources\n\n",
        },
    )
    configure_model(monkeypatch, "TARGET: NEW\n\n重写后的综合。")
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略", "content": "检索增强生成把外部来源带入上下文。"},
    )

    content = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages/{path}").json()["content"]
    assert "重写后的综合。" in content
    assert "旧的综合。" not in content
    assert "这一段不能被模型重写吞掉。" in content
    assert content.index("## 我的笔记") > content.index("重写后的综合。")
    assert content.count("## Sources") == 1
    assert "\n\n\n" not in content

    llm_service.clear_runtime_config()


def test_workspace_purpose_reaches_the_model(client: TestClient, monkeypatch):
    kb = create_kb(client, "AI 研究", "专注检索增强生成的研究笔记")
    prompts = configure_model(monkeypatch, "TARGET: NEW\n\n综合内容。")
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略", "content": "检索增强生成把外部来源带入上下文。"},
    )
    assert "Why this workspace exists" in prompts[0]
    assert "专注检索增强生成的研究笔记" in prompts[0]

    query_prompts: list[str] = []

    def capture(config, messages):
        query_prompts.append(messages[0]["content"])
        return "回答 [1]"

    monkeypatch.setattr(llm_service, "_chat_completion", capture)
    client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "检索"})
    assert "专注检索增强生成的研究笔记" in query_prompts[0]

    llm_service.clear_runtime_config()


def test_removing_the_last_source_removes_the_topic_page(client: TestClient):
    kb = create_kb(client, "AI 研究")
    created = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "RAG 检索策略", "content": "检索增强生成把外部来源带入上下文。"},
    ).json()

    assert client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/rag-检索策略.md").status_code == 200

    assert client.delete(f"/api/documents/{created['id']}").status_code == 204

    assert client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/rag-检索策略.md").status_code == 404
