from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.models import QueryFeedback
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


# ---------------------------------------------------------------------------
# Query scoring: CJK bigrams and coverage
# ---------------------------------------------------------------------------


def test_search_tokens_split_cjk_into_bigrams_and_keep_latin_words():
    from app.services.wiki_service import _search_tokens

    assert _search_tokens("如何配置资产") == ("如何", "何配", "配置", "置资", "资产")
    assert _search_tokens("RAG 检索") == ("rag", "检索")
    assert _search_tokens("春") == ("春",)
    assert _search_tokens("资产 资产") == ("资产",), "repeated terms collapse"
    assert _search_tokens("  ") == ()


def test_query_finds_pages_across_word_order_and_inserted_particles(client: TestClient):
    """如何配置资产 must reach a page about 资产配置; a plain substring match cannot."""
    kb = create_kb(client, "投资笔记")
    base = f"/api/knowledge-bases/{kb['id']}/wiki/pages"
    client.put(f"{base}/wiki/topics/资产配置入门.md", json={"path": "wiki/topics/资产配置入门.md", "content": "# 资产配置入门\n\n先建立应急金，再考虑权益类资产的比例。\n"})
    client.put(f"{base}/wiki/topics/天气.md", json={"path": "wiki/topics/天气.md", "content": "# 天气\n\n今天多云，适合散步。\n"})

    results = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "如何配置资产"}).json()["results"]

    paths = {item["path"] for item in results}
    assert "wiki/topics/资产配置入门.md" in paths
    assert "wiki/topics/天气.md" not in paths


def test_query_matches_across_a_particle_inside_the_phrase(client: TestClient):
    kb = create_kb(client, "AI 研究")
    base = f"/api/knowledge-bases/{kb['id']}/wiki/pages"
    client.put(f"{base}/wiki/topics/rag.md", json={"path": "wiki/topics/rag.md", "content": "# RAG 检索策略\n\n向量召回要配合关键词召回使用。\n"})

    results = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "检索的策略"}).json()["results"]

    assert results and results[0]["path"] == "wiki/topics/rag.md"


def test_title_match_outranks_a_body_only_match(client: TestClient):
    kb = create_kb(client, "投资笔记")
    base = f"/api/knowledge-bases/{kb['id']}/wiki/pages"
    client.put(f"{base}/wiki/topics/资产配置入门.md", json={"path": "wiki/topics/资产配置入门.md", "content": "# 资产配置入门\n\n先建立应急金。\n"})
    client.put(f"{base}/wiki/topics/随笔.md", json={"path": "wiki/topics/随笔.md", "content": "# 随笔\n\n今天和朋友聊到资产配置这个话题。\n"})

    results = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "资产配置"}).json()["results"]

    assert results[0]["path"] == "wiki/topics/资产配置入门.md"
    assert results[0]["score"] > results[1]["score"]


def test_score_does_not_grow_with_query_length(client: TestClient):
    """Coverage, not raw counts: a long query must not push every page near 1.0."""
    kb = create_kb(client, "投资笔记")
    client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/资产配置入门.md",
        json={"path": "wiki/topics/资产配置入门.md", "content": "# 资产配置入门\n\n先建立应急金，再考虑权益类资产的比例。\n"},
    )

    short = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "资产配置"}).json()["results"][0]
    long = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "资产配置应该怎么开始比较稳妥并且长期坚持下来"},
    ).json()["results"][0]

    assert 0.0 < long["score"] < short["score"] <= 0.99


# ---------------------------------------------------------------------------
# Adaptive link expansion
# ---------------------------------------------------------------------------


def chain_workspace(client: TestClient, kb_id: int) -> None:
    """近 -> 中 -> 远 一条三跳链，用于观察扩展跳数。"""
    base = f"/api/knowledge-bases/{kb_id}/wiki/pages"
    client.put(f"{base}/wiki/topics/近.md", json={"path": "wiki/topics/近.md", "content": "# 近\n\n阿尔法关键词。\n\n[[wiki/topics/中]]\n"})
    client.put(f"{base}/wiki/topics/中.md", json={"path": "wiki/topics/中.md", "content": "# 中\n\n贝塔内容。\n\n[[wiki/topics/远]]\n"})
    client.put(f"{base}/wiki/topics/远.md", json={"path": "wiki/topics/远.md", "content": "# 远\n\n伽马内容，与查询无关。\n"})


