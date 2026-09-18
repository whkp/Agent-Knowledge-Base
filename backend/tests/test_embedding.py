import sys
import types

from app.services import embedding_service


def test_embedding_service_loads_a_cached_snapshot_without_contacting_the_hub(monkeypatch):
    calls: list[tuple[str, bool]] = []

    class FakeSentenceTransformer:
        def __init__(self, model_name: str, local_files_only: bool = False):
            calls.append((model_name, local_files_only))

    monkeypatch.setattr(embedding_service, "_local_model_reference", lambda model_name: "/cache/example-model")
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    assert isinstance(embedding_service.EmbeddingService("example/model").model, FakeSentenceTransformer)
    assert calls == [("/cache/example-model", True)]


def test_embedding_service_uses_network_only_when_cached_snapshot_is_missing(monkeypatch):
    calls: list[tuple[str, bool]] = []

    class FakeSentenceTransformer:
        def __init__(self, model_name: str, local_files_only: bool = False):
            calls.append((model_name, local_files_only))

    monkeypatch.setattr(embedding_service, "_local_model_reference", lambda model_name: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    assert isinstance(embedding_service.EmbeddingService("example/model").model, FakeSentenceTransformer)
    assert calls == [("example/model", False)]
