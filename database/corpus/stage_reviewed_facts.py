#!/usr/bin/env python3
"""Stage reviewer-supplied typed facts into PostgreSQL.

This is an offline, release-scoped import boundary. It accepts candidate rows
for review, but only rows explicitly marked ``accepted`` and carrying reviewer
metadata are eligible for the Neo4j exporter. Every row is checked against the
canonical document/unit text before it is inserted; existing fact IDs are
immutable and conflicting replays fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from src.domain.facts import LegalFact
from src.domain.ontology import ontology_issues
from src.services.fact_recognizer import recognize_fact_rows


def _url(value: str) -> str:
    value = value.strip()
    if value.startswith("postgresql+asyncpg://"):
        return "postgresql://" + value.removeprefix("postgresql+asyncpg://")
    if value.startswith("postgresql://"):
        return value
    raise ValueError("database URL must use postgresql://")


def _read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at line {line_number}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"fact at line {line_number} must be an object")
        rows.append(value)
    if not rows:
        raise ValueError("fact file has no rows")
    return rows


def validate_review_rows(
    rows: list[dict[str, Any]], *, release_id: str
) -> list[tuple[LegalFact, dict[str, Any]]]:
    """Validate ontology/release/reviewer requirements without database I/O."""
    result = recognize_fact_rows(rows, release_id=release_id)
    if result.rejected:
        first = result.rejected[0]
        raise ValueError(f"invalid fact row {int(first['row']) + 1}: {first['reason']}")
    validated: list[tuple[LegalFact, dict[str, Any]]] = []
    for index, (fact, row) in enumerate(zip(result.facts, rows, strict=True), start=1):
        issues = tuple(issue for issue in ontology_issues(row) if issue != "not_accepted")
        if issues:
            raise ValueError(f"invalid fact row {index}: ontology issues: {', '.join(issues[:5])}")
        if fact.review_status == "accepted":
            reviewer = str(row.get("reviewed_by") or row.get("reviewer") or "").strip()
            if not reviewer:
                raise ValueError(f"accepted fact row {index} requires reviewed_by/reviewer")
            review_note = str(row.get("review_note") or row.get("decision_note") or "").strip()
            if not review_note:
                raise ValueError(f"accepted fact row {index} requires review_note")
        validated.append((fact, row))
    return validated


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_hash_matches(
    fact: LegalFact,
    *,
    document_text: str,
    unit_text: str,
    unit_start: int | None = None,
    unit_end: int | None = None,
) -> bool:
    if fact.source_start is None or fact.source_end is None:
        return False
    if fact.source_end <= fact.source_start or fact.source_start < 0:
        return False
    expected = fact.source_sha256.casefold()
    if fact.source_end <= len(document_text):
        span = document_text[fact.source_start : fact.source_end]
        if expected == _hash(span):
            return True
    if (
        unit_start is not None
        and unit_end is not None
        and fact.source_start == unit_start
        and fact.source_end == unit_end
        and expected == _hash(unit_text)
    ):
        return True
    # PageIndex-derived rows may hash the parsed unit rather than the document
    # substring.  Accept that representation only when the fact span is the
    # unit's own canonical source interval; an arbitrary span must not inherit
    # a unit hash.
    return False


def stage_facts(
    database_url: str,
    validated: list[tuple[LegalFact, dict[str, Any]]],
) -> dict[str, int]:
    """Verify canonical anchors and insert immutable fact rows in one transaction."""
    if not validated:
        return {"rows": 0, "inserted": 0, "replayed": 0}
    release_ids = {fact.release_id for fact, _ in validated}
    if len(release_ids) != 1:
        raise ValueError("all facts must belong to one release")
    release_id = next(iter(release_ids))
    inserted = 0
    replayed = 0
    with psycopg.connect(_url(database_url), autocommit=False) as db:
        dataset = db.execute(
            "SELECT 1 FROM public.datasets WHERE dataset_id = %s", (release_id,)
        ).fetchone()
        if dataset is None:
            raise ValueError(f"dataset not found: {release_id}")
        for fact, reviewed_row in validated:
            canonical_row = db.execute(
                """SELECT d.content_text, u.text AS unit_text,
                          u.source_start, u.source_end
                   FROM public.documents d
                   JOIN public.legal_units u
                     ON u.dataset_id = d.dataset_id AND u.document_id = d.id
                    AND u.unit_id = %s
                   WHERE d.dataset_id = %s AND d.id = %s""",
                (fact.unit_id, release_id, fact.document_id),
            ).fetchone()
            if canonical_row is None:
                raise ValueError(f"fact {fact.fact_id} references a missing document/unit")
            if not _source_hash_matches(
                fact,
                document_text=str(canonical_row[0] or ""),
                unit_text=str(canonical_row[1] or ""),
                unit_start=int(canonical_row[2]) if canonical_row[2] is not None else None,
                unit_end=int(canonical_row[3]) if canonical_row[3] is not None else None,
            ):
                raise ValueError(f"fact {fact.fact_id} source_sha256 does not match canonical text")

            payload = dict(reviewed_row.get("payload") or {})
            reviewer = str(reviewed_row.get("reviewed_by") or reviewed_row.get("reviewer") or "").strip()
            if reviewer:
                payload.update(
                    {
                        "reviewed_by": reviewer,
                        "review_note": str(reviewed_row.get("review_note") or reviewed_row.get("decision_note") or "").strip(),
                        "reviewed_at": str(reviewed_row.get("reviewed_at") or ""),
                    }
                )
            record = fact.as_record()
            record["payload"] = Jsonb(payload)
            existing = db.execute(
                "SELECT subject, predicate, normalized_value, document_id, unit_id, source_sha256, review_status "
                "FROM public.legal_facts WHERE fact_id = %s",
                (fact.fact_id,),
            ).fetchone()
            if existing is not None:
                expected = (
                    fact.subject, fact.predicate, fact.normalized_value, fact.document_id,
                    fact.unit_id, fact.source_sha256, fact.review_status,
                )
                if tuple(str(value or "") for value in existing) != tuple(str(value or "") for value in expected):
                    raise ValueError(f"fact {fact.fact_id} conflicts with an existing immutable row")
                replayed += 1
                continue
            db.execute(
                """INSERT INTO public.legal_facts(
                    fact_id, dataset_id, subject, predicate, normalized_value,
                    effective_from, effective_to, jurisdiction, provision_id,
                    document_id, unit_id, source_start, source_end, source_sha256,
                    review_status, payload
                ) VALUES (
                    %(fact_id)s, %(release_id)s, %(subject)s, %(predicate)s,
                    %(normalized_value)s, %(effective_from)s, %(effective_to)s,
                    %(jurisdiction)s, %(provision_id)s, %(document_id)s, %(unit_id)s,
                    %(source_start)s, %(source_end)s, %(source_sha256)s,
                    %(review_status)s, %(payload)s
                )""",
                record,
            )
            inserted += 1
        db.commit()
    return {"rows": len(validated), "inserted": inserted, "replayed": replayed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if not args.release_id.startswith("snapshot-"):
        parser.error("release-id must be an immutable snapshot-... identifier")
    load_dotenv(args.env_file, override=False)
    database_url = args.database_url or os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
    if not database_url:
        parser.error("DATABASE_URL or MIGRATION_DATABASE_URL is required")
    rows = _read_rows(args.input)
    validated = validate_review_rows(rows, release_id=args.release_id)
    report = stage_facts(database_url, validated)
    report.update({"release_id": args.release_id, "accepted": sum(f.review_status == "accepted" for f, _ in validated)})
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
