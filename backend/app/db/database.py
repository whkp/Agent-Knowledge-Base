from collections.abc import Generator
from pathlib import Path

from hashlib import sha256

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(database_url: str) -> dict[str, bool]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return

    db_path = database_url.replace("sqlite:///", "", 1)
    if db_path == ":memory:":
        return

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_ensure_sqlite_parent_dir(settings.database_url)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args(settings.database_url),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


_DOCUMENT_COLUMN_MIGRATIONS = {
    "source_url": "VARCHAR(2048)",
    "source_platform": "VARCHAR(60)",
    "source_author": "VARCHAR(255)",
    "source_account": "VARCHAR(255)",
    "source_published_at": "DATETIME",
    "source_captured_at": "DATETIME",
    "source_policy": "VARCHAR(20) DEFAULT 'full_text'",
    "source_disclosures": "TEXT DEFAULT '[]'",
    "content_hash": "VARCHAR(64)",
    "tags": "TEXT DEFAULT '[]'",
    # Where a document's Markdown lives, so an update rewrites it in place.
    "raw_path": "VARCHAR(512)",
    "source_path": "VARCHAR(512)",
    "topic_path": "VARCHAR(512)",
}

_FEEDBACK_COLUMN_MIGRATIONS: dict[str, str] = {
    "strategy_id": "VARCHAR(40)",
    "bad_paths": "JSON",
}


def _add_missing_columns(bind: Engine, inspector, tables: set[str], table: str, migrations: dict[str, str]) -> None:
    """Additive, idempotent column additions for databases created before a field existed."""
    if table not in tables:
        return
    existing = {column["name"] for column in inspector.get_columns(table)}
    with bind.begin() as connection:
        for column, definition in migrations.items():
            if column not in existing:
                connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                if definition == "JSON":
                    # Existing rows must read as an empty list, not as NULL.
                    connection.exec_driver_sql(f"UPDATE {table} SET {column} = '[]' WHERE {column} IS NULL")


def ensure_schema_compatibility(bind: Engine) -> None:
    """Apply additive SQLite-safe migrations for installations created before Alembic.

    AgentKB deliberately keeps its deployment footprint small. Until a full
    migration framework is introduced, only additive, idempotent migrations
    live here so upgrading an existing local SQLite database cannot leave the
    ORM ahead of the table schema.
    """

    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    _add_missing_columns(bind, inspector, tables, "query_feedback", _FEEDBACK_COLUMN_MIGRATIONS)
    if "documents" not in tables:
        return

    existing_columns = {column["name"] for column in inspector.get_columns("documents")}
    with bind.begin() as connection:
        for column, definition in _DOCUMENT_COLUMN_MIGRATIONS.items():
            if column not in existing_columns:
                connection.exec_driver_sql(f"ALTER TABLE documents ADD COLUMN {column} {definition}")

        connection.execute(
            text("UPDATE documents SET source_policy = 'full_text' WHERE source_policy IS NULL OR source_policy = ''")
        )
        connection.execute(
            text("UPDATE documents SET source_disclosures = '[]' WHERE source_disclosures IS NULL OR source_disclosures = ''")
        )
        connection.execute(text("UPDATE documents SET tags = '[]' WHERE tags IS NULL OR tags = ''"))
        rows = connection.execute(
            text("SELECT id, content FROM documents WHERE content_hash IS NULL OR content_hash = ''")
        )
        for document_id, content in rows:
            content_hash = sha256((content or "").encode("utf-8")).hexdigest()
            connection.execute(
                text("UPDATE documents SET content_hash = :content_hash WHERE id = :document_id"),
                {"content_hash": content_hash, "document_id": document_id},
            )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
