"""Markdown-first wiki storage and maintenance for each knowledge base."""

from __future__ import annotations

import math
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from app.config import get_settings
from app.db.schemas import (
    WikiGraphEdge,
    WikiGraphNode,
    WikiIssue,
    LLMConfigurationInput,
    WikiLintResponse,
    WikiPageRead,
    WikiQueryResponse,
    WikiQueryResult,
    WikiStatusResponse,
)
from app.services import embedding_service, llm_service, retrieval_strategy


WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)#]+\.md)(?:#[^)]+)?\)")
TOKEN_PATTERN = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)
CJK_RUN_PATTERN = re.compile(r"[\u3400-\u9fff]+")
LATIN_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")
BM25_K1 = 1.2
BM25_B = 0.75
SAFE_SLUG_PATTERN = re.compile(r"[^\w\u3400-\u9fff-]+", re.UNICODE)


class WikiWorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class WikiFile:
    path: str
    title: str
    page_type: str
    content: str
    summary: str
    updated_at: datetime
    outbound_links: tuple[str, ...]


def workspace_path(knowledge_base_id: int) -> Path:
    return Path(get_settings().wiki_root_dir).expanduser().resolve() / f"kb-{knowledge_base_id}"


def initialize_workspace(knowledge_base_id: int, name: str, description: str | None) -> Path:
    root = workspace_path(knowledge_base_id)
    for directory in (root / "raw" / "sources", root / "wiki" / "sources", root / "wiki" / "topics", root / "wiki" / "entities", root / "wiki" / "queries"):
        directory.mkdir(parents=True, exist_ok=True)

    schema = root / ".wiki-schema.md"
    if not schema.exists():
        schema.write_text(_schema_content(name), encoding="utf-8")
    purpose = root / "purpose.md"
    if not purpose.exists():
        purpose.write_text(
            f"# {name}\n\n{description or '用持续积累、互相链接的 Markdown 页面组织这个知识库。'}\n\n## 关键问题\n\n- 哪些主题值得持续追踪？\n- 新来源改变了哪些已有结论？\n",
            encoding="utf-8",
        )
    overview = root / "wiki" / "overview.md"
    if not overview.exists():
        overview.write_text(
            f"---\ntype: overview\nsummary: {name} 的持续综合入口\n---\n\n# {name}\n\n这是一个由来源、主题和实体页面组成的可维护知识库。优先从 [[index]] 找入口，原始素材位于 `raw/sources/`。\n",
            encoding="utf-8",
        )
    if not (root / "log.md").exists():
        (root / "log.md").write_text(f"# Activity log\n\n## [{_today()}] init | {name}\n\nInitialized Markdown wiki workspace.\n", encoding="utf-8")
    rebuild_index(knowledge_base_id)
    return root


def delete_workspace(knowledge_base_id: int) -> None:
    root = workspace_path(knowledge_base_id)
    if root.exists():
        shutil.rmtree(root)


def delete_source(knowledge_base_id: int, document_id: int) -> None:
    root = workspace_path(knowledge_base_id)
    for directory in (root / "raw" / "sources", root / "wiki" / "sources"):
        for path in directory.glob(f"{document_id}-*.md"):
            path.unlink(missing_ok=True)
    topics_root = root / "wiki" / "topics"
    if topics_root.exists():
        for path in sorted(topics_root.glob("*.md")):
            content = path.read_text(encoding="utf-8")
            doomed = [name for name in _topic_source_names(content) if name.startswith(f"{document_id}-")]
            if not doomed:
                continue
            remaining = content
            for name in doomed:
                remaining = _drop_source_line(remaining, name)
            after = _topic_source_names(remaining)
            # A topic page whose last source is gone has nothing left to stand on.
            if not after:
                path.unlink(missing_ok=True)
                continue
            path.write_text(_with_topic_front_matter(remaining, sources=after) + "\n", encoding="utf-8")
    rebuild_index(knowledge_base_id)


def ingest_source(
    knowledge_base_id: int,
    knowledge_base_name: str,
    document_id: int,
    title: str,
    content: str,
    source_type: str,
    file_name: str | None,
    tags: list[str] | None = None,
    source_url: str | None = None,
    source_platform: str | None = None,
    source_author: str | None = None,
    source_account: str | None = None,
    source_published_at: datetime | None = None,
    source_captured_at: datetime | None = None,
    source_policy: str = "full_text",
    source_disclosures: list[str] | None = None,
    content_hash: str = "",
) -> dict[str, str]:
    root = initialize_workspace(knowledge_base_id, knowledge_base_name, None)
    slug = _slug(title) or f"source-{document_id}"
    source_name = f"{document_id}-{slug}"
    raw_path = root / "raw" / "sources" / f"{source_name}.md"
    source_path = root / "wiki" / "sources" / f"{source_name}.md"
    raw_path.write_text(
        _raw_source_page(
            title=title,
            content=content,
            source_type=source_type,
            file_name=file_name,
            source_url=source_url,
            source_platform=source_platform,
            source_author=source_author,
            source_account=source_account,
            source_published_at=source_published_at,
            source_captured_at=source_captured_at,
            source_policy=source_policy,
            source_disclosures=source_disclosures or [],
            content_hash=content_hash,
        ),
        encoding="utf-8",
    )

    summary = _summary(content)
    topic_path, topic_title, topic_content = _resolve_topic_page(root, title, summary, document_id)
    topic_content = _merge_topic(topic_content, summary, source_name, tags or [])
    topic_content = _with_topic_front_matter(topic_content, sources=_topic_source_names(topic_content))
    topic_path.write_text(topic_content, encoding="utf-8")
    source_path.write_text(
        _source_page(
            title,
            summary,
            source_name,
            topic_path.stem,
            content,
            tags or [],
            source_url=source_url,
            source_platform=source_platform,
            source_author=source_author,
            source_account=source_account,
            source_published_at=source_published_at,
            source_policy=source_policy,
        ),
        encoding="utf-8",
    )
    append_log(
        knowledge_base_id,
        f"ingest | {title}",
        f"Added [[sources/{source_name}]] and updated [[topics/{topic_path.stem}]].",
    )
    rebuild_index(knowledge_base_id)
    return {"raw_path": _relative(root, raw_path), "source_path": _relative(root, source_path), "topic_path": _relative(root, topic_path)}


