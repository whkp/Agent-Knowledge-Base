from fastapi.testclient import TestClient

from tests.conftest import create_kb


def rate(client: TestClient, kb_id: int, **overrides) -> dict:
    payload = {
        "mode": "wiki",
        "query": "资产配置怎么开始",
        "rating": 1,
        "answer": "先建立应急金。",
        "answer_mode": "deterministic",
        "source_paths": ["wiki/topics/资产配置入门.md"],
    }
    payload.update(overrides)
    return client.post(f"/api/knowledge-bases/{kb_id}/feedback", json=payload)


def test_rating_is_recorded_with_its_context(client: TestClient):
    kb = create_kb(client)

    response = rate(client, kb["id"], rating=-1, note="引用页面和问题无关")

    assert response.status_code == 201
    data = response.json()
    assert data["rating"] == -1
    assert data["note"] == "引用页面和问题无关"
    assert data["query"] == "资产配置怎么开始"
    assert data["source_paths"] == ["wiki/topics/资产配置入门.md"]
    assert data["knowledge_base_id"] == kb["id"]
    assert data["created_at"]


def test_note_is_optional_and_blank_notes_become_empty(client: TestClient):
    kb = create_kb(client)

    assert rate(client, kb["id"], note=None).json()["note"] is None
    assert rate(client, kb["id"], note="   ").json()["note"] is None


def test_only_thumbs_up_or_down_is_accepted(client: TestClient):
    kb = create_kb(client)

    for rating in (0, 2, -2):
        assert rate(client, kb["id"], rating=rating).status_code == 422


def test_empty_query_is_rejected(client: TestClient):
    kb = create_kb(client)

    assert rate(client, kb["id"], query="   ").status_code == 422


def test_unknown_knowledge_base_is_not_found(client: TestClient):
    assert rate(client, 999, rating=1).status_code == 404


def test_signal_can_be_read_back_with_totals(client: TestClient):
    kb = create_kb(client)
    rate(client, kb["id"], query="a", rating=1)
    rate(client, kb["id"], query="b", rating=1)
    rate(client, kb["id"], query="c", rating=-1, note="没用")

    page = client.get(f"/api/knowledge-bases/{kb['id']}/feedback").json()

    assert page["total"] == 3
    assert page["positive"] == 2
    assert page["negative"] == 1
    assert [item["query"] for item in page["items"]] == ["c", "b", "a"]
    assert page["items"][0]["note"] == "没用"


def test_feedback_is_not_written_into_the_wiki(client: TestClient):
    """A rating is business data; it must never become a Markdown page."""
    kb = create_kb(client)
    before = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages?page_size=50").json()

    rate(client, kb["id"], rating=-1, note="这条反馈不该出现在 wiki 里")

    after = client.get(f"/api/knowledge-bases/{kb['id']}/wiki/pages?page_size=50").json()
    assert after["total"] == before["total"]
    assert "这条反馈不该出现在 wiki 里" not in str(after)


def test_a_thumbs_down_can_name_the_citations_that_misled_it(client: TestClient):
    kb = create_kb(client)

    created = rate(
        client,
        kb["id"],
        rating=-1,
        note="引用的第一页和问题无关",
        bad_paths=["wiki/topics/资产配置入门.md", "wiki/sources/10-四个木.md"],
    ).json()

    assert created["bad_paths"] == ["wiki/topics/资产配置入门.md", "wiki/sources/10-四个木.md"]
    listed = client.get(f"/api/knowledge-bases/{kb['id']}/feedback").json()["items"][0]
    assert listed["bad_paths"] == created["bad_paths"]


def test_bad_paths_default_to_empty_and_deduplicate(client: TestClient):
    kb = create_kb(client)

    assert rate(client, kb["id"], rating=-1).json()["bad_paths"] == []
    assert rate(client, kb["id"], rating=-1, bad_paths=["a.md", "a.md", "  "]).json()["bad_paths"] == ["a.md"]


def test_a_thumbs_up_cannot_mark_a_citation_as_wrong(client: TestClient):
    """Silently dropping the field would leave the caller believing it was recorded."""
    kb = create_kb(client)

    response = rate(client, kb["id"], rating=1, bad_paths=["wiki/topics/资产配置入门.md"])

    assert response.status_code == 422
    assert "wrong" in response.text