def test_confident_match_stays_one_hop(client: TestClient, monkeypatch):
    monkeypatch.setenv("WIKI_QUERY_DEEP_THRESHOLD", "-1")
    get_settings.cache_clear()
    kb = create_kb(client, "链式")
    chain_workspace(client, kb["id"])

    results = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "阿尔法"}).json()["results"]

    related = {item["path"] for item in results if item["related"]}
    assert "wiki/topics/中.md" in related
    assert "wiki/topics/远.md" not in related, "confident matches must not spend budget on a second hop"


def test_weak_match_walks_a_second_hop(client: TestClient, monkeypatch):
    monkeypatch.setenv("WIKI_QUERY_DEEP_THRESHOLD", "1")
    get_settings.cache_clear()
    kb = create_kb(client, "链式")
    chain_workspace(client, kb["id"])

    results = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "阿尔法"}).json()["results"]

    related = {item["path"]: item["score"] for item in results if item["related"]}
    assert "wiki/topics/远.md" in related, "a weak match may follow one more link"
    assert related["wiki/topics/远.md"] < related["wiki/topics/中.md"], "distance must cost confidence"


def test_second_hop_halves_confidence_again():
    from app.db.schemas import WikiPageRead
    from app.services.wiki_service import WikiQueryResult, _related_neighbours

    def page(path: str) -> WikiPageRead:
        return WikiPageRead(path=path, title=path, page_type="topic", summary="", content="", updated_at=datetime.now(timezone.utc), outbound_links=0)

    pages = {name: page(name) for name in ("a.md", "b.md", "c.md")}
    seed = [WikiQueryResult(path="a.md", title="a", page_type="topic", summary="", snippet="", score=0.8)]
    edges = [("a.md", "b.md"), ("b.md", "c.md")]

    one_hop = _related_neighbours(seed, pages, edges, 10, (), hops=1)
    two_hops = _related_neighbours(seed, pages, edges, 10, (), hops=2)

    assert [item.path for item in one_hop] == ["b.md"]
    assert {item.path: item.score for item in two_hops} == {"b.md": 0.4, "c.md": 0.2}


def test_expansion_respects_the_neighbour_budget(client: TestClient, monkeypatch):
    monkeypatch.setenv("WIKI_QUERY_DEEP_THRESHOLD", "1")
    monkeypatch.setenv("WIKI_QUERY_NEIGHBOUR_LIMIT", "1")
    get_settings.cache_clear()
    kb = create_kb(client, "链式")
    chain_workspace(client, kb["id"])

    results = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "阿尔法"}).json()["results"]

    related = [item["path"] for item in results if item["related"]]
    assert related == ["wiki/topics/中.md"]


# ---------------------------------------------------------------------------
# Named retrieval strategies
# ---------------------------------------------------------------------------


def test_strategies_are_listed_with_their_tradeoffs(client: TestClient):
    items = client.get("/api/retrieval-strategies").json()["items"]

    ids = [item["id"] for item in items]
    assert ids == ["auto", "local", "deep", "hybrid", "planned"]
    assert all(item["description"] for item in items)


def test_query_reports_the_strategy_and_the_hops_it_used(client: TestClient):
    kb = create_kb(client, "链式")
    chain_workspace(client, kb["id"])

    data = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "阿尔法"}).json()

    assert data["strategy"] == "auto", "the default strategy is applied and reported"
    assert data["hops"] in {1, 2}


def test_local_strategy_skips_link_expansion(client: TestClient):
    kb = create_kb(client, "链式")
    chain_workspace(client, kb["id"])

    data = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "阿尔法", "strategy": "local"},
    ).json()

    assert data["strategy"] == "local"
    assert data["results"]
    assert all(not item["related"] for item in data["results"])


def test_deep_strategy_always_walks_two_hops(client: TestClient):
    kb = create_kb(client, "链式")
    chain_workspace(client, kb["id"])

    data = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "阿尔法", "strategy": "deep"},
    ).json()

    related = {item["path"] for item in data["results"] if item["related"]}
    assert data["hops"] == 2
    assert "wiki/topics/远.md" in related


def test_unknown_strategy_is_rejected_with_the_available_ids(client: TestClient):
    kb = create_kb(client, "链式")

    response = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "阿尔法", "strategy": "smarter"},
    )

    assert response.status_code == 400
    assert "auto" in response.json()["detail"] and "deep" in response.json()["detail"]