def complete_ingest(
    knowledge_base_id: int,
    document_id: int,
    title: str,
    content: str,
    synthesize_topic: bool | None = None,
) -> str:
    """Topic maintenance that runs after the document row is committed.

    Routing is the one step where a model beats a token heuristic, so when one is
    configured it files the source among the candidate topic pages and rewrites that
    page's synthesis. This is deliberately separate from `ingest_source`: the source
    page and its source list are already on disk and committed, so a slow or broken
    model can no longer hold a database transaction open or undo an ingest.
    """
    try:
        return _complete_ingest(knowledge_base_id, document_id, title, content, synthesize_topic)
    except Exception as exc:  # noqa: BLE001 - synthesis is best-effort by design
        append_log(knowledge_base_id, f"topic synthesis | {title}", f"Skipped after an error: {exc}")
        return "skipped"


def _complete_ingest(
    knowledge_base_id: int,
    document_id: int,
    title: str,
    content: str,
    synthesize_topic: bool | None,
) -> str:
    if synthesize_topic is False:
        return "disabled"
    root = initialize_workspace(knowledge_base_id, title, None)
    source_name = f"{document_id}-{_slug(title) or f'source-{document_id}'}"
    summary = _summary(content)
    current = _topic_page_with_source(root, source_name)
    candidates = _topic_candidates(root, title, summary, exclude=current[0] if current else None)
    outcome = llm_service.synthesize_topic(
        source_title=title,
        source_content=content,
        candidates=[
            llm_service.TopicCandidate(
                slug=candidate["path"].stem,
                title=candidate["title"],
                summary=candidate["summary"],
                synthesis=_topic_synthesis(candidate["content"]),
            )
            for candidate in candidates
        ],
        purpose=_purpose_text(knowledge_base_id),
    )
    if not outcome.body:
        if outcome.error:
            append_log(knowledge_base_id, f"topic synthesis | {title}", f"Skipped: {outcome.error}")
            return "unavailable"
        return "not configured"

    chosen = next((item for item in candidates if item["path"].stem == outcome.target), None)
    if chosen is not None:
        destination_path = chosen["path"]
    elif current is not None:
        destination_path = current[0]
    else:
        return "skipped"
    moved = current is None or current[0] != destination_path
    if moved:
        _move_source_between_topics(root, source_name, destination_path, summary, title)
    _write_topic_synthesis(destination_path, outcome.body)
    rebuild_index(knowledge_base_id)
    append_log(
        knowledge_base_id,
        f"topic synthesis | {title}",
        f"Filed into [[topics/{destination_path.stem}]] by {outcome.model or 'model'}"
        + (" (moved from another topic page)." if moved else "."),
    )
    return "llm"


def _write_topic_synthesis(path: Path, body: str) -> None:
    content = path.read_text(encoding="utf-8")
    content = _replace_topic_synthesis(content, body)
    content = _with_topic_front_matter(content, summary=_summary(body))
    content = _with_topic_front_matter(content, sources=_topic_source_names(content))
    path.write_text(content, encoding="utf-8")


def _topic_page_with_source(root: Path, source_name: str) -> tuple[Path, str, str] | None:
    topics_root = root / "wiki" / "topics"
    marker = f"[[sources/{source_name}]]"
    for path in sorted(topics_root.glob("*.md")) if topics_root.exists() else []:
        content = path.read_text(encoding="utf-8")
        if marker in content:
            return path, _frontmatter_value(content, "title") or path.stem, content
    return None


def _topic_candidates(
    root: Path,
    title: str,
    summary: str,
    exclude: Path | None = None,
    limit: int = 5,
) -> list[dict[str, object]]:
    """Topic pages that could host a source, best token overlap first.

    Recall only: the ordering is a heuristic, the decision belongs to the model.
    """
    topics_root = root / "wiki" / "topics"
    if not topics_root.exists():
        return []
    probe_tokens = _tokens(f"{title} {summary}")
    scored: list[tuple[float, dict[str, object]]] = []
    for path in sorted(topics_root.glob("*.md")):
        if exclude is not None and path == exclude:
            continue
        page = _page_read(root, path)
        haystack = f"{page.title} {page.summary} {page.content}".casefold()
        hits = sum(haystack.count(token.casefold()) for token in probe_tokens)
        title_hits = sum(token.casefold() in page.title.casefold() for token in probe_tokens)
        if hits == 0 and title_hits == 0:
            continue
        score = 0.12 + (hits * 0.08) + (title_hits * 0.18)
        scored.append(
            (
                score,
                {
                    "path": path,
                    "title": page.title,
                    "summary": page.summary,
                    "content": page.content,
                    "title_hits": title_hits,
                },
            )
        )
    scored.sort(key=lambda item: (-item[0], str(item[1]["path"])))
    return [item[1] for item in scored[:limit]]


