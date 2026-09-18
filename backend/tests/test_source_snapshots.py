from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.config import get_settings
from app.db.database import ensure_schema_compatibility
from app.services import document_service
from tests.conftest import create_kb


def source_snapshot_payload() -> dict:
    return {
        "title": "硅谷居士：指数投资观察",
        "content": "这是保存在本地的完整原帖快照。长期配置需要考虑风险承受能力和投资期限。",
        "source_url": "https://x.com/SVScholar/status/2088799040133845219",
        "platform": "x",
        "author": "硅谷居士",
        "account": "SVScholar",
        "published_at": "2026-08-16T12:30:00+00:00",
        "captured_at": "2026-08-17T08:00:00+00:00",
        "source_policy": "full_text",
        "disclosures": ["不构成投资建议", "原作者可能持仓"],
        "tags": ["nasdaq", "long-term"],
    }


def test_source_snapshot_persists_original_text_metadata_and_raw_page(client: TestClient):
    kb = create_kb(client, "投资资料")
    payload = source_snapshot_payload()

    response = client.post(f"/api/knowledge-bases/{kb['id']}/documents/source-snapshot", json=payload)

    assert response.status_code == 201
    document = response.json()
    assert document["source_type"] == "source_snapshot"
    assert document["content"] == payload["content"]
    assert document["source_url"] == payload["source_url"]
    assert document["source_platform"] == "x"
    assert document["source_author"] == "硅谷居士"
    assert document["source_account"] == "SVScholar"
    assert document["source_policy"] == "full_text"
    assert document["source_disclosures"] == payload["disclosures"]
    assert document["content_hash"] == sha256(payload["content"].encode("utf-8")).hexdigest()

    workspace = Path(get_settings().wiki_root_dir) / f"kb-{kb['id']}"
    raw_path = workspace / "raw" / "sources" / f"{document['id']}-硅谷居士-指数投资观察.md"
    raw_source = raw_path.read_text(encoding="utf-8")
    assert payload["content"] in raw_source
    assert payload["source_url"] in raw_source
    assert "source_policy: full_text" in raw_source
    assert document["content_hash"] in raw_source

    source_page = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages/wiki/sources/{document['id']}-硅谷居士-指数投资观察.md")
    assert source_page.status_code == 200
    assert payload["source_url"] in source_page.json()["content"]

    delete_response = client.delete(f"/api/documents/{document['id']}")
    assert delete_response.status_code == 204
    assert not raw_path.exists()


def test_source_snapshot_writes_traceability_metadata_to_chroma(client: TestClient, monkeypatch):
    kb = create_kb(client, "投资资料")
    captured: dict = {}

    monkeypatch.setattr(document_service, "get_settings", lambda: type("Settings", (), {"vector_index_enabled": True})())
    monkeypatch.setattr(document_service.embedding_service, "embed_texts", lambda texts: [[0.1, 0.2] for _ in texts])
    monkeypatch.setattr(
        document_service.chroma_client,
        "add_chunks",
        lambda vector_ids, embeddings, documents, metadatas: captured.update(
            vector_ids=vector_ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        ),
    )

    response = client.post(
        f"/api/knowledge-bases/{kb['id']}/documents/source-snapshot",
        json=source_snapshot_payload(),
    )

    assert response.status_code == 201
    metadata = captured["metadatas"][0]
    assert metadata["source_url"] == "https://x.com/SVScholar/status/2088799040133845219"
    assert metadata["source_platform"] == "x"
    assert metadata["source_author"] == "硅谷居士"
    assert metadata["source_account"] == "SVScholar"
    assert metadata["source_policy"] == "full_text"
    assert metadata["content_hash"] == response.json()["content_hash"]


def test_source_snapshot_rejects_non_http_url(client: TestClient):
    kb = create_kb(client)
    payload = source_snapshot_payload()
    payload["source_url"] = "file:///private/notes.txt"

    response = client.post(f"/api/knowledge-bases/{kb['id']}/documents/source-snapshot", json=payload)

    assert response.status_code == 422


def test_link_only_snapshot_is_auditable_but_excluded_from_rag_vectors(client: TestClient, monkeypatch):
    kb = create_kb(client)
    vector_calls: list[object] = []
    monkeypatch.setattr(document_service, "get_settings", lambda: type("Settings", (), {"vector_index_enabled": True})())
    monkeypatch.setattr(document_service.chroma_client, "add_chunks", vector_calls.append)
    payload = source_snapshot_payload()
    payload["source_policy"] = "link_only"
    payload["content"] = "原帖正文未保留；此记录仅用于追踪来源链接与人工审查状态。"

    response = client.post(f"/api/knowledge-bases/{kb['id']}/documents/source-snapshot", json=payload)

    assert response.status_code == 201
    document = response.json()
    assert document["source_policy"] == "link_only"
    assert document["chunks"] == []
    assert vector_calls == []


def test_content_hash_is_stable_for_identical_source_text():
    content = "原帖内容\n第二行"
    assert document_service.content_hash(content) == document_service.content_hash(content)
    assert document_service.content_hash(content) != document_service.content_hash(content + "。")


def test_existing_sqlite_documents_table_is_upgraded_with_source_columns(tmp_path):
    database_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE documents ("
                "id INTEGER PRIMARY KEY, "
                "knowledge_base_id INTEGER NOT NULL, "
                "title VARCHAR(200) NOT NULL, "
                "source_type VARCHAR(20) NOT NULL, "
                "file_name VARCHAR(255), "
                "content TEXT NOT NULL, "
                "created_at DATETIME NOT NULL, "
                "updated_at DATETIME NOT NULL"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO documents (id, knowledge_base_id, title, source_type, content, created_at, updated_at) "
                "VALUES (1, 1, '旧文档', 'text', '旧内容', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    ensure_schema_compatibility(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("documents")}
    assert {"source_url", "source_policy", "source_disclosures", "content_hash"}.issubset(columns)
    with engine.connect() as connection:
        migrated = connection.execute(text("SELECT source_policy, source_disclosures, content_hash FROM documents WHERE id = 1")).one()
    assert migrated.source_policy == "full_text"
    assert migrated.source_disclosures == "[]"
    assert migrated.content_hash == sha256("旧内容".encode("utf-8")).hexdigest()