def test_feedback_records_which_strategy_produced_the_answer(client: TestClient):
    kb = create_kb(client)

    created = client.post(
        f"/api/knowledge-bases/{kb['id']}/feedback",
        json={"mode": "wiki", "query": "资产配置", "rating": -1, "strategy_id": "deep"},
    ).json()

    assert created["strategy_id"] == "deep"
    listed = client.get(f"/api/knowledge-bases/{kb['id']}/feedback").json()["items"][0]
    assert listed["strategy_id"] == "deep"


# ---------------------------------------------------------------------------
# Replay tool
# ---------------------------------------------------------------------------


def load_replay_module():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "replay_queries.py"
    spec = importlib.util.spec_from_file_location("replay_queries", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_replay_summary_separates_liked_from_disliked():
    replay = load_replay_module()

    summary = replay.summarise(
        [
            {"rating": 1, "recorded": 4, "kept": 4, "changed": False, "related": 2, "hops": 2},
            {"rating": 1, "recorded": 2, "kept": 1, "changed": True, "related": 0, "hops": 1},
            {"rating": -1, "recorded": 1, "kept": 1, "changed": False, "related": 0, "hops": 1},
        ]
    )

    assert summary["liked"] == 2
    assert summary["disliked"] == 1
    assert (summary["kept_on_liked"], summary["recorded_on_liked"]) == (5, 6)
    assert summary["changed_on_disliked"] == 0
    assert summary["average_hops"] == round(4 / 3, 2)


def test_replay_never_writes_to_the_activity_log(client: TestClient, monkeypatch):
    """A replay must be invisible in the workspace: no log lines, no saved pages."""
    from app.services import wiki_service

    replay = load_replay_module()
    captured: dict = {}
    original = wiki_service.query_wiki

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(replay.wiki_service, "query_wiki", spy)
    kb = create_kb(client, "投资笔记")
    client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/资产配置入门.md",
        json={"path": "wiki/topics/资产配置入门.md", "content": "# 资产配置入门\n\n先建立应急金。\n"},
    )
    row = QueryFeedback(
        knowledge_base_id=kb["id"],
        mode="wiki",
        query="资产配置",
        rating=1,
        source_paths=["wiki/topics/资产配置入门.md"],
    )

    result = replay.replay(row, "auto", 8)

    assert captured["record_activity"] is False
    assert captured["strategy_id"] == "auto"
    assert result["kept"] == 1 and result["recorded"] == 1
    log = (Path(get_settings().wiki_root_dir) / f"kb-{kb['id']}" / "log.md").read_text(encoding="utf-8")
    assert "query |" not in log.split("init |")[-1], "replays stay out of the activity log"


# ---------------------------------------------------------------------------
# Page vectors: a synonym still has to be reachable
# ---------------------------------------------------------------------------


def fake_embeddings(mapping: dict[str, list[float]], dimension: int = 4):
    """Deterministic stand-in for the embedding model, so tests stay offline."""

    def embed(texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            for needle, vector in mapping.items():
                if needle in text:
                    vectors.append(vector)
                    break
            else:
                vectors.append([0.0] * dimension)
        return vectors

    return embed


def test_hybrid_strategy_reaches_a_page_the_words_do_not_share(client: TestClient, monkeypatch):
    """小孩子 and 少年闰土 share no characters; only the vector side can connect them."""
    from app.services import embedding_service, wiki_service

    wiki_service._page_vector_cache.clear()
    kb = create_kb(client, "现代文学")
    client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/闰土.md",
        json={"path": "wiki/topics/闰土.md", "content": "# 少年闰土\n\n银项圈，钢叉，西瓜地里猹。\n"},
    )
    client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/天气.md",
        json={"path": "wiki/topics/天气.md", "content": "# 天气\n\n今天多云。\n"},
    )
    monkeypatch.setattr(
        embedding_service,
        "embed_texts",
        fake_embeddings({"少年闰土": [1.0, 0.0, 0.0, 0.0], "小孩子": [1.0, 0.0, 0.0, 0.0]}),
    )

    lexical_only = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "小孩子", "strategy": "local"},
    ).json()
    hybrid = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "小孩子", "strategy": "hybrid"},
    ).json()

    assert lexical_only["results"] == [], "the words share nothing, so lexical search finds nothing"
    assert hybrid["vectors"] is True
    assert [item["path"] for item in hybrid["results"]] == ["wiki/topics/闰土.md"]