def _move_source_between_topics(
    root: Path,
    source_name: str,
    destination: Path,
    summary: str,
    title: str,
) -> None:
    """Relocate one source bullet when the model files it elsewhere.

    A topic page that loses its last source has nothing left to stand on, so it goes
    away rather than lingering as an empty shell.
    """
    topics_root = root / "wiki" / "topics"
    marker = f"[[sources/{source_name}]]"
    for path in sorted(topics_root.glob("*.md")):
        if path == destination:
            continue
        content = path.read_text(encoding="utf-8")
        if marker not in content:
            continue
        remaining = _drop_source_line(content, source_name)
        after = _topic_source_names(remaining)
        if not after:
            path.unlink(missing_ok=True)
            continue
        path.write_text(_with_topic_front_matter(remaining, sources=after) + "\n", encoding="utf-8")
    destination_content = _merge_topic(destination.read_text(encoding="utf-8"), summary, source_name, [])
    destination_content = _with_topic_front_matter(destination_content, sources=_topic_source_names(destination_content))
    destination.write_text(destination_content, encoding="utf-8")


def _drop_source_line(content: str, source_name: str) -> str:
    prefix = f"[[sources/{source_name}]]"
    remaining = "\n".join(line for line in content.splitlines() if prefix not in line).strip()
    if "## Sources" in remaining and not any("[[sources/" in line for line in remaining.splitlines()):
        remaining = remaining.split("## Sources", 1)[0].rstrip()
    return remaining


def _purpose_text(knowledge_base_id: int) -> str:
    """The workspace's stated intent, injected as context (never as a citation)."""
    path = workspace_path(knowledge_base_id) / "purpose.md"
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    body = re.sub(r"^---[\s\S]*?---\s*", "", content, count=1) if content.startswith("---") else content
    body = re.sub(r"^#{1,6}\s+.*$", "", body, flags=re.MULTILINE)
    return body.strip()[:1_200]


def _resolve_topic_page(
    root: Path,
    title: str,
    summary: str,
    document_id: int,
) -> tuple[Path, str, str]:
    """Deterministic destination for a source, used when no model is available.

    Deliberately strict: a single shared word ("AI", "ETF") must never merge two unrelated
    topics. Only two independent title tokens, or a title that already says the same thing,
    count as evidence. With a model configured, `complete_ingest` re-files the source using
    the model's judgement instead.
    """
    topics_root = root / "wiki" / "topics"
    probe_tokens = _tokens(title)
    best: tuple[int, Path, WikiPageRead] | None = None
    normalized_title = _normalized_title(title)
    for path in (sorted(topics_root.glob("*.md")) if topics_root.exists() else []):
        page = _page_read(root, path)
        if _normalized_title(page.title) == normalized_title:
            return path, page.title, path.read_text(encoding="utf-8")
        title_hits = sum(token.casefold() in page.title.casefold() for token in probe_tokens)
        if title_hits < 2:
            continue
        if best is None or title_hits > best[0]:
            best = (title_hits, path, page)
    if best is not None:
        return best[1], best[2].title, best[1].read_text(encoding="utf-8")
    slug = _slug(title) or f"topic-{document_id}"
    return topics_root / f"{slug}.md", title, _topic_template(title, summary)


def _normalized_title(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE).casefold()


def _topic_synthesis(content: str) -> str:
    """The part of a topic page the model is allowed to own."""
    if "## Evolving synthesis" not in content:
        return ""
    body = content.split("## Evolving synthesis", 1)[1]
    return body.split("## Sources", 1)[0].strip()


def _replace_topic_synthesis(content: str, synthesis: str) -> str:
    """Swap in a new synthesis body, keeping the front matter, the source list
    and any other section the user wrote on the page."""
    heading = "## Evolving synthesis"
    block = f"{heading}\n\n{synthesis.strip()}"
    if heading in content:
        head, tail = content.split(heading, 1)
        sections = re.split(r"^## ", tail, maxsplit=1, flags=re.MULTILINE)
        trailing = f"## {sections[1]}" if len(sections) > 1 else ""
        return f"{head.rstrip()}\n\n{block}".rstrip() + (f"\n\n{trailing.rstrip()}" if trailing else "") + "\n"
    index = content.find("## Sources")
    if index == -1:
        return f"{content.rstrip()}\n\n{block}\n"
    return f"{content[:index].rstrip()}\n\n{block}\n\n{content[index:].lstrip()}"


def _topic_source_names(content: str) -> list[str]:
    """Source slugs recorded by the page's own source list."""
    return list(dict.fromkeys(re.findall(r"\[\[sources/([^\]|]+)\]\]", content)))


def _with_topic_front_matter(
    content: str,
    *,
    summary: str | None = None,
    sources: list[str] | None = None,
) -> str:
    """Keep the machine-readable fields of a topic page in sync with its body."""
    if not content.startswith("---"):
        return content
    end = content.find("\n---", 3)
    if end == -1:
        return content
    front, rest = content[:end], content[end:]
    if summary is not None:
        front = re.sub(r"\nsummary:.*", "", front, count=1)
        front = f"{front}\nsummary: {_yaml(summary)}"
    if sources is not None:
        front = re.sub(r"\nsources:\s*\[[^\]]*\]", "", front, count=1)
        listed = ", ".join(f'"{name}"' for name in sources)
        front = f"{front}\nsources: [{listed}]"
    return f"{front}{rest}"


