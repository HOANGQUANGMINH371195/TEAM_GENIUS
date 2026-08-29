"""Public, release-scoped legal timeline assembly.

PostgreSQL metadata remains authoritative. Neo4j relations are navigation
signals only and are discarded unless both endpoints hydrate to canonical
documents in the active release.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from src.models.graph import Relation

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d")
_RELATION_PREFIX = re.compile(r"^REL_", re.IGNORECASE)
_PUBLIC_RELATION_LABELS = {
    "bai bo": "Bãi bỏ",
    "can cu": "Căn cứ",
    "dan chieu": "Dẫn chiếu",
    "sua oi bo sung": "Sửa đổi, bổ sung",
    "tam ngung hieu luc": "Tạm ngưng hiệu lực",
    "thay the": "Thay thế",
    "van ban bo sung": "Văn bản bổ sung",
    "van ban het hieu luc": "Văn bản quy định hết hiệu lực",
    "van ban sua oi": "Văn bản sửa đổi",
}


def parse_legal_date(value: object) -> date | None:
    """Parse only the bounded date formats present in reviewed metadata."""
    normalized = str(value or "").strip()
    if not normalized:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(normalized[:10], fmt).date()
        except ValueError:
            continue
    return None


def state_at(document: dict[str, Any], as_of: date) -> str:
    """Return a deterministic temporal state without inferring legal effect."""
    effective_from = parse_legal_date(document.get("effective_from"))
    effective_to = parse_legal_date(document.get("effective_to"))
    if effective_from and as_of < effective_from:
        return "not_yet_effective"
    if effective_to and as_of > effective_to:
        return "expired"
    if effective_from:
        return "effective"
    return "unknown"


def public_relation_type(value: object) -> str:
    """Convert a graph relationship slug to a bounded public label."""
    normalized = _RELATION_PREFIX.sub("", str(value or "RELATED").strip())
    normalized = " ".join(normalized.replace("_", " ").split())[:120] or "RELATED"
    return _PUBLIC_RELATION_LABELS.get(normalized.casefold(), normalized)


def assemble_public_timeline(
    *,
    seed_document_id: str,
    documents: dict[str, dict[str, Any]],
    relations: list[Relation],
    as_of: date,
    degraded: bool,
) -> dict[str, Any]:
    """Hydrate a graph walk into a public response with no storage IDs."""
    seed = documents.get(seed_document_id)
    if not seed:
        raise ValueError("seed document is not canonical")

    def public_document(document: dict[str, Any]) -> dict[str, Any]:
        number = str(document.get("document_number") or "").strip()
        return {
            "document_number": number,
            "title": str(document.get("title") or "").strip(),
            "issued_at": str(document.get("issued_at") or "").strip(),
            "effective_from": str(document.get("effective_from") or "").strip(),
            "effective_to": str(document.get("effective_to") or "").strip(),
            "status": str(document.get("status") or "").strip(),
            "source_url": str(document.get("source_url") or "").strip(),
            "viewer_url": f"/document?number={number}" if number else "",
            "state_at_date": state_at(document, as_of),
        }

    public_documents: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    seen_events: set[tuple[str, str, str]] = set()
    for relation in relations:
        source = documents.get(relation.source_id)
        target = documents.get(relation.target_id)
        if not source or not target:
            continue
        source_public = public_document(source)
        target_public = public_document(target)
        source_number = source_public["document_number"]
        target_number = target_public["document_number"]
        if not source_number or not target_number:
            continue
        # Relationship descriptions can contain imported free text.  The
        # bounded public label is derived only from the typed relationship
        # enum, never from graph prose.
        relation_type = public_relation_type(relation.relation_type)
        identity = (str(source_number), relation_type, str(target_number))
        if identity in seen_events:
            continue
        seen_events.add(identity)
        public_documents[str(source_number)] = source_public
        public_documents[str(target_number)] = target_public
        events.append({
            "relation": relation_type,
            "source_document_number": source_number,
            "target_document_number": target_number,
            "adverse": bool(relation.adverse),
        })

    seed_public = public_document(seed)
    if seed_public["document_number"]:
        public_documents[str(seed_public["document_number"])] = seed_public
    ordered_documents = sorted(
        public_documents.values(),
        key=lambda item: (
            parse_legal_date(item.get("effective_from")) or date.max,
            str(item.get("document_number") or ""),
        ),
    )
    return {
        "query_document": seed_public,
        "as_of": as_of.isoformat(),
        "documents": ordered_documents,
        "events": events,
        "degraded": degraded,
    }
