import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))


DEFAULT_BACKEND_API_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 10.0
MCP_LOCAL_RETRIEVAL = {"enabled": False}
MCP_EXPLICIT_SYNTHESIS = {"enabled": True}


def backend_api_url() -> str:
    return os.getenv("BACKEND_API_URL", DEFAULT_BACKEND_API_URL).rstrip("/")


def backend_timeout_seconds() -> float:
    raw_timeout = os.getenv("BACKEND_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        return float(raw_timeout)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


async def search_knowledge_base(query: str, knowledge_base_id: int, top_k: int = 5) -> dict[str, Any]:
    """Retrieve raw-source RAG evidence without invoking AgentKB's LLM.

    Use this tool when the calling Agent should inspect chunks and compose the
    final response with its own model. It remains retrieval-only even when the
    AgentKB workbench has model synthesis enabled.
    """
    query = query.strip()
    if not query:
        return _error("query cannot be empty.", query=query)
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.", query=query)
    if top_k <= 0:
        return _error("top_k must be greater than 0.", query=query)

    payload = {
        "knowledge_base_id": knowledge_base_id,
        "query": query,
        "top_k": top_k,
        "llm": MCP_LOCAL_RETRIEVAL,
    }
    response = await _request("POST", "/api/search", json=payload)
    if "error" in response:
        return _error(response["error"], query=query)

    results = response.get("results", [])
    if not results:
        return {
            "query": query,
            "results": [],
            "message": "No matching knowledge chunks found.",
        }

    return response


async def list_knowledge_bases(page: int = 1, page_size: int = 20) -> dict[str, Any]:
    if page <= 0:
        return _error("page must be greater than 0.")
    if page_size <= 0:
        return _error("page_size must be greater than 0.")

    response = await _request("GET", f"/api/knowledge-bases?page={page}&page_size={page_size}")
    if "error" in response:
        return _error(response["error"])
    return response


async def add_text_document(knowledge_base_id: int, title: str, content: str) -> dict[str, Any]:
    title = title.strip()
    content = content.strip()
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.")
    if not title:
        return _error("title cannot be empty.")
    if not content:
        return _error("content cannot be empty.")

    response = await _request(
        "POST",
        f"/api/knowledge-bases/{knowledge_base_id}/documents/text",
        # MCP ingest stays deterministic: model synthesis is only reachable through
        # the explicit synthesize_knowledge tool.
        json={"title": title, "content": content, "synthesize_topic": False},
    )
    if "error" in response:
        return _error(response["error"])
    return response


async def get_wiki_status(knowledge_base_id: int) -> dict[str, Any]:
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.")
    response = await _request("GET", f"/api/knowledge-bases/{knowledge_base_id}/wiki/status")
    if "error" in response:
        return _error(response["error"])
    return response


async def list_wiki_pages(knowledge_base_id: int, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.")
    if page <= 0 or page_size <= 0:
        return _error("page and page_size must be greater than 0.")
    response = await _request(
        "GET",
        f"/api/knowledge-bases/{knowledge_base_id}/wiki/pages?page={page}&page_size={page_size}",
    )
    if "error" in response:
        return _error(response["error"])
    return response


async def read_wiki_page(knowledge_base_id: int, path: str) -> dict[str, Any]:
    path = path.strip().lstrip("/")
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.")
    if not path:
        return _error("path cannot be empty.")
    response = await _request("GET", f"/api/knowledge-bases/{knowledge_base_id}/wiki/pages/{path}")
    if "error" in response:
        return _error(response["error"])
    return response


async def list_answer_feedback(knowledge_base_id: int, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """Read the recorded 👍/👎 ratings and their running totals.

    This is the only signal AgentKB has about whether retrieval worked, and it is produced
    by a person rating an answer in the workbench — never by a model. Each row carries the
    retrieval configuration that produced the answer, so a rating can be attributed.
    """
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.")
    if page <= 0:
        return _error("page must be greater than 0.")
    if page_size <= 0:
        return _error("page_size must be greater than 0.")
    response = await _request(
        "GET",
        f"/api/knowledge-bases/{knowledge_base_id}/feedback",
        params={"page": page, "page_size": page_size},
    )
    if "error" in response:
        return _error(response["error"])
    return response


async def search_with_strategy(knowledge_base_id: int, query: str, strategy: str, top_k: int = 8) -> dict[str, Any]:
    """Retrieve wiki pages through a named retrieval strategy.

    Strategies differ in how far they follow the wiki's own links and how many linked
    pages they add, so the calling Agent can pick one per question instead of living with
    a single fixed configuration. Available ids come from ``list_retrieval_strategies``;
    the response echoes the strategy that ran and the hop count it used, and the same id
    should be reported back through answer feedback so the choice can be evaluated later.
    """
    query = query.strip()
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.")
    if not query:
        return _error("query cannot be empty.")
    if not strategy.strip():
        return _error("strategy cannot be empty.")
    if top_k <= 0:
        return _error("top_k must be greater than 0.")
    payload: dict[str, Any] = {"query": query, "top_k": top_k, "strategy": strategy.strip(), "llm": MCP_LOCAL_RETRIEVAL}
    response = await _request("POST", f"/api/knowledge-bases/{knowledge_base_id}/wiki/query", json=payload)
    if "error" in response:
        return _error(response["error"], query=query)
    return response


async def list_retrieval_strategies() -> dict[str, Any]:
    """List the named retrieval strategies this Backend offers, with their trade-offs."""
    response = await _request("GET", "/api/retrieval-strategies")
    if "error" in response:
        return _error(response["error"])
    return response


async def query_wiki(knowledge_base_id: int, query: str, top_k: int = 8, save_as: str | None = None) -> dict[str, Any]:
    """Retrieve ranked Markdown wiki pages without invoking AgentKB's LLM.

    Use the returned pages as durable knowledge evidence in the calling
    Agent's own reasoning. Results marked ``related: true`` were reached by
    following a wiki link from a matching page: treat them as supporting
    context, not as direct matches. Set ``save_as`` only when the deterministic
    page answer should be saved as a wiki query page.
    """
    query = query.strip()
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.")
    if not query:
        return _error("query cannot be empty.")
    if top_k <= 0:
        return _error("top_k must be greater than 0.")
    payload: dict[str, Any] = {"query": query, "top_k": top_k, "llm": MCP_LOCAL_RETRIEVAL}
    if save_as and save_as.strip():
        payload["save_as"] = save_as.strip()
    response = await _request("POST", f"/api/knowledge-bases/{knowledge_base_id}/wiki/query", json=payload)
    if "error" in response:
        return _error(response["error"], query=query)
    return response


async def synthesize_knowledge(
    knowledge_base_id: int,
    query: str,
    source_mode: str = "wiki",
    top_k: int = 8,
    save_as: str | None = None,
) -> dict[str, Any]:
    """Explicitly ask AgentKB's configured LLM to answer from retrieved evidence.

    ``source_mode`` is ``wiki`` for maintained Markdown pages or ``rag`` for
    raw-source vector retrieval. The tool uses the model configured in the
    AgentKB Backend; it never accepts Provider credentials from an MCP client.
    Base MCP retrieval tools intentionally do not call this model.
    """
    query = query.strip()
    source_mode = source_mode.strip().lower()
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.")
    if not query:
        return _error("query cannot be empty.")
    if top_k <= 0:
        return _error("top_k must be greater than 0.")
    if source_mode not in {"wiki", "rag"}:
        return _error("source_mode must be either 'wiki' or 'rag'.", query=query)
    if source_mode == "rag" and save_as and save_as.strip():
        return _error("save_as is only supported when source_mode is 'wiki'.", query=query)

    if source_mode == "rag":
        payload = {
            "knowledge_base_id": knowledge_base_id,
            "query": query,
            "top_k": top_k,
            "llm": MCP_EXPLICIT_SYNTHESIS,
        }
        response = await _request("POST", "/api/search", json=payload)
    else:
        payload: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "llm": MCP_EXPLICIT_SYNTHESIS,
        }
        if save_as and save_as.strip():
            payload["save_as"] = save_as.strip()
        response = await _request(
            "POST",
            f"/api/knowledge-bases/{knowledge_base_id}/wiki/query",
            json=payload,
        )

    if "error" in response:
        return _error(response["error"], query=query)
    return response


async def lint_wiki(knowledge_base_id: int) -> dict[str, Any]:
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.")
    response = await _request("POST", f"/api/knowledge-bases/{knowledge_base_id}/wiki/lint")
    if "error" in response:
        return _error(response["error"])
    return response


async def add_source_to_wiki(knowledge_base_id: int, title: str, content: str, tags: list[str] | None = None) -> dict[str, Any]:
    """Ingest a source into raw/ and maintain its linked wiki pages.

    The source page and its source list are always updated; topic-page rewriting
    by the Backend model is disabled so an MCP ingest stays deterministic.
    """
    title = title.strip()
    content = content.strip()
    if knowledge_base_id <= 0:
        return _error("knowledge_base_id must be greater than 0.")
    if not title:
        return _error("title cannot be empty.")
    if not content:
        return _error("content cannot be empty.")
    response = await _request(
        "POST",
        f"/api/knowledge-bases/{knowledge_base_id}/documents/text",
        json={"title": title, "content": content, "tags": tags or [], "synthesize_topic": False},
    )
    if "error" in response:
        return _error(response["error"])
    return response


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            base_url=backend_api_url(),
            timeout=backend_timeout_seconds(),
            trust_env=False,
        ) as client:
            response = await client.request(method, path, **kwargs)
    except httpx.TimeoutException:
        return {"error": "Backend request timed out."}
    except httpx.RequestError:
        return {"error": "Backend service unavailable."}

    if response.status_code == 404:
        return {"error": "Backend resource not found."}
    if response.status_code >= 400:
        return {"error": await _read_error_message(response)}
    if response.status_code == 204:
        return {}
    return response.json()


async def _read_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text or response.reason_phrase

    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        return "; ".join(str(item.get("msg", item)) for item in detail)
    return response.reason_phrase


def _error(message: str, query: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": message}
    if query is not None:
        payload["query"] = query
        payload["results"] = []
    return payload