def test_hybrid_degrades_to_lexical_when_embeddings_are_unavailable(client: TestClient, monkeypatch):
    from app.services import embedding_service, wiki_service

    wiki_service._page_vector_cache.clear()
    kb = create_kb(client, "现代文学")
    client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/闰土.md",
        json={"path": "wiki/topics/闰土.md", "content": "# 少年闰土\n\n银项圈，钢叉，西瓜地里猹。\n"},
    )

    def unavailable(texts: list[str]) -> list[list[float]]:
        raise embedding_service.EmbeddingError("model not installed")

    monkeypatch.setattr(embedding_service, "embed_texts", unavailable)

    data = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "闰土", "strategy": "hybrid"},
    ).json()

    assert data["strategy"] == "hybrid", "the requested strategy is still reported"
    assert data["vectors"] is False, "but the response must not pretend vectors were used"
    assert [item["path"] for item in data["results"]] == ["wiki/topics/闰土.md"]


def test_auto_strategy_stays_purely_lexical(client: TestClient, monkeypatch):
    """The default must not depend on an embedding model being present."""
    from app.services import embedding_service, wiki_service

    wiki_service._page_vector_cache.clear()
    kb = create_kb(client, "现代文学")
    client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/闰土.md",
        json={"path": "wiki/topics/闰土.md", "content": "# 少年闰土\n\n银项圈，钢叉，西瓜地里猹。\n"},
    )
    called: list[str] = []

    def embed(texts: list[str]) -> list[list[float]]:
        called.append(texts[0])
        return fake_embeddings({"少年闰土": [1.0, 0.0, 0.0, 0.0]})(texts)

    monkeypatch.setattr(embedding_service, "embed_texts", embed)

    data = client.post(f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "闰土"}).json()

    assert data["vectors"] is False
    assert called == [], "the default path must not load the embedding model"


# ---------------------------------------------------------------------------
# Query planning: the model may rewrite the question, never the ranking
# ---------------------------------------------------------------------------


def test_planned_strategy_searches_with_model_supplied_terms(client: TestClient, monkeypatch):
    """The page only contains 少年闰土; the question says 小孩子, and planning bridges them."""
    from app.services import embedding_service, llm_service, wiki_service

    wiki_service._page_vector_cache.clear()
    kb = create_kb(client, "现代文学")
    client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/闰土.md",
        json={"path": "wiki/topics/闰土.md", "content": "# 少年闰土\n\n银项圈，钢叉，西瓜地里猹。\n"},
    )
    monkeypatch.setattr(embedding_service, "embed_texts", lambda texts: [[0.0, 0.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(llm_service, "plan_query", lambda question, purpose="", override=None: ("闰土",))
    llm_service.clear_runtime_config()

    data = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "小孩子", "strategy": "planned"},
    ).json()

    assert data["planned"] is True
    assert [item["path"] for item in data["results"]] == ["wiki/topics/闰土.md"]
    assert data["answer_mode"] == "deterministic", "planning must not turn the answer into a model answer"


def test_planned_strategy_falls_back_to_the_original_question(client: TestClient, monkeypatch):
    from app.services import embedding_service, llm_service, wiki_service

    wiki_service._page_vector_cache.clear()
    monkeypatch.setattr(embedding_service, "embed_texts", lambda texts: [[0.0, 0.0, 0.0, 0.0] for _ in texts])
    kb = create_kb(client, "现代文学")
    client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/闰土.md",
        json={"path": "wiki/topics/闰土.md", "content": "# 少年闰土\n\n银项圈，钢叉，西瓜地里猹。\n"},
    )
    monkeypatch.setattr(llm_service, "plan_query", lambda question, purpose="", override=None: ())
    llm_service.clear_runtime_config()

    data = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "闰土", "strategy": "planned"},
    ).json()

    assert data["planned"] is False
    assert [item["path"] for item in data["results"]] == ["wiki/topics/闰土.md"]


def test_plan_query_parses_only_a_keyword_line(monkeypatch):
    from app.services import llm_service

    llm_service.clear_runtime_config()
    llm_service.update_runtime_config(LLMConfigurationInput(enabled=True, base_url="http://model.test/v1", api_key="k", model="m"))
    monkeypatch.setattr(
        llm_service,
        "_chat_completion",
        lambda config, messages: "KEYWORDS: 闰土，少年闰土、西瓜地\n（其余解释应当被忽略）",
    )

    assert llm_service.plan_query("小孩子") == ("闰土", "少年闰土", "西瓜地")

    monkeypatch.setattr(llm_service, "_chat_completion", lambda config, messages: "直接回答问题，没有关键词行")
    assert llm_service.plan_query("小孩子") == ()

    llm_service.clear_runtime_config()


