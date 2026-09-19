"""Re-ingesting a source, and updating one that already exists.

Both paths exist so that provenance is not silently duplicated (two rows for one source)
or silently destroyed (delete-and-re-ingest produces a new document id, a new source file
and a new row in the topic page's source list).
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import document_service
from tests.conftest import create_kb


def snapshot(kb: dict, url: str, title: str, content: str, **overrides) -> dict:
    payload = {
        "title": title,
        "content": content,
        "source_url": url,
        "platform": "web",
        "author": "四个木",
        "account": "四个木的笔记",
        "captured_at": "2026-09-01T09:00:00+00:00",
        "tags": ["资产配置"],
    }
    payload.update(overrides)
    return {"payload": payload, "kb": kb}


def ingest_snapshot(client: TestClient, kb: dict, url: str, title: str, content: str, **overrides):
    return client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/source-snapshot",
        json=snapshot(kb, url, title, content, **overrides)["payload"],
    )


def workspace_file(kb: dict, relative: str) -> Path:
    return Path(get_settings().wiki_root_dir) / f"kb-{kb['id']}" / relative


def test_re_ingesting_the_same_source_is_rejected_instead_of_duplicated(client: TestClient):
    kb = create_kb(client, "投资笔记")
    first = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "资产配置", "content": "先建立应急金，再配置权益类资产。"},
    )
    assert first.status_code == 201

    again = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "资产配置", "content": "先建立应急金，再配置权益类资产。"},
    )

    assert again.status_code == 409
    # The error has to be actionable: it names the document to update instead.
    assert str(first.json()["id"]) in again.json()["detail"]
    assert f"documents/{first.json()['id']}" in again.json()["detail"]
    listing = client.get(f"/api/knowledge-bases/{kb['id']}/documents").json()
    assert listing["total"] == 1


def test_reuploading_the_same_file_is_rejected(client: TestClient):
    kb = create_kb(client, "投资笔记")
    files = {"file": ("notes.txt", b"same bytes", "text/plain")}
    form = {"title": "笔记", "tags": "a,b"}
    first = client.post(f"/api/knowledge-bases/{kb['id']}/documents/file", data=form, files=files)
    assert first.status_code == 201

    again = client.post(f"/api/knowledge-bases/{kb['id']}/documents/file", data=form, files=files)

    assert again.status_code == 409


def test_identical_text_from_another_source_stays_a_second_source(client: TestClient):
    """Two publications of the same text are two sources: collapsing them loses provenance."""
    kb = create_kb(client, "投资笔记")
    body = "同一篇文章被两个平台转载，正文完全一样。"

    on_platform_a = ingest_snapshot(client, kb, "https://a.example/p/1", "转载 A", body)
    on_platform_b = ingest_snapshot(client, kb, "https://b.example/p/2", "转载 B", body)

    assert on_platform_a.status_code == 201
    assert on_platform_b.status_code == 201
    assert client.get(f"/api/knowledge-bases/{kb['id']}/documents").json()["total"] == 2
    assert client.get(f"/api/knowledge-bases/{kb['id']}/wiki/status").json()["source_count"] == 2


def test_update_keeps_the_source_paths_and_the_topic_source_row(client: TestClient):
    kb = create_kb(client, "投资笔记")
    created = ingest_snapshot(
        client, kb, "https://a.example/p/1", "资产配置入门", "先建立应急金，再配置权益类资产。"
    ).json()

    updated = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{created['id']}",
        json={"content": "先建立应急金，再配置权益类资产。新手应优先配置低波动资产。"},
    )

    assert updated.status_code == 200
    body = updated.json()
    assert body["source_path"] == created["source_path"]
    assert body["raw_path"] == created["raw_path"]
    assert body["topic_path"] == created["topic_path"]
    assert body["content_hash"] != created["content_hash"]

    topic = workspace_file(kb, body["topic_path"]).read_text(encoding="utf-8")
    source_link = f"[[sources/{Path(body['source_path']).stem}]]"
    assert topic.count(source_link) == 1, "the topic page must not gain a second row for the same source"
    assert "新手应优先配置低波动资产" in topic
    assert "Tags: `资产配置`" in topic, "tags survive an update that did not restate them"
    assert topic.count("Tags:") == 1, "a refreshed source must not accumulate tags lines"

    raw = workspace_file(kb, body["raw_path"]).read_text(encoding="utf-8")
    assert "新手应优先配置低波动资产" in raw
    assert body["content_hash"] in raw, "the raw snapshot records the hash of the content it holds"


def test_update_replaces_chunks_and_keeps_omitted_metadata(client: TestClient):
    kb = create_kb(client, "投资笔记")
    created = ingest_snapshot(
        client, kb, "https://a.example/p/1", "资产配置入门", "第一版：只讲应急金。" * 40
    ).json()

    body = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{created['id']}",
        json={"content": "第二版：应急金、保险、指数基金。" * 40},
    ).json()

    assert body["chunks"], "a non-empty document must still be indexed"
    detail = client.get(f"/api/documents/{created['id']}").json()
    assert {chunk["id"] for chunk in detail["chunks"]} == {chunk["id"] for chunk in body["chunks"]}
    assert all("第二版" in chunk["content"] for chunk in body["chunks"])
    assert not any("第一版" in chunk["content"] for chunk in body["chunks"]), "stale chunks must be gone"
    assert [chunk["chunk_index"] for chunk in body["chunks"]] == list(range(len(body["chunks"])))
    assert body["content"].startswith("第二版")
    # Provenance the caller did not restate is kept, not cleared.
    assert body["source_url"] == created["source_url"]
    assert body["source_platform"] == created["source_platform"]
    assert body["source_author"] == created["source_author"]
    assert body["source_captured_at"] == created["source_captured_at"]


def test_update_can_refresh_source_metadata_and_the_title(client: TestClient):
    kb = create_kb(client, "投资笔记")
    created = ingest_snapshot(client, kb, "https://a.example/p/1", "资产配置入门", "第一版。").json()

    body = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{created['id']}",
        json={
            "title": "资产配置入门（修订）",
            "content": "第二版。",
            "source_captured_at": "2026-09-19T10:00:00+00:00",
            "tags": ["资产配置", "修订"],
        },
    ).json()

    assert body["title"] == "资产配置入门（修订）"
    assert body["source_captured_at"].startswith("2026-09-19")
    assert body["source_path"] == created["source_path"], "a corrected title keeps the same page path"
    topic = workspace_file(kb, body["topic_path"]).read_text(encoding="utf-8")
    assert "Tags: `资产配置`, `修订`" in topic


def test_update_logs_the_re_ingest(client: TestClient):
    kb = create_kb(client, "投资笔记")
    created = ingest_snapshot(client, kb, "https://a.example/p/1", "资产配置入门", "第一版。").json()

    client.put(f"/api/knowledge-bases/{kb['id']}/documents/{created['id']}", json={"content": "第二版。"})

    log = workspace_file(kb, "log.md").read_text(encoding="utf-8")
    assert "update | 资产配置入门" in log


def test_update_rejects_empty_content(client: TestClient):
    kb = create_kb(client, "投资笔记")
    created = ingest_snapshot(client, kb, "https://a.example/p/1", "资产配置入门", "第一版。").json()

    response = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{created['id']}",
        json={"content": "   "},
    )

    assert response.status_code == 422


def test_update_cannot_target_a_document_of_another_workspace(client: TestClient):
    kb = create_kb(client, "投资笔记")
    other = create_kb(client, "另一个库", "别的")
    created = ingest_snapshot(client, kb, "https://a.example/p/1", "资产配置入门", "第一版。").json()

    response = client.put(
        f"/api/knowledge-bases/{other['id']}/documents/{created['id']}",
        json={"content": "越权写入"},
    )

    assert response.status_code == 404


def test_update_reports_a_missing_document(client: TestClient):
    kb = create_kb(client, "投资笔记")

    response = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/9999",
        json={"content": "不存在的文档"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found."


def test_update_replaces_the_document_vectors(client: TestClient, monkeypatch):
    """The indexed copy has to follow the new content, without touching other documents.

    Uses a fake vector store: the ordering matters (new vectors are written before the
    stale ones are dropped), so the calls themselves are what this test checks.
    """
    added: list[tuple[list[str], list[list[float]]]] = []
    deleted: list[list[str]] = []

    monkeypatch.setattr(
        document_service.embedding_service,
        "embed_texts",
        lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
    )
    monkeypatch.setattr(
        document_service.chroma_client,
        "add_chunks",
        lambda vector_ids, embeddings, documents, metadatas: added.append((vector_ids, embeddings)),
    )
    monkeypatch.setattr(
        document_service.chroma_client,
        "delete_chunks",
        lambda vector_ids: deleted.append(vector_ids),
    )
    monkeypatch.setenv("VECTOR_INDEX_ENABLED", "true")
    get_settings.cache_clear()

    kb = create_kb(client, "投资笔记")
    created = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "资产配置", "content": "第一版内容。" * 60},
    ).json()
    assert len(added) == 1
    first_batch = list(added[0][0])
    assert first_batch

    updated = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{created['id']}",
        json={"content": "第二版内容。" * 60},
    ).json()

    assert len(added) == 2, "the new content must be embedded"
    second_batch = added[1][0]
    assert set(second_batch).isdisjoint(first_batch), "re-indexed chunks carry new vector ids"
    assert deleted == [first_batch], "the stale vectors are dropped only after the new ones land"
    assert {chunk["vector_id"] for chunk in updated["chunks"]} == set(second_batch)


def test_a_metadata_only_update_does_not_rewrite_vectors(client: TestClient, monkeypatch):
    """Same content means the indexed copy is still correct; re-embedding it is wasted work."""
    added: list[list[str]] = []
    monkeypatch.setattr(document_service.embedding_service, "embed_texts", lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
    monkeypatch.setattr(
        document_service.chroma_client,
        "add_chunks",
        lambda vector_ids, embeddings, documents, metadatas: added.append(list(vector_ids)),
    )
    monkeypatch.setattr(document_service.chroma_client, "delete_chunks", lambda vector_ids: None)
    monkeypatch.setenv("VECTOR_INDEX_ENABLED", "true")
    get_settings.cache_clear()

    kb = create_kb(client, "投资笔记")
    created = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "资产配置", "content": "内容没有变化。" * 60},
    ).json()

    updated = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{created['id']}",
        json={"content": created["content"], "source_captured_at": "2026-09-19T12:00:00+00:00"},
    ).json()

    assert len(added) == 1, "the vector index was not touched again"
    assert updated["source_captured_at"].startswith("2026-09-19")
    assert {chunk["vector_id"] for chunk in updated["chunks"]} == set(added[0])


def test_updating_the_only_source_of_a_topic_refreshes_its_summary(client: TestClient):
    """A one-source topic page must not end up contradicting its own source list."""
    kb = create_kb(client, "投资笔记")
    created = ingest_snapshot(
        client, kb, "https://a.example/p/1", "资产配置入门", "第一版：只讲应急金。"
    ).json()
    topic_before = workspace_file(kb, created["topic_path"]).read_text(encoding="utf-8")
    assert "第一版：只讲应急金。" in topic_before

    body = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{created['id']}",
        json={"content": "第二版：应急金、保险、指数基金。"},
    ).json()

    topic = workspace_file(kb, body["topic_path"]).read_text(encoding="utf-8")
    # Front matter, the opening paragraph and the source row all describe the same source,
    # so all three have to move together.
    assert topic.count("第二版：应急金、保险、指数基金。") == 3
    assert "第一版：只讲应急金。" not in topic
    assert "## Sources\n\n- " in topic, "the source list heading stays on its own line"


def test_a_topic_that_aggregates_sources_keeps_its_prose(client: TestClient):
    """The narrative of a multi-source page is curation, not something an update rewrites."""
    kb = create_kb(client, "投资笔记")
    # Same title, different sources: the deterministic rule files them on one topic page.
    first = ingest_snapshot(client, kb, "https://a.example/p/1", "资产配置入门", "来源甲的结论。").json()
    second = ingest_snapshot(client, kb, "https://a.example/p/2", "资产配置入门", "来源乙的结论。").json()
    assert second["topic_path"] == first["topic_path"]

    body = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{first['id']}",
        json={"content": "来源甲的原话（修订）。"},
    ).json()

    topic = workspace_file(kb, body["topic_path"]).read_text(encoding="utf-8")
    assert topic.count("[[sources/") == 2, "one row per source"
    assert "来源甲的原话（修订）。" in topic, "the refreshed row shows the current summary"
    assert "来源乙的结论。" in topic, "the other source is untouched"
    assert "来源甲的结论。" in topic, "an aggregated page keeps the prose it was built on"


def test_switching_a_source_to_link_only_drops_its_vectors(client: TestClient, monkeypatch):
    """A policy change is an indexing change: unread vectors must not be left behind."""
    added: list[list[str]] = []
    deleted: list[list[str]] = []
    monkeypatch.setattr(document_service.embedding_service, "embed_texts", lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
    monkeypatch.setattr(
        document_service.chroma_client,
        "add_chunks",
        lambda vector_ids, embeddings, documents, metadatas: added.append(list(vector_ids)),
    )
    monkeypatch.setattr(document_service.chroma_client, "delete_chunks", lambda vector_ids: deleted.append(list(vector_ids)))
    monkeypatch.setenv("VECTOR_INDEX_ENABLED", "true")
    get_settings.cache_clear()

    kb = create_kb(client, "投资笔记")
    created = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/text",
        json={"title": "只留链接的来源", "content": "正文会被撤下。" * 40, "tags": ["验证"]},
    ).json()
    assert created["tags"] == ["验证"], "tags are kept on the document, not only merged into the wiki"

    body = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{created['id']}",
        json={"content": "只剩链接与一句说明。", "source_policy": "link_only"},
    ).json()

    assert body["source_policy"] == "link_only"
    assert body["chunks"] == [], "a link-only source is not indexed"
    assert len(added) == 1, "no new vectors are written for unindexed content"
    assert deleted == [added[0]], "the vectors that are no longer readable are dropped"


def test_a_document_created_from_a_file_can_be_updated_in_place(client: TestClient):
    """The update path is not restricted by how the document was ingested."""
    kb = create_kb(client, "投资笔记")
    created = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/file",
        data={"title": "手记", "tags": "笔记"},
        files={"file": ("notes.txt", "第一版手记。".encode(), "text/plain")},
    ).json()

    body = client.put(
        f"/api/knowledge-bases/{kb['id']}/documents/{created['id']}",
        json={"content": "第二版手记。"},
    ).json()

    assert body["source_path"] == created["source_path"]
    assert body["file_name"] == "notes.txt", "the file the document came from is still recorded"
    assert all("第二版" in chunk["content"] for chunk in body["chunks"])
