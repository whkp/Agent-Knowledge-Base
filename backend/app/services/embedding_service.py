from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings


class EmbeddingError(RuntimeError):
    pass


class EmbeddingService:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model: Any | None = None

    @property
    def model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError("sentence-transformers is not installed.") from exc

            try:
                # Resolve an actual snapshot directory first. Some transformers releases
                # still make a Hub metadata call when given a repository ID together with
                # local_files_only=True.
                self._model = SentenceTransformer(_local_model_reference(self.model_name), local_files_only=True)
            except Exception:
                try:
                    self._model = SentenceTransformer(self.model_name)
                except Exception as exc:
                    raise EmbeddingError("Failed to load the embedding model from the local cache or network.") from exc
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        try:
            embeddings = self.model.encode(texts, normalize_embeddings=True)
        except Exception as exc:
            raise EmbeddingError("Failed to generate embeddings.") from exc

        return [list(map(float, embedding)) for embedding in embeddings]


@lru_cache
def get_embedding_service() -> EmbeddingService:
    settings = get_settings()
    return EmbeddingService(settings.embedding_model_name)


def embed_texts(texts: list[str]) -> list[list[float]]:
    return get_embedding_service().embed_texts(texts)


def _local_model_reference(model_name: str) -> str:
    local_path = Path(model_name).expanduser()
    if local_path.exists():
        return str(local_path)

    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id=model_name, local_files_only=True)