def test_vector_floor_keeps_unrelated_pages_out(client: TestClient, monkeypatch):
    """Below the floor a page is not a semantic hit, so "no answer" stays visible."""
    from app.services import embedding_service, wiki_service

    wiki_service._page_vector_cache.clear()
    kb = create_kb(client, "投资笔记")
    client.put(
        f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/topics/无关.md",
        json={"path": "wiki/topics/无关.md", "content": "# 无关\n\n今天多云。\n"},
    )
    # A similarity of 0.5 clears the default floor of 0.35; 0.1 does not.
    monkeypatch.setattr(
        embedding_service,
        "embed_texts",
        fake_embeddings({"无关": [0.5, 0.0, 0.0, 0.0], "查询": [1.0, 0.0, 0.0, 0.0]}),
    )

    above = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "查询", "strategy": "hybrid"}
    ).json()
    monkeypatch.setattr(
        embedding_service,
        "embed_texts",
        fake_embeddings({"无关": [0.1, 0.0, 0.0, 0.0], "查询": [1.0, 0.0, 0.0, 0.0]}),
    )
    wiki_service._page_vector_cache.clear()
    below = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query", json={"query": "查询", "strategy": "hybrid"}
    ).json()

    assert above["vectors"] is True and above["results"]
    assert below["vectors"] is False and below["results"] == []


# ---------------------------------------------------------------------------
# Proposal tool: evidence in, reviewable Markdown out
# ---------------------------------------------------------------------------


def load_proposal_module():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "propose_strategy_change.py"
    spec = importlib.util.spec_from_file_location("propose_strategy_change", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def row(rating: int, kept: int, recorded: int, changed: bool = False, hops: int = 1) -> dict:
    return {"query": "q", "rating": rating, "kept": kept, "recorded": recorded, "changed": changed, "hops": hops, "related": 0, "direct": 1, "top": None}


def test_top_changed_compares_the_leading_citation_only():
    replay = load_replay_module()

    assert replay.top_changed(["a.md", "b.md"], "a.md") is False
    assert replay.top_changed(["a.md", "b.md"], "c.md") is True
    assert replay.top_changed([], "a.md") is False, "nothing was recorded, so nothing changed"


def test_candidate_is_judged_against_the_default_not_the_recording():
    """The default already changed every case here; a candidate that changes nothing new is not an improvement."""
    proposal = load_proposal_module()
    results = {
        "auto": {"rows": [row(1, 4, 4), row(-1, 4, 4, changed=True)]},
        "deep": {"rows": [row(1, 4, 4), row(-1, 4, 4, changed=True)]},
    }

    chosen, reason = proposal.recommendation("auto", results)

    assert chosen == "auto"
    assert "多付成本" in reason


def test_a_candidate_that_loses_a_liked_citation_is_never_recommended():
    proposal = load_proposal_module()
    results = {
        "auto": {"rows": [row(1, 4, 4), row(-1, 1, 4, changed=False)]},
        "local": {"rows": [row(1, 2, 4), row(-1, 1, 4, changed=True)]},
    }

    chosen, reason = proposal.recommendation("auto", results)

    assert chosen == "auto"
    assert "保持现状" in reason


def test_a_candidate_that_changes_a_case_the_default_left_alone_is_proposed():
    proposal = load_proposal_module()
    results = {
        "auto": {"rows": [row(1, 4, 4), row(-1, 4, 4, changed=False)]},
        "hybrid": {"rows": [row(1, 4, 4), row(-1, 4, 4, changed=True)]},
    }

    chosen, reason = proposal.recommendation("auto", results)

    assert chosen == "hybrid"
    assert "值得人工判断" in reason


def test_proposal_lists_what_the_evidence_cannot_show():
    proposal = load_proposal_module()
    results = {"auto": {"rows": [row(1, 4, 4)]}, "hybrid": {"rows": [row(1, 4, 4)]}}

    text = proposal.render_proposal(1, "auto", results, [], "auto", "保持现状")

    assert "不能说明什么" in text
    assert "相对当前默认策略" in text
    assert "WIKI_QUERY_DEFAULT_STRATEGY" in text
