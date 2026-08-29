#!/usr/bin/env python3
"""Back up and remove one explicitly named stale Neo4j release projection.

Deletion is fail-closed: the caller must name both the stale target and the
release to retain, pass an exact confirmation string, and write a JSON backup
before any mutation. The script never deletes the retained release and does
not alter PostgreSQL/Qdrant or the active-release pointer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


def _url(value: str) -> str:
    if not value.strip():
        raise ValueError("NEO4J_URI is required")
    return value.strip()


def _validate_target(target: str, retain: str, confirmation: str) -> None:
    if not target.startswith("snapshot-") or not retain.startswith("snapshot-"):
        raise ValueError("target and retain must be immutable snapshot IDs")
    if target == retain:
        raise ValueError("refusing to delete the retained release")
    expected = f"DELETE {target}"
    if confirmation != expected:
        raise ValueError(f"confirmation must be exactly {expected!r}")


async def cleanup(
    *,
    uri: str,
    username: str,
    password: str,
    database: str,
    target: str,
    retain: str,
    confirmation: str,
    backup_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    _validate_target(target, retain, confirmation)
    try:
        from neo4j import AsyncGraphDatabase
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("neo4j driver is required") from exc
    driver = AsyncGraphDatabase.driver(_url(uri), auth=(username, password))
    try:
        async with driver.session(database=database) as session:
            node_result = await session.run(
                "MATCH (n {dataset_id: $target}) RETURN labels(n) AS labels, properties(n) AS properties",
                target=target,
            )
            node_rows = [record.data() async for record in node_result]
            relationship_result = await session.run(
                """
                MATCH (a {dataset_id: $target})-[r]->(b {dataset_id: $target})
                RETURN properties(a) AS source, type(r) AS type,
                       properties(r) AS properties, properties(b) AS target
                """,
                target=target,
            )
            relationship_rows = [record.data() async for record in relationship_result]
            backup = {
                "target_dataset_id": target,
                "retained_dataset_id": retain,
                "nodes": node_rows,
                "relationships": relationship_rows,
            }
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(json.dumps(backup, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            if dry_run:
                return {
                    "target_dataset_id": target,
                    "retained_dataset_id": retain,
                    "nodes": len(node_rows),
                    "relationships": len(relationship_rows),
                    "deleted": False,
                    "backup": str(backup_path),
                }
            await session.run("MATCH (n {dataset_id: $target}) DETACH DELETE n", target=target)
            remaining_result = await session.run(
                "MATCH (n {dataset_id: $target}) RETURN count(n) AS count", target=target
            )
            remaining = int((await remaining_result.single())["count"])
            if remaining:
                raise RuntimeError(f"cleanup left {remaining} nodes for {target}")
            return {
                "target_dataset_id": target,
                "retained_dataset_id": retain,
                "nodes": len(node_rows),
                "relationships": len(relationship_rows),
                "deleted": True,
                "backup": str(backup_path),
            }
    finally:
        await driver.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dataset", required=True)
    parser.add_argument("--retain-dataset", required=True)
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    try:
        report = asyncio.run(
            cleanup(
                uri=os.getenv("NEO4J_URI", ""),
                username=os.getenv("NEO4J_USERNAME", "neo4j"),
                password=os.getenv("NEO4J_PASSWORD", ""),
                database=os.getenv("NEO4J_DATABASE", "neo4j"),
                target=args.target_dataset,
                retain=args.retain_dataset,
                confirmation=args.confirm,
                backup_path=args.backup,
                dry_run=args.dry_run,
            )
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