def list_pages(knowledge_base_id: int, page: int, page_size: int) -> tuple[list[WikiPageRead], int]:
    pages = [_page_read(root, item) for root, item in _iter_readable_page_paths(knowledge_base_id)]
    inbound = _inbound_counts(pages)
    enriched = [item.model_copy(update={"inbound_links": inbound.get(item.path, 0)}) for item in pages]
    enriched.sort(key=lambda item: (item.page_type, item.title.casefold()))
    offset = (page - 1) * page_size
    return enriched[offset : offset + page_size], len(enriched)


def get_page(knowledge_base_id: int, page_path: str) -> WikiPageRead | None:
    root = workspace_path(knowledge_base_id)
    path = _safe_page_path(root, page_path)
    if not path.exists() or not path.is_file():
        return None
    pages = [_page_read(root, item) for _, item in _iter_readable_page_paths(knowledge_base_id)]
    current = _page_read(root, path)
    inbound = _inbound_counts(pages)
    return current.model_copy(update={"inbound_links": inbound.get(current.path, 0)})


def save_page(knowledge_base_id: int, page_path: str, content: str) -> WikiPageRead:
    root = workspace_path(knowledge_base_id)
    path = _safe_page_path(root, page_path)
    if len(content.encode("utf-8")) > get_settings().wiki_max_page_bytes:
        raise WikiWorkspaceError("Wiki page is too large.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    rebuild_index(knowledge_base_id)
    return get_page(knowledge_base_id, page_path)  # type: ignore[return-value]


def query_wiki(
    knowledge_base_id: int,
    query: str,
    top_k: int,
    save_as: str | None = None,
    llm: LLMConfigurationInput | None = None,
    strategy_id: str | None = None,
    record_activity: bool = True,
) -> WikiQueryResponse:
    """Answer a question from the wiki pages.

    `record_activity=False` exists for replays and evaluations: they ask the same
    questions many times over and must not write hundreds of lines into `log.md`.
    """
    try:
        strategy = retrieval_strategy.get_strategy(strategy_id)
    except retrieval_strategy.UnknownStrategyError as exc:
        raise WikiWorkspaceError(str(exc)) from exc
    pages = [
        page
        for root, item in _iter_readable_page_paths(knowledge_base_id)
        if (page := _page_read(root, item)).path.startswith("wiki/")
    ]
    pages_by_path = {page.path: page for page in pages}
    # An optional single planning call rewrites the question into search terms. It only
    # ever adds terms; the ranking itself stays deterministic.
    planned_terms: tuple[str, ...] = ()
    if strategy.plan_query:
        planned_terms = llm_service.plan_query(query, _purpose_text(knowledge_base_id), override=llm)
    search_query = " ".join((query, *planned_terms)) if planned_terms else query

    query_tokens = _search_tokens(search_query)
    lexical = _lexical_scores(pages, query_tokens)
    semantic: dict[str, float] = {}
    if strategy.use_vectors:
        vectors = _page_vectors(knowledge_base_id, pages)
        if vectors is not None:
            semantic = _vector_scores(pages, vectors, search_query)
    vector_weight = get_settings().wiki_query_vector_weight if semantic else 0.0

    ranked: list[WikiQueryResult] = []
    for page in pages:
        # Lexical relevance stays the backbone so scores remain comparable between
        # strategies; the vector side only adds recall on top of it.
        relevance = min(1.0, lexical.get(page.path, 0.0) + (semantic.get(page.path, 0.0) * vector_weight))
        if relevance <= 0:
            continue
        title = page.title.casefold()
        title_coverage = len([token for token in query_tokens if token in title]) / len(query_tokens)
        score = round(min(0.99, 0.15 + (relevance * 0.55) + (title_coverage * 0.30)), 4)
        ranked.append(
            WikiQueryResult(
                path=page.path,
                title=page.title,
                page_type=page.page_type,
                summary=page.summary,
                snippet=_snippet(page.content, query_tokens),
                score=score,
                citations=[page.path],
            )
        )
    ranked.sort(key=lambda item: (-item.score, item.path))
    direct = ranked[:top_k]
    # Following the wiki's own links is what a page store can do that raw chunk
    # retrieval cannot, so linked neighbours join the evidence as extra context.
    settings = get_settings()
    # A weak result means the direct matches were not enough, so an "auto" strategy is
    # allowed to walk one link further instead of answering from the same evidence.
    hops = retrieval_strategy.resolve_hops(
        strategy,
        direct[0].score if direct else None,
        settings.wiki_query_deep_threshold,
    )
    related = _related_neighbours(
        direct,
        pages_by_path,
        _link_edges(pages),
        strategy.neighbour_limit,
        query_tokens,
        hops=hops,
    )
    results = direct + related
    deterministic_answer = _answer(query, direct)
    synthesis = llm_service.synthesize_answer(
        question=query,
        evidence=[
            llm_service.Evidence(
                reference=str(index + 1),
                title=result.title,
                content=pages_by_path[result.path].content if result.path in pages_by_path else result.snippet or result.summary,
                related=result.related,
            )
            for index, result in enumerate(results)
        ],
        mode="wiki",
        purpose=_purpose_text(knowledge_base_id),
        override=llm,
    )
    answer = synthesis.answer or deterministic_answer
    saved_path = None
    if save_as:
        slug = _slug(save_as) or f"query-{_today()}"
        path = f"wiki/queries/{slug}.md"
        content = _query_page(query, answer, direct)
        save_page(knowledge_base_id, path, content)
        append_log(knowledge_base_id, f"query | {query}", f"Saved synthesis to [[{path.removesuffix('.md')}]].")
        saved_path = path.removeprefix("wiki/")
    elif record_activity:
        append_log(knowledge_base_id, f"query | {query}", f"Read {len(direct)} matching wiki pages and {len(related)} linked pages.")
    return WikiQueryResponse(
        query=query,
        answer=answer,
        results=results,
        saved_path=saved_path,
        answer_mode="llm" if synthesis.answer else "deterministic",
        model=synthesis.model,
        model_error=synthesis.error,
        strategy=strategy.id,
        hops=hops,
        vectors=bool(semantic),
        planned=bool(planned_terms),
    )


def _related_neighbours(
    seeds: list[WikiQueryResult],
    pages_by_path: dict[str, WikiPageRead],
    edges: list[tuple[str, str]],
    limit: int,
    query_tokens: tuple[str, ...],
    hops: int = 1,
) -> list[WikiQueryResult]:
    """Pages reachable by following links out of the direct matches.

    `hops` is the caller's decision: a confident query only needs its immediate
    neighbours, a weak one may walk one link further. Every hop halves the confidence
    a page inherits, so a page two links away never outranks one link away. Candidates
    are ordered by how many direct matches reach them, which keeps a small set of
    matches from deciding the whole neighbourhood on their own.
    """
    if limit <= 0 or not seeds or hops < 1:
        return []

    adjacency: dict[str, set[str]] = {}
    for source, target in edges:
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)

    seed_paths = {seed.path for seed in seeds}
    inherited: dict[str, float] = {seed.path: seed.score for seed in seeds}
    reaches: dict[str, set[str]] = {}
    seen = set(seed_paths)
    frontier: dict[str, set[str]] = {path: {path} for path in seed_paths}

    for _ in range(hops):
        next_frontier: dict[str, set[str]] = {}
        for path, origins in frontier.items():
            base = inherited.get(path, 0.0)
            for neighbour in adjacency.get(path, ()):
                if neighbour in seen:
                    continue
                next_frontier.setdefault(neighbour, set()).update(origins)
                inherited[neighbour] = max(inherited.get(neighbour, 0.0), base * 0.5)
        if not next_frontier:
            break
        for neighbour, origins in next_frontier.items():
            reaches.setdefault(neighbour, set()).update(origins)
        seen |= set(next_frontier)
        frontier = next_frontier

    ordered = sorted(reaches, key=lambda path: (-len(reaches[path]), -inherited.get(path, 0.0), path))
    neighbours: list[WikiQueryResult] = []
    for path in ordered:
        if len(neighbours) >= limit:
            break
        page = pages_by_path.get(path)
        if page is None:
            continue
        neighbours.append(
            WikiQueryResult(
                path=path,
                title=page.title,
                page_type=page.page_type,
                summary=page.summary,
                snippet=_snippet(page.content, query_tokens),
                score=round(inherited.get(path, 0.0), 4),
                citations=[path],
                related=True,
            )
        )
    return neighbours


