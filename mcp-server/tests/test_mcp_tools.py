import importlib.util
import json
from pathlib import Path

import httpx
import pytest


TOOLS_PATH = Path(__file__).resolve().parents[1] / "tools.py"
spec = importlib.util.spec_from_file_location("mcp_tools", TOOLS_PATH)
tools = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(tools)


class FakeAsyncClient:
    transport: httpx.MockTransport | None = None
    last_trust_env: bool | None = None

    def __init__(self, *args, **kwargs):
        self.base_url = kwargs.get("base_url")
        self.timeout = kwargs.get("timeout")
        self.__class__.last_trust_env = kwargs.get("trust_env")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def request(self, method: str, path: str, **kwargs):
        assert self.transport is not None
        assert self.last_trust_env is False
        request = httpx.Request(
            method,
            f"http://backend.test{path}",
            params=kwargs.get("params"),
            json=kwargs.get("json"),
        )
        return await self.transport.handle_async_request(request)


@pytest.fixture(autouse=True)
def patch_async_client(monkeypatch):
    monkeypatch.setattr(tools.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("BACKEND_API_URL", "http://backend.test")
    monkeypatch.setenv("BACKEND_TIMEOUT_SECONDS", "10")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def set_transport(handler):
    FakeAsyncClient.transport = httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_search_knowledge_base_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/search"
        assert json.loads(request.content) == {
            "knowledge_base_id": 1,
            "query": "春天",
            "top_k": 5,
            "llm": {"enabled": False},
        }
        return httpx.Response(
            200,
            json={
                "query": "春天",
                "results": [
                    {
                        "document_id": 1,
                        "title": "春",
                        "chunk": "春天的脚步近了。",
                        "score": 0.88,
                    }
                ],
            },
        )

    set_transport(handler)

    result = await tools.search_knowledge_base("春天", 1, 5)

    assert result["query"] == "春天"
    assert result["results"][0]["title"] == "春"


@pytest.mark.anyio
async def test_search_knowledge_base_empty_query_returns_error():
    result = await tools.search_knowledge_base("   ", 1, 5)

    assert result["error"] == "query cannot be empty."
    assert result["results"] == []


@pytest.mark.anyio
async def test_search_knowledge_base_backend_404_returns_error():
    set_transport(lambda request: httpx.Response(404, json={"detail": "Knowledge base not found."}))

    result = await tools.search_knowledge_base("春天", 999, 5)

    assert result["error"] == "Backend resource not found."
    assert result["results"] == []


@pytest.mark.anyio
async def test_search_knowledge_base_timeout_returns_error(monkeypatch):
    class TimeoutClient(FakeAsyncClient):
        async def request(self, method: str, path: str, **kwargs):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(tools.httpx, "AsyncClient", TimeoutClient)

    result = await tools.search_knowledge_base("春天", 1, 5)

    assert result["error"] == "Backend request timed out."


@pytest.mark.anyio
async def test_search_knowledge_base_empty_results_returns_message():
    set_transport(lambda request: httpx.Response(200, json={"query": "未知", "results": []}))

    result = await tools.search_knowledge_base("未知", 1, 5)

    assert result["results"] == []
    assert result["message"] == "No matching knowledge chunks found."


@pytest.mark.anyio
async def test_list_knowledge_bases_success():
    set_transport(
        lambda request: httpx.Response(
            200,
            json={"items": [{"id": 1, "name": "现代文学"}], "total": 1, "page": 1, "page_size": 20},
        )
    )

    result = await tools.list_knowledge_bases()

    assert result["total"] == 1
    assert result["items"][0]["name"] == "现代文学"


@pytest.mark.anyio
async def test_add_text_document_success():
    set_transport(
        lambda request: httpx.Response(
            201,
            json={
                "id": 1,
                "title": "春",
                "knowledge_base_id": 1,
                "source_type": "text",
                "file_name": None,
                "content": "春天的脚步近了。",
                "created_at": "2026-06-09T00:00:00",
                "updated_at": "2026-06-09T00:00:00",
                "chunks": [],
            },
        )
    )

    result = await tools.add_text_document(1, "春", "春天的脚步近了。")

    assert result["title"] == "春"


@pytest.mark.anyio
async def test_query_wiki_success():
    set_transport(
        lambda request: httpx.Response(
            200,
            json={
                "query": "花草",
                "answer": "春天包含花草。",
                "results": [{"path": "wiki/topics/春天.md", "title": "春天", "score": 0.8}],
                "saved_path": None,
            },
        )
    )

    result = await tools.query_wiki(1, "花草")

    assert result["answer"] == "春天包含花草。"
    assert result["results"][0]["path"] == "wiki/topics/春天.md"


@pytest.mark.anyio
async def test_query_wiki_forces_local_retrieval_even_when_backend_llm_is_enabled():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/knowledge-bases/1/wiki/query"
        assert json.loads(request.content) == {
            "query": "花草",
            "top_k": 8,
            "llm": {"enabled": False},
        }
        return httpx.Response(200, json={"query": "花草", "answer": "本地页面回答", "results": []})

    set_transport(handler)

    result = await tools.query_wiki(1, "花草")

    assert result["answer"] == "本地页面回答"


@pytest.mark.anyio
async def test_synthesize_knowledge_explicitly_enables_backend_model_for_wiki():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/knowledge-bases/1/wiki/query"
        assert json.loads(request.content) == {
            "query": "花草",
            "top_k": 8,
            "save_as": "春天结论",
            "llm": {"enabled": True},
        }
        return httpx.Response(
            200,
            json={
                "query": "花草",
                "answer": "春天与花草的新生有关。[1]",
                "answer_mode": "llm",
                "model": "test-model",
                "results": [],
                "saved_path": "queries/春天结论.md",
            },
        )

    set_transport(handler)

    result = await tools.synthesize_knowledge(1, "花草", "wiki", 8, "春天结论")

    assert result["answer_mode"] == "llm"
    assert result["model"] == "test-model"


@pytest.mark.anyio
async def test_synthesize_knowledge_rejects_unknown_source_mode():
    result = await tools.synthesize_knowledge(1, "花草", "unknown")

    assert result["error"] == "source_mode must be either 'wiki' or 'rag'."


@pytest.mark.anyio
async def test_read_wiki_page_rejects_empty_path():
    result = await tools.read_wiki_page(1, "  ")

    assert result["error"] == "path cannot be empty."


@pytest.mark.anyio
async def test_list_answer_feedback_reads_the_signal_with_totals():
    """The Agent must be able to see the ratings it is asked to reason about."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/knowledge-bases/3/feedback"
        assert request.url.params["page_size"] == "20"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": 1,
                        "knowledge_base_id": 3,
                        "mode": "wiki",
                        "query": "资产配置",
                        "rating": -1,
                        "note": "引用无关",
                        "answer": "...",
                        "answer_mode": "deterministic",
                        "model": None,
                        "strategy_id": "deep",
                        "source_paths": ["wiki/topics/资产配置入门.md"],
                        "created_at": "2026-09-19T00:00:00Z",
                    }
                ],
                "total": 1,
                "page": 1,
                "page_size": 20,
                "positive": 0,
                "negative": 1,
            },
        )

    set_transport(handler)

    result = await tools.list_answer_feedback(knowledge_base_id=3)

    assert result["total"] == 1
    assert result["negative"] == 1
    assert result["items"][0]["strategy_id"] == "deep", "a rating has to carry the configuration it judged"


@pytest.mark.anyio
async def test_list_answer_feedback_rejects_bad_arguments():
    assert "error" in await tools.list_answer_feedback(knowledge_base_id=0)
    assert "error" in await tools.list_answer_feedback(knowledge_base_id=1, page=0)
    assert "error" in await tools.list_answer_feedback(knowledge_base_id=1, page_size=0)


@pytest.mark.anyio
async def test_search_with_strategy_sends_the_strategy_and_stays_model_free():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["strategy"] == "hybrid"
        assert body["llm"] == {"enabled": False}, "MCP retrieval never invokes the Backend model"
        return httpx.Response(200, json={"query": "春天", "answer": "…", "results": [], "strategy": "hybrid", "hops": 2, "vectors": True, "planned": False})

    set_transport(handler)

    result = await tools.search_with_strategy(knowledge_base_id=1, query="春天", strategy="hybrid")

    assert result["strategy"] == "hybrid"
    assert result["vectors"] is True


@pytest.mark.anyio
async def test_search_with_strategy_rejects_an_empty_strategy():
    assert "error" in await tools.search_with_strategy(knowledge_base_id=1, query="x", strategy="   ")


@pytest.mark.anyio
async def test_update_source_in_wiki_keeps_ingest_model_free():
    seen: dict = {}

    def transport(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 7, "title": "来源", "chunks": []})

    set_transport(transport)

    result = await tools.update_source_in_wiki(3, 7, "修订后的正文。", title="来源（修订）")

    assert result["id"] == 7
    assert seen["method"] == "PUT"
    assert seen["path"] == "/api/knowledge-bases/3/documents/7"
    # Same contract as every other MCP ingest tool: never trigger topic rewriting.
    assert seen["body"]["synthesize_topic"] is False
    assert seen["body"]["title"] == "来源（修订）"


@pytest.mark.anyio
async def test_update_source_in_wiki_rejects_empty_content():
    set_transport(lambda request: httpx.Response(200, json={}))

    assert "error" in await tools.update_source_in_wiki(1, 2, "   ")
    assert "error" in await tools.update_source_in_wiki(0, 2, "正文")
    assert "error" in await tools.update_source_in_wiki(1, 0, "正文")


@pytest.mark.anyio
async def test_update_source_in_wiki_surfaces_a_backend_conflict():
    set_transport(lambda request: httpx.Response(404, json={"detail": "Document not found."}))

    result = await tools.update_source_in_wiki(1, 99, "正文")

    assert result["error"]
