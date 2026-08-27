"""Opt-in retrieval of reviewed, de-identified interaction trajectories.

Experience rows are navigation hints only.  They never become evidence,
citations, user facts, or answer text.  The loader rejects unreviewed rows and
common PII/secret patterns before an index can be served.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?84|0)\d{8,10}(?!\d)")
_SECRET = re.compile(r"(?i)\b(?:sk|api[_-]?key|token|password|otp|cvv)[=:][^\s,;]+")
_LONG_ID = re.compile(r"(?<![\w-])[A-Za-z0-9_-]{24,}(?![\w-])")


def deidentify(text: str) -> str:
    """Redact common PII/credential forms deterministically."""
    value = _EMAIL.sub("[EMAIL]", str(text or ""))
    value = _PHONE.sub("[PHONE]", value)
    value = _SECRET.sub("[REDACTED_SECRET]", value)
    return _LONG_ID.sub("[ID]", value)


def _terms(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(text) if len(token) > 2}


@dataclass(frozen=True)
class ReviewedTrajectory:
    trajectory_id: str
    release_id: str
    query: str
    resolution_pattern: str
    tags: tuple[str, ...]
    reviewer: str
    approved: bool
    deidentification_version: str = "v1"

    def validate(self) -> None:
        if not self.trajectory_id.strip() or not self.release_id.startswith("snapshot-"):
            raise ValueError("trajectory_id and immutable release_id are required")
        if not self.query.strip() or not self.resolution_pattern.strip() or not self.reviewer.strip():
            raise ValueError("reviewed trajectory requires query, resolution_pattern and reviewer")
        if not self.approved:
            raise ValueError("only approved trajectories may be indexed")
        if self.deidentification_version != "v1":
            raise ValueError("unsupported deidentification version")
        combined = f"{self.query} {self.resolution_pattern} {' '.join(self.tags)}"
        if _EMAIL.search(combined) or _PHONE.search(combined) or _SECRET.search(combined):
            raise ValueError("trajectory contains PII or secret material")

    def as_hint(self) -> dict[str, object]:
        self.validate()
        # Deliberately omit query/answer prose.  Consumers only receive a
        # bounded workflow hint and must retrieve canonical evidence afresh.
        return {
            "trajectory_id": self.trajectory_id,
            "release_id": self.release_id,
            "tags": list(self.tags),
            "resolution_pattern": self.resolution_pattern,
            "navigation_only": True,
        }


class ExperienceIndex:
    def __init__(self, rows: tuple[ReviewedTrajectory, ...], *, source_sha256: str) -> None:
        self.rows = rows
        self.source_sha256 = source_sha256

    @classmethod
    def load(cls, path: Path, *, release_id: str) -> ExperienceIndex:
        if not path.is_file():
            raise FileNotFoundError(path)
        rows: list[ReviewedTrajectory] = []
        seen: set[str] = set()
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            raw: dict[str, Any] = json.loads(line)
            if "manifest" in raw:
                continue
            row = ReviewedTrajectory(
                trajectory_id=str(raw.get("trajectory_id") or ""),
                release_id=str(raw.get("release_id") or ""),
                query=deidentify(str(raw.get("query") or "")),
                resolution_pattern=deidentify(str(raw.get("resolution_pattern") or "")),
                tags=tuple(deidentify(str(item)) for item in raw.get("tags") or []),
                reviewer=str(raw.get("reviewer") or ""),
                approved=bool(raw.get("approved")),
                deidentification_version=str(raw.get("deidentification_version") or "v1"),
            )
            if row.release_id != release_id:
                raise ValueError(f"line {line_number}: release mismatch")
            row.validate()
            if row.trajectory_id in seen:
                raise ValueError(f"duplicate trajectory_id: {row.trajectory_id}")
            seen.add(row.trajectory_id)
            rows.append(row)
        if not rows:
            raise ValueError("experience index has no approved rows")
        return cls(tuple(rows), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def search(self, query: str, *, max_hits: int = 3) -> list[dict[str, object]]:
        if max_hits < 1:
            return []
        query_terms = _terms(query)
        scored = []
        for row in self.rows:
            overlap = query_terms & _terms(f"{row.query} {' '.join(row.tags)}")
            if overlap:
                scored.append((len(overlap), row.trajectory_id, row.as_hint()))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [hint for _, _, hint in scored[:max_hits]]