def lint_wiki(knowledge_base_id: int) -> WikiLintResponse:
    pages = [_page_read(root, item) for root, item in _iter_readable_page_paths(knowledge_base_id)]
    issues = _lint_pages(pages)
    append_log(knowledge_base_id, "lint | wiki health check", f"Checked {len(pages)} pages and found {len(issues)} issues.")
    return WikiLintResponse(healthy=not any(issue.severity == "error" for issue in issues), checked_pages=len(pages), issues=issues)


def _lint_pages(pages: list[WikiPageRead]) -> list[WikiIssue]:
    known = {page.path for page in pages}
    by_stem = {Path(page.path).stem: page.path for page in pages}
    inbound = _inbound_counts(pages)
    issues: list[WikiIssue] = []
    for page in pages:
        for link in _links(page.content):
            target = _resolve_link(link, page.path, known, by_stem)
            if target is None:
                issues.append(WikiIssue(severity="error", code="broken_link", path=page.path, message=f"找不到链接目标：[[{link}]]"))
        if page.path.startswith("wiki/") and page.page_type not in {"overview", "query"} and inbound.get(page.path, 0) == 0:
            issues.append(WikiIssue(severity="warning", code="orphan_page", path=page.path, message="没有其他页面链接到它。"))
        if not page.summary:
            issues.append(WikiIssue(severity="warning", code="missing_summary", path=page.path, message="页面缺少可用于索引的摘要。"))
    return issues


def graph(knowledge_base_id: int) -> tuple[list[WikiGraphNode], list[WikiGraphEdge]]:
    pages = [_page_read(root, item) for root, item in _iter_readable_page_paths(knowledge_base_id)]
    known = {page.path for page in pages}
    by_stem = {Path(page.path).stem: page.path for page in pages}
    inbound = _inbound_counts(pages)
    nodes = [WikiGraphNode(id=page.path, label=page.title, path=page.path, page_type=page.page_type, inbound_links=inbound.get(page.path, 0), outbound_links=page.outbound_links) for page in pages]
    edges = [WikiGraphEdge(source=source, target=target) for source, target in _link_edges(pages)]
    return nodes, edges


def _link_edges(pages: list[WikiPageRead]) -> list[tuple[str, str]]:
    """Every resolvable page-to-page link, the single source of wiki structure."""
    known = {page.path for page in pages}
    by_stem = {Path(page.path).stem: page.path for page in pages}
    edges: list[tuple[str, str]] = []
    for page in pages:
        for link in _links(page.content):
            target = _resolve_link(link, page.path, known, by_stem)
            if target and target != page.path:
                edges.append((page.path, target))
    return edges


