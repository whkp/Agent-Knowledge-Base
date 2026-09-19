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
    # Below this score the direct matches are considered weak, so the query may
    # follow one more link hop. Set it to 1 to always walk two hops.
    wiki_query_deep_threshold: float = 0.5
    # Which strategy a query uses when it does not name one. Change this only with
    # replay evidence: `replay_queries.py` compares strategies on recorded feedback.
    wiki_query_default_strategy: str = "auto"
    # How much a page's vector similarity may add on top of its lexical score.
    wiki_query_vector_weight: float = 0.35
    # Minimum cosine similarity for a page to count as a semantic hit. Measured on the
    # multilingual MiniLM model: real matches score above 0.5, unrelated pages sit in the
    # 0.1-0.2 band, so without a floor every query would drag the least-unrelated pages in.
    wiki_query_vector_floor: float = 0.35
    cors_origins: str = Field(default="http://localhost:5173")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
