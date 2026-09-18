"""Provider-neutral OpenAI-compatible answer synthesis.

The service intentionally has no provider SDK dependency. Any endpoint that
implements POST /chat/completions with OpenAI-compatible request and response
shapes can be used through the same configuration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from app.config import get_settings
from app.db.schemas import LLMConfigurationInput, LLMConfigStatusResponse


class LLMConfigurationError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMRuntimeConfig:
    enabled: bool
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: float
    context_max_chars: int

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.base_url and self.api_key and self.model)


@dataclass(frozen=True)
class Evidence:
    reference: str
    title: str
    content: str
    related: bool = False


@dataclass(frozen=True)
class SynthesisOutcome:
    answer: str | None
    model: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class TopicCandidate:
    """One existing topic page the model may file a new source under."""

    slug: str
    title: str
    summary: str
    synthesis: str


@dataclass(frozen=True)
class TopicSynthesisOutcome:
    # Slug of the chosen candidate, or "NEW" when the source starts a new topic.
    target: str | None
    body: str | None
    model: str | None = None
    error: str | None = None


_runtime_override: LLMConfigurationInput | None = None


def resolve_runtime_config(override: LLMConfigurationInput | None = None) -> LLMRuntimeConfig:
    settings = get_settings()

    def pick(field: str, default: Any) -> Any:
        request_value = getattr(override, field) if override is not None else None
        if request_value is not None:
            return request_value
        runtime_value = getattr(_runtime_override, field) if _runtime_override is not None else None
        return default if runtime_value is None else runtime_value

    return LLMRuntimeConfig(
        enabled=pick("enabled", settings.llm_enabled),
        base_url=pick("base_url", settings.llm_base_url).rstrip("/"),
        api_key=pick("api_key", settings.llm_api_key),
        model=pick("model", settings.llm_model),
        temperature=pick("temperature", settings.llm_temperature),
        max_tokens=pick("max_tokens", settings.llm_max_tokens),
        timeout_seconds=pick("timeout_seconds", settings.llm_timeout_seconds),
        context_max_chars=settings.llm_context_max_chars,
    )


def config_status(override: LLMConfigurationInput | None = None) -> LLMConfigStatusResponse:
    config = resolve_runtime_config(override)
    return LLMConfigStatusResponse(
        enabled=config.enabled,
        ready=config.ready,
        base_url=config.base_url,
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout_seconds=config.timeout_seconds,
        api_key_configured=bool(config.api_key),
    )


def update_runtime_config(payload: LLMConfigurationInput) -> LLMConfigStatusResponse:
    """Store web-configured values in this backend process only.

    A server restart deliberately clears these values and returns control to
    deployment configuration from environment variables or `.env`.
    """
    global _runtime_override
    _runtime_override = payload
    return config_status()


def clear_runtime_config() -> None:
    """Reset process-local configuration. Used by tests and local reloads."""
    global _runtime_override
    _runtime_override = None


def synthesize_answer(
    question: str,
    evidence: Iterable[Evidence],
    mode: str,
    purpose: str = "",
    override: LLMConfigurationInput | None = None,
) -> SynthesisOutcome:
    items = [item for item in evidence if item.content.strip()]
    if not items:
        return SynthesisOutcome(answer=None)

    config = resolve_runtime_config(override)
    if not config.enabled:
        return SynthesisOutcome(answer=None)
    if not config.ready:
        return SynthesisOutcome(answer=None, error="模型配置不完整：需要服务地址、API Key 和模型名称。")

    try:
        answer = _chat_completion(config, _messages(question, items, mode, config.context_max_chars, purpose))
    except LLMRequestError as exc:
        return SynthesisOutcome(answer=None, model=config.model, error=str(exc))

    return SynthesisOutcome(answer=answer, model=config.model)


TOPIC_SYSTEM_PROMPT = (
    "You maintain the topic pages of a Markdown knowledge wiki. Decide which single topic page a "
    "new source belongs to, then rewrite that page's evolving synthesis so it covers the source "
    "together with what the page already said. Keep what still holds, revise what the new source "
    "changes, and state disagreements between sources instead of hiding them. Attribute claims to "
    "their source with [[sources/<slug>]] links, and link a related topic page as [[topics/<slug>]] "
    "when the candidate list contains one. Never invent a page that is not in the list."
)

TOPIC_OUTPUT_RULES = (
    "Reply in exactly two parts.\n"
    "Line 1: `TARGET: <slug>` using a candidate slug, or `TARGET: NEW` when this source starts a "
    "topic that no candidate covers. Pick NEW rather than merging on a single generic word.\n"
    "Then a blank line, then the synthesis body in Markdown: no YAML front matter, no code fence, "
    "no top-level '# ' title, no '## Sources' section. Write in the language of the existing page "
    "and stay under 400 words."
)


def synthesize_topic(
    source_title: str,
    source_content: str,
    candidates: Iterable[TopicCandidate] = (),
    purpose: str = "",
    override: LLMConfigurationInput | None = None,
) -> TopicSynthesisOutcome:
    """Let the model file one source into the right topic page and merge it there.

    Routing lives with the model because a token heuristic cannot tell "AI 服务器 MLCC" from
    "AI 制药"; the caller still validates the returned slug against the candidates it offered,
    so the model can never create or rename a file.
    """
    config = resolve_runtime_config(override)
    if not config.enabled:
        return TopicSynthesisOutcome(target=None, body=None)
    if not config.ready:
        return TopicSynthesisOutcome(target=None, body=None, error="模型配置不完整：需要服务地址、API Key 和模型名称。")

    offered = list(candidates)
    messages = _topic_messages(source_title, source_content, offered, purpose, config.context_max_chars)
    try:
        reply = _chat_completion(config, messages)
    except LLMRequestError as exc:
        return TopicSynthesisOutcome(target=None, body=None, model=config.model, error=str(exc))

    target, body = _split_topic_reply(reply)
    if body is None:
        return TopicSynthesisOutcome(target=None, body=None, model=config.model, error="模型未返回主题综合内容。")
    if target not in {candidate.slug for candidate in offered}:
        target = "NEW"
    return TopicSynthesisOutcome(target=target, body=body, model=config.model)


def _topic_messages(
    source_title: str,
    source_content: str,
    candidates: list[TopicCandidate],
    purpose: str,
    context_limit: int,
) -> list[dict[str, str]]:
    blocks = [TOPIC_SYSTEM_PROMPT]
    intent = _purpose_block(purpose)
    if intent:
        blocks.append(intent)
    blocks.append(TOPIC_OUTPUT_RULES)

    if candidates:
        rendered = "\n\n".join(
            f"slug: {candidate.slug}\n"
            f"title: {candidate.title}\n"
            f"summary: {candidate.summary or '(none)'}\n"
            f"current synthesis: {(candidate.synthesis or '(none)')[:600]}"
            for candidate in candidates
        )
    else:
        rendered = "(no candidate pages exist yet)"

    allowance = max(1_000, context_limit - len(rendered))
    excerpt = source_content.strip()[:allowance]
    if len(source_content.strip()) > len(excerpt):
        excerpt = excerpt.rstrip() + "\n[truncated]"

    return [
        {"role": "system", "content": "\n\n".join(blocks)},
        {
            "role": "user",
            "content": (
                f"--- candidate topic pages ---\n{rendered}\n\n"
                f"--- new source: {source_title} ---\n{excerpt}"
            ),
        },
    ]


def _purpose_block(purpose: str) -> str:
    text = purpose.strip()
    if not text:
        return ""
    return f"Why this workspace exists, for judging relevance:\n{text[:1_200]}"


def _split_topic_reply(reply: str) -> tuple[str | None, str | None]:
    match = re.match(r"\s*TARGET:\s*([^\s]+)[^\n]*\n", reply)
    if match:
        target = match.group(1).strip().strip("`\"'").strip()
        body = _synthesis_body(reply[match.end() :])
        return (target or None), (body or None)
    body = _synthesis_body(reply)
    return None, (body or None)


def _synthesis_body(text: str) -> str:
    """Keep only the prose the model is allowed to own."""
    body = re.sub(r"^```[a-zA-Z]*\s*\n", "", text.strip())
    body = re.sub(r"\n```\s*$", "", body).strip()
    lines = body.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("# ")):
        lines.pop(0)
    body = "\n".join(lines)
    body = re.split(r"^#{1,3}\s+(?:Sources|来源)\s*$", body, flags=re.MULTILINE)[0]
    return body.strip()


def test_connection(override: LLMConfigurationInput | None = None) -> str:
    config = resolve_runtime_config(override)
    if not config.ready:
        raise LLMConfigurationError("模型配置不完整：请填写服务地址、API Key 和模型名称，并启用模型综合。")
    return _chat_completion(
        config,
        [
            {"role": "system", "content": "You are a connection check. Reply with exactly: OK"},
            {"role": "user", "content": "Check the connection."},
        ],
    )


def _messages(question: str, evidence: list[Evidence], mode: str, context_limit: int, purpose: str = "") -> list[dict[str, str]]:
    context = _format_evidence(evidence, context_limit)
    source_kind = "knowledge pages" if mode == "wiki" else "raw-source retrieval chunks"
    system = (
        "You are AgentKB's evidence-bound answer writer. Answer in the user's language. "
        f"Use only the supplied {source_kind}; do not add facts from outside knowledge. "
        "If the sources are insufficient, say so plainly. Cite factual claims using the supplied "
        "reference markers such as [1]. Keep the answer concise and readable in Markdown."
    )
    intent = _purpose_block(purpose)
    if intent:
        system = f"{system}\n\n{intent}"
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": f"Question:\n{question}\n\nSources:\n{context}",
        },
    ]


def _format_evidence(evidence: list[Evidence], context_limit: int) -> str:
    remaining = max(1_000, context_limit)
    sections: list[str] = []
    for item in evidence:
        if remaining <= 0:
            break
        header = f"[{item.reference}] {item.title}{' (linked page)' if item.related else ''}\n"
        allowance = max(0, remaining - len(header))
        excerpt = item.content.strip()[:allowance]
        if len(item.content.strip()) > len(excerpt):
            excerpt = excerpt.rstrip() + "\n[truncated]"
        section = f"{header}{excerpt}".strip()
        sections.append(section)
        remaining -= len(section) + 2
    return "\n\n".join(sections)


def _chat_completion(config: LLMRuntimeConfig, messages: list[dict[str, str]]) -> str:
    url = f"{config.base_url}/chat/completions"
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "stream": False,
    }
    try:
        with httpx.Client(timeout=config.timeout_seconds, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {config.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.TimeoutException as exc:
        raise LLMRequestError("模型服务请求超时，已保留本地检索结果。") from exc
    except httpx.RequestError as exc:
        raise LLMRequestError("无法连接模型服务，已保留本地检索结果。") from exc

    if response.status_code >= 400:
        raise LLMRequestError(f"模型服务返回 HTTP {response.status_code}，已保留本地检索结果。")

    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise LLMRequestError("模型服务返回格式无效，已保留本地检索结果。") from exc

    answer = _content_text(content).strip()
    if not answer:
        raise LLMRequestError("模型服务未返回回答，已保留本地检索结果。")
    return answer


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type", "text") in {"text", "output_text"}
        )
    return ""