def status(knowledge_base_id: int) -> WikiStatusResponse:
    root = initialize_workspace(knowledge_base_id, f"Knowledge base {knowledge_base_id}", None)
    pages = [_page_read(root, item) for _, item in _iter_readable_page_paths(knowledge_base_id)]
    lint_issues = _lint_pages(pages)
    return WikiStatusResponse(
        workspace_path=str(root),
        initialized=True,
        source_count=len(list((root / "raw" / "sources").glob("*.md"))),
        page_count=len(pages),
        topic_count=sum(page.page_type == "topic" for page in pages),
        orphan_count=sum(issue.code == "orphan_page" for issue in lint_issues),
        broken_link_count=sum(issue.code == "broken_link" for issue in lint_issues),
        recent_activity=_recent_log(root),
    )


def rebuild_index(knowledge_base_id: int) -> None:
    root = workspace_path(knowledge_base_id)
    if not root.exists():
        return
    pages = [_page_read(root, item) for _, item in _iter_page_paths(knowledge_base_id)]
    sections: dict[str, list[WikiPageRead]] = {}
    for page in pages:
        sections.setdefault(page.page_type, []).append(page)
    lines = ["# Wiki index", "", "> Generated from Markdown pages. Edit pages, not this file.", ""]
    for page_type in ("overview", "source", "topic", "entity", "query", "page"):
        group = sections.get(page_type, [])
        if not group:
            continue
        lines.extend([f"## {_page_type_label(page_type)}", ""])
        for page in sorted(group, key=lambda item: item.title.casefold()):
            lines.append(f"- [[{page.path.removesuffix('.md')}]] — {page.summary or '暂无摘要'}")
        lines.append("")
    (root / "index.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def append_log(knowledge_base_id: int, event: str, detail: str) -> None:
    root = workspace_path(knowledge_base_id)
    root.mkdir(parents=True, exist_ok=True)
    log_path = root / "log.md"
    if not log_path.exists():
        log_path.write_text("# Activity log\n", encoding="utf-8")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n## [{_today()}] {event}\n\n{detail}\n")


def _iter_page_paths(knowledge_base_id: int):
    root = workspace_path(knowledge_base_id)
    wiki_root = root / "wiki"
    if not wiki_root.exists():
        return
    for path in sorted(wiki_root.rglob("*.md")):
        if path.is_file():
            yield root, path


def _iter_readable_page_paths(knowledge_base_id: int):
    yield from _iter_page_paths(knowledge_base_id)
    root = workspace_path(knowledge_base_id)
    for path in (root / "index.md", root / "purpose.md"):
        if path.is_file():
            yield root, path


def _page_read(root: Path, path: Path) -> WikiPageRead:
    content = path.read_text(encoding="utf-8")
    relative = _relative(root, path)
    page_type = _page_type(relative)
    title = _frontmatter_value(content, "title") or _heading(content) or path.stem
    summary = _frontmatter_value(content, "summary") or _summary(content)
    stat = path.stat()
    return WikiPageRead(
        path=relative,
        title=title,
        page_type=page_type,
        summary=summary,
        content=content,
        updated_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        outbound_links=len(_links(content)),
    )


def _links(content: str) -> tuple[str, ...]:
    links = list(WIKILINK_PATTERN.findall(content))
    links.extend(MARKDOWN_LINK_PATTERN.findall(content))
    return tuple(dict.fromkeys(item.strip().removesuffix(".md") for item in links if item.strip()))


def _inbound_counts(pages: list[WikiPageRead]) -> dict[str, int]:
    known = {page.path for page in pages} | {"index.md", "purpose.md", ".wiki-schema.md"}
    by_stem = {Path(page.path).stem: page.path for page in pages}
    counts: Counter[str] = Counter()
    for page in pages:
        for link in _links(page.content):
            target = _resolve_link(link, page.path, known, by_stem)
            if target:
                counts[target] += 1
    return counts


def _resolve_link(link: str, source_path: str, known: set[str], by_stem: dict[str, str]) -> str | None:
    normalized = link.strip().lstrip("./").removesuffix(".md")
    candidates = [normalized, f"wiki/{normalized}", f"wiki/{normalized}.md", f"{normalized}.md"]
    source_dir = PurePosixPath(source_path).parent
    candidates.append(str(source_dir / normalized).removesuffix(".md"))
    for candidate in candidates:
        if candidate in known:
            return candidate
        if f"{candidate}.md" in known:
            return f"{candidate}.md"
    if normalized in {"index", "purpose", ".wiki-schema"}:
        return f"{normalized}.md"
    return by_stem.get(Path(normalized).stem)


def _safe_page_path(root: Path, page_path: str) -> Path:
    normalized = page_path.strip().replace("\\", "/").lstrip("/")
    candidate = PurePosixPath(normalized)
    root_page = normalized in {"index.md", "purpose.md"}
    if candidate.is_absolute() or ".." in candidate.parts or not normalized.endswith(".md") or (not normalized.startswith("wiki/") and not root_page):
        raise WikiWorkspaceError("Page path must be a relative Markdown path under wiki/ or a root index page.")
    path = (root / normalized).resolve()
    if root.resolve() not in path.parents:
        raise WikiWorkspaceError("Page path escapes the wiki workspace.")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _page_type(relative: str) -> str:
    parts = PurePosixPath(relative).parts
    if len(parts) > 1 and parts[0] == "wiki":
        if parts[1] == "overview.md":
            return "overview"
        return {"sources": "source", "topics": "topic", "entities": "entity", "queries": "query"}.get(parts[1], "page")
    return "page"


def _frontmatter_value(content: str, key: str) -> str:
    if not content.startswith("---"):
        return ""
    match = re.search(rf"^\s{{0,2}}{re.escape(key)}:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    return match.group(1).strip().strip('"\'') if match else ""


def _heading(content: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", content, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _summary(content: str) -> str:
    body = re.sub(r"^---[\s\S]*?---\s*", "", content, count=1) if content.startswith("---") else content
    body = re.sub(r"^#{1,6}\s+.*$", "", body, flags=re.MULTILINE)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    for line in body.splitlines():
        clean = re.sub(r"[`*_]", "", line).strip(" >-")
        if clean and not clean.startswith(("|", "- ")):
            return clean[:180]
    return ""


def _snippet(content: str, query_tokens: list[str]) -> str:
    body = _summary(content)
    if body:
        return body[:300]
    return content.strip()[:300]


def _answer(query: str, results: list[WikiQueryResult]) -> str:
    if not results:
        return f"知识库中暂时没有足够内容回答“{query}”。可以先摄取一份来源，或换一个更具体的问法。"
    lines = [f"基于知识库中 {len(results)} 个相关页面，关于“{query}”可以先关注："]
    for result in results[:5]:
        lines.append(f"- {result.summary or result.snippet} [[{result.path.removesuffix('.md')}]]")
    lines.append("\n以上结论来自 wiki 页面摘要，详细内容请打开引用页面核对原始来源。")
    return "\n".join(lines)


def _raw_source_page(
    title: str,
    content: str,
    source_type: str,
    file_name: str | None,
    source_url: str | None,
    source_platform: str | None,
    source_author: str | None,
    source_account: str | None,
    source_published_at: datetime | None,
    source_captured_at: datetime | None,
    source_policy: str,
    source_disclosures: list[str],
    content_hash: str,
) -> str:
    fields = [
        "---",
        "type: raw-source",
        f"title: {_yaml(title)}",
        f"source_type: {source_type}",
        f"file_name: {_yaml(file_name or '')}",
        f"source_url: {_yaml(source_url or '')}",
        f"source_platform: {_yaml(source_platform or '')}",
        f"source_author: {_yaml(source_author or '')}",
        f"source_account: {_yaml(source_account or '')}",
        f"source_published_at: {_iso_datetime(source_published_at)}",
        f"source_captured_at: {_iso_datetime(source_captured_at)}",
        f"source_policy: {source_policy}",
        f"source_disclosures: {_yaml(', '.join(source_disclosures))}",
        f"content_hash: {content_hash}",
        f"imported_at: {_today()}",
        "---",
    ]
    return "\n".join(fields) + f"\n\n# {title}\n\n{content.rstrip()}\n"


def _source_page(
    title: str,
    summary: str,
    source_name: str,
    topic_slug: str,
    content: str,
    tags: list[str],
    source_url: str | None = None,
    source_platform: str | None = None,
    source_author: str | None = None,
    source_account: str | None = None,
    source_published_at: datetime | None = None,
    source_policy: str = "full_text",
) -> str:
    tag_line = ", ".join(tags) if tags else ""
    external_source = ""
    if source_url:
        attribution = " · ".join(
            value
            for value in [source_platform, source_author, f"@{source_account}" if source_account else None]
            if value
        )
        published = _iso_datetime(source_published_at)
        external_source = "## External source\n\n"
        if attribution:
            external_source += f"{attribution}\n\n"
        external_source += f"[{source_url}]({source_url})\n\n"
        if published:
            external_source += f"Published at: {published}\n\n"
        external_source += f"Retention policy: `{source_policy}`\n\n"
    return f"---\ntype: source\ntitle: {_yaml(title)}\nsummary: {_yaml(summary)}\nsource_file: raw/sources/{source_name}.md\nsource_url: {_yaml(source_url or '')}\nsource_policy: {source_policy}\ntags: {tag_line}\n---\n\n# {title}\n\n{summary}\n\n{external_source}## Key takeaways\n\n{_bullets(content)}\n\n## Related topic\n\n[[topics/{topic_slug}]]\n\n## Raw source\n\n`raw/sources/{source_name}.md`\n"


def _topic_template(title: str, summary: str) -> str:
    return f"---\ntype: topic\ntitle: {_yaml(title)}\nsummary: {_yaml(summary)}\n---\n\n# {title}\n\n{summary}\n\n## Evolving synthesis\n\n"


def _merge_topic(content: str, summary: str, source_name: str, tags: list[str]) -> str:
    source_link = f"[[sources/{source_name}]]"
    if source_link in content:
        return content
    marker = "## Sources"
    if marker not in content:
        content = content.rstrip() + f"\n\n{marker}\n\n"
    content += f"- {source_link}: {summary}\n"
    if tags:
        content += f"\nTags: {', '.join(f'`{tag}`' for tag in tags)}\n"
    return content


def _bullets(content: str) -> str:
    sentences = [item.strip() for item in re.split(r"[。！？.!?\n]+", content) if item.strip()]
    return "\n".join(f"- {sentence[:180]}。" for sentence in sentences[:5]) or "- 未提取到摘要。"


def _query_page(query: str, answer: str, results: list[WikiQueryResult]) -> str:
    citations = "\n".join(f"- [[{result.path.removesuffix('.md')}]]: {result.summary}" for result in results)
    return f"---\ntype: query\ntitle: {_yaml(query)}\nsummary: {_yaml(answer.splitlines()[0])}\ndate: {_today()}\n---\n\n# {query}\n\n{answer}\n\n## Citations\n\n{citations or '- 暂无引用。'}\n"


def _schema_content(name: str) -> str:
    return f"# {name} wiki schema\n\n## Layers\n\n- `raw/`: immutable source snapshots.\n- `wiki/`: maintained Markdown pages.\n- `index.md`: content-oriented catalog.\n- `log.md`: append-only activity history.\n\n## Conventions\n\n- Use `[[path/without-extension]]` for links.\n- Keep source claims traceable to a page under `wiki/sources/`.\n- Treat `index.md` as generated navigation; edit the page it points to instead.\n- Do not store secrets or personal data in this workspace.\n"


def _recent_log(root: Path) -> list[str]:
    path = root / "log.md"
    if not path.exists():
        return []
    return [line[3:].strip() for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("## ")][-5:][::-1]


def _tokens(value: str) -> list[str]:
    return [item for item in TOKEN_PATTERN.findall(value.casefold()) if len(item) > 1]


def _lexical_scores(pages: list[WikiPageRead], query_tokens: tuple[str, ...]) -> dict[str, float]:
    """BM25 over bigram terms, mapped to 0..1 so scores stay comparable across queries.

    Ordinary term counts would let the longest page win, because bigram terms make the
    count grow with the length of the query. BM25 saturates term frequency and
    normalises by page length instead, which is what separates four pages that all
    mention the same phrase.
    """
    if not query_tokens or not pages:
        return {}

    haystacks = {page.path: f"{page.title} {page.summary} {page.content}".casefold() for page in pages}
    lengths = {path: max(1, len(text)) for path, text in haystacks.items()}
    average_length = sum(lengths.values()) / len(lengths)

    raw: dict[str, float] = {}
    ceiling = 0.0
    for token in query_tokens:
        document_frequency = sum(1 for text in haystacks.values() if token in text)
        if document_frequency == 0:
            continue
        idf = math.log(1 + (len(pages) - document_frequency + 0.5) / (document_frequency + 0.5))
        ceiling += idf * (BM25_K1 + 1)
        for path, text in haystacks.items():
            frequency = text.count(token)
            if frequency == 0:
                continue
            saturation = (frequency * (BM25_K1 + 1)) / (
                frequency + BM25_K1 * (1 - BM25_B + BM25_B * lengths[path] / average_length)
            )
            raw[path] = raw.get(path, 0.0) + idf * saturation

    if ceiling <= 0:
        return {}
    return {path: min(1.0, value / ceiling) for path, value in raw.items()}


# Page embeddings rarely change and are cheap to score against, so they are cached per
# workspace and rebuilt only when a page's file timestamp moves.
_page_vector_cache: dict[int, tuple[tuple, dict[str, list[float]]]] = {}


def _page_vectors(knowledge_base_id: int, pages: list[WikiPageRead]) -> dict[str, list[float]] | None:
    """Embed page titles, summaries and openings, or None when that is not possible.

    Returning None rather than raising keeps a semantic strategy usable on a machine
    without the embedding model: it quietly degrades to the lexical ranking.
    """
    fingerprint = tuple(sorted((page.path, page.updated_at.isoformat()) for page in pages))
    cached = _page_vector_cache.get(knowledge_base_id)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    payload = [f"{page.title}\n{page.summary}\n{page.content[:1_500]}" for page in pages]
    try:
        vectors = embedding_service.embed_texts(payload)
    except embedding_service.EmbeddingError:
        return None
    if len(vectors) != len(pages):
        return None

    mapping = {page.path: vector for page, vector in zip(pages, vectors)}
    _page_vector_cache[knowledge_base_id] = (fingerprint, mapping)
    return mapping


def _vector_scores(pages: list[WikiPageRead], vectors: dict[str, list[float]], query: str) -> dict[str, float]:
    """Cosine similarity per page, above the floor. Embeddings are normalised, so a dot product.

    The floor matters: without it every query returns the least-unrelated pages, which looks
    like recall but is noise, and it hides the honest answer "this workspace cannot answer
    that question".
    """
    try:
        query_vector = embedding_service.embed_texts([query])[0]
    except embedding_service.EmbeddingError:
        return {}

    floor = get_settings().wiki_query_vector_floor
    scores: dict[str, float] = {}
    for page in pages:
        page_vector = vectors.get(page.path)
        if page_vector is None or len(page_vector) != len(query_vector):
            continue
        similarity = sum(left * right for left, right in zip(page_vector, query_vector))
        if similarity >= floor:
            scores[page.path] = similarity
    return scores


def _search_tokens(value: str) -> tuple[str, ...]:
    """Query terms used to score pages.

    Latin words stay whole; a run of CJK characters is split into overlapping
    bigrams, which is what lets 如何配置资产 match a page about 资产配置 without
    shipping a segmenter. It fixes word order and inserted particles, not
    synonyms: 小孩子 still will not find 闰土, that needs vectors.
    """
    tokens: list[str] = [word.casefold() for word in LATIN_TOKEN_PATTERN.findall(value) if len(word) > 1]
    for run in CJK_RUN_PATTERN.findall(value):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tuple(dict.fromkeys(tokens))


def _slug(value: str) -> str:
    return SAFE_SLUG_PATTERN.sub("-", value.casefold()).strip("-")[:80]


def _yaml(value: str) -> str:
    return '"' + value.replace('"', '\\"').replace("\n", " ") + '"'


def _iso_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _page_type_label(page_type: str) -> str:
    return {"overview": "Overview", "source": "Sources", "topic": "Topics", "entity": "Entities", "query": "Queries", "page": "Pages"}.get(page_type, "Pages")
