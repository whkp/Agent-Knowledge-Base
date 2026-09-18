"""Migrate local storage filenames to the AgentKB naming scheme.

This is intentionally a small, explicit migration for local development data.
It does not alter SQLite tables or attempt to rename a Chroma collection.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


LEGACY_DATABASE_NAMES = ("livingwiki.db", "kk_knowledge.db")


def migrate_database(project_root: Path, *, dry_run: bool) -> str:
    data_dir = project_root / "data"
    new_path = data_dir / "agentkb.db"
    old_path = next(
        (data_dir / name for name in LEGACY_DATABASE_NAMES if (data_dir / name).exists()),
        None,
    )
    if new_path.exists():
        if old_path is None:
            return f"AgentKB database already exists: {new_path}; nothing to migrate."
        raise FileExistsError(
            f"Target database already exists: {new_path}. Resolve the legacy database first: {old_path}"
        )
    if old_path is None:
        return "No legacy database found; nothing to migrate."
    if dry_run:
        return f"Would rename {old_path} -> {new_path}"

    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old_path), str(new_path))
    return f"Renamed {old_path} -> {new_path}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate local storage names to AgentKB.")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        print(migrate_database(args.project_root.resolve(), dry_run=args.dry_run))
    except FileExistsError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
