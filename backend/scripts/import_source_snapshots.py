"""Import externally collected source snapshots into a local AgentKB workspace.

The input is deliberately local JSON or JSONL. Acquisition tools such as
OpenCLI stay outside the server process and complete source text is never
required to live in the public examples directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db import models  # noqa: F401 - registers SQLAlchemy metadata
from app.db.database import Base, SessionLocal, engine, ensure_schema_compatibility
from app.db.schemas import SourceSnapshotCreate
from app.services import document_service, knowledge_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import local source snapshots into AgentKB.")
    parser.add_argument("--knowledge-base-id", type=int, required=True)
    parser.add_argument("--input", required=True, help="Path to a JSON array, JSONL file, or '-' for stdin.")
    parser.add_argument("--dry-run", action="store_true", help="Validate input without writing documents or vectors.")
    return parser.parse_args()


def load_records(input_path: str) -> list[dict[str, Any]]:
    raw = sys.stdin.read() if input_path == "-" else Path(input_path).read_text(encoding="utf-8")
    stripped = raw.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list):
            raise ValueError("JSON input must be an array of source snapshots.")
        return _validate_records(parsed)
    return _validate_records([json.loads(line) for line in raw.splitlines() if line.strip()])


def _validate_records(records: list[Any]) -> list[dict[str, Any]]:
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("Every source snapshot must be a JSON object.")
    return records


def main() -> int:
    args = parse_args()
    try:
        records = load_records(args.input)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    try:
        snapshots = [SourceSnapshotCreate.model_validate(record) for record in records]
    except ValueError as exc:
        print(f"Snapshot validation error: {exc}", file=sys.stderr)
        return 2

    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility(engine)
    db = SessionLocal()
    try:
        knowledge_base = knowledge_service.get_knowledge_base(db, args.knowledge_base_id)
        if knowledge_base is None:
            print(f"Knowledge base {args.knowledge_base_id} was not found.", file=sys.stderr)
            return 2

        imported = 0
        skipped = 0
        for snapshot in snapshots:
            content_hash = document_service.content_hash(snapshot.content)
            existing = db.scalar(
                select(models.Document.id).where(
                    models.Document.knowledge_base_id == knowledge_base.id,
                    models.Document.source_url == snapshot.source_url,
                    models.Document.content_hash == content_hash,
                )
            )
            if existing:
                print(f"skip  {snapshot.title} (same URL and content hash already imported)")
                skipped += 1
                continue
            if args.dry_run:
                print(f"check {snapshot.title} ({snapshot.source_policy})")
                imported += 1
                continue
            document = document_service.create_source_snapshot(db, knowledge_base, snapshot)
            print(f"import {document.id} {document.title} ({document.content_hash[:12]})")
            imported += 1
        print(f"Completed: {imported} imported, {skipped} skipped.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
