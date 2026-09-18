from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AgentKB API"
    database_url: str = "sqlite:///./data/agentkb.db"
    chroma_persist_dir: str = "./chroma"
    chroma_collection_name: str = "agentkb_chunks"
    embedding_model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    vector_index_enabled: bool = True
    chunk_size: int = 500
    chunk_overlap: int = 80
    max_txt_file_bytes: int = 1_048_576
    wiki_root_dir: str = "./data/wiki"
    wiki_max_page_bytes: int = 2_000_000
    llm_enabled: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1_200
    llm_timeout_seconds: float = 60.0
    llm_context_max_chars: int = 12_000
    wiki_query_neighbour_limit: int = 3
    cors_origins: str = Field(default="http://localhost:5173")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
