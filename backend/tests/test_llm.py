from fastapi.testclient import TestClient

from app.db.schemas import LLMConfigurationInput, SearchRequest
from app.services import llm_service, retrieval_service
from tests.conftest import create_kb


def test_llm_status_never_returns_api_key(client: TestClient, monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "server-secret")
    monkeypatch.setenv("LLM_MODEL", "demo-model")
    from app.config import get_settings

    get_settings.cache_clear()
    llm_service.clear_runtime_config()

    response = client.get("/api/llm/config")

    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert data["api_key_configured"] is True
    assert "server-secret" not in response.text


def test_web_config_is_process_local_and_can_drive_wiki_synthesis(client: TestClient, monkeypatch):
    kb = create_kb(client)
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "春天", "content": "春天带来花草和新的生长。"},
    )
    calls: list[tuple[str, str]] = []

    def fake_chat(config, messages):
        calls.append((config.model, messages[-1]["content"]))
        return "春天与花草的新生有关。[1]"

    monkeypatch.setattr(llm_service, "_chat_completion", fake_chat)
    llm_service.clear_runtime_config()

    configure = client.put(
        "/api/llm/config",
        json={
            "enabled": True,
            "base_url": "http://model.test/v1",
            "api_key": "browser-secret",
            "model": "test-model",
            "temperature": 0.1,
            "max_tokens": 256,
            "timeout_seconds": 10,
        },
    )
    assert configure.status_code == 200
    assert "browser-secret" not in configure.text

    response = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "花草"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "春天与花草的新生有关。[1]"
    assert data["answer_mode"] == "llm"
    assert data["model"] == "test-model"
    assert calls and "[1] 春天" in calls[0][1]


def test_llm_failure_preserves_deterministic_wiki_answer(client: TestClient, monkeypatch):
    kb = create_kb(client)
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "春天", "content": "春天带来花草和新的生长。"},
    )
    llm_service.clear_runtime_config()

    def failed_chat(config, messages):
        raise llm_service.LLMRequestError("模型服务请求超时，已保留本地检索结果。")

    monkeypatch.setattr(llm_service, "_chat_completion", failed_chat)
    response = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={
            "query": "花草",
            "llm": {
                "enabled": True,
                "base_url": "http://model.test/v1",
                "api_key": "test-key",
                "model": "test-model",
            },
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["answer_mode"] == "deterministic"
    assert data["model_error"] == "模型服务请求超时，已保留本地检索结果。"
    assert "基于知识库中" in data["answer"]


def test_request_can_disable_backend_runtime_llm_for_agent_retrieval(client: TestClient, monkeypatch):
    kb = create_kb(client)
    client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "春天", "content": "春天带来花草和新的生长。"},
    )
    calls: list[object] = []

    def fake_chat(config, messages):
        calls.append(config)
        return "这不应被调用。"

    monkeypatch.setattr(llm_service, "_chat_completion", fake_chat)
    configure = client.put(
        "/api/llm/config",
        json={
            "enabled": True,
            "base_url": "http://model.test/v1",
            "api_key": "browser-secret",
            "model": "test-model",
        },
    )
    assert configure.status_code == 200

    response = client.post(
        f"/api/knowledge-bases/{kb['id']}/wiki/query",
        json={"query": "花草", "llm": {"enabled": False}},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["answer_mode"] == "deterministic"
    assert calls == []


def test_rag_synthesis_returns_retrieval_results_with_answer(monkeypatch):
    def fake_embed(texts):
        return [[0.1, 0.2]]

    def fake_query(*args, **kwargs):
        return {
            "documents": [["花草在春天开始生长。"]],
            "metadatas": [[{"knowledge_base_id": 1, "document_id": 1, "chunk_id": 1, "chunk_index": 0, "title": "春天"}]],
            "distances": [[0.1]],
        }

    monkeypatch.setattr(retrieval_service.embedding_service, "embed_texts", fake_embed)
    monkeypatch.setattr(retrieval_service.chroma_client, "query_chunks", fake_query)
    monkeypatch.setattr(llm_service, "_chat_completion", lambda config, messages: "花草在春天生长。[1]")

    response = retrieval_service.search_knowledge_base(
        SearchRequest(
            knowledge_base_id=1,
            query="花草",
            llm=LLMConfigurationInput(
                enabled=True,
                base_url="http://model.test/v1",
                api_key="test-key",
                model="test-model",
            ),
        )
    )

    assert response.answer == "花草在春天生长。[1]"
    assert response.answer_mode == "llm"
    assert response.results[0].title == "春天"
