"""Release-scoped community/global retrieval primitives.

Community summaries are navigation hints, never legal evidence.  This module
keeps the global/DRIFT path deterministic and provider-free: summaries are
assembled from canonical passage text offline, and every selected community
returns document IDs that the serving layer must hydrate through PostgreSQL
before citation or generation.  No generated summary is allowed to become a
source of authority by itself.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.IGNORECASE)
_STOPWORDS = frozenset(
    {
        "và", "là", "có", "được", "cho", "của", "theo", "trong", "với",
        "khi", "này", "một", "các", "người", "gì", "nào", "những", "về",
    }
)


def _terms(text: str) -> frozenset[str]:
    return frozenset(
        token.casefold() for token in _TOKEN.findall(text)
        if len(token) > 2 and token.casefold() not in _STOPWORDS
    )


@dataclass(frozen=True)
class CommunitySummary:
    """A deterministic, release-scoped navigation summary."""

    community_id: str
    release_id: str
    title: str
    document_ids: tuple[str, ...]
    text: str
    source_passage_ids: tuple[str, ...]
    content_sha256: str

    def validate(self) -> None:
        if not self.community_id.strip() or not self.release_id.strip():
            raise ValueError("community_id and release_id are required")
        if not self.document_ids or not self.source_passage_ids:
            raise ValueError("community summary needs source documents and passages")
        if not self.text.strip():
            raise ValueError("community summary text is required")
        expected = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.content_sha256.casefold() != expected:
            raise ValueError("community summary content_sha256 does not match text")

    def as_record(self) -> dict[str, object]:
        self.validate()
        return {
            "community_id": self.community_id,
            "release_id": self.release_id,
            "title": self.title,
            "document_ids": list(self.document_ids),
            "text": self.text,
            "source_passage_ids": list(self.source_passage_ids),
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class GlobalSearchHit:
    """A community hit that must be hydrated before it can support a claim."""

    summary: CommunitySummary
    score: float
    round: int
    matched_terms: tuple[str, ...]

    @property
    def document_ids(self) -> tuple[str, ...]:
        return self.summary.document_ids


def build_community_summaries(
    rows: Iterable[Mapping[str, object]],
    *,
    release_id: str,
    max_communities: int = 256,
    max_documents: int = 32,
    max_passages: int = 12,
    max_chars: int = 4_000,
) -> tuple[CommunitySummary, ...]:
    """Build stable summaries from canonical rows without inventing text.

    Each row must provide ``community_id``, ``document_id``, ``passage_id``
    and ``text``; optional ``title`` and ``ordinal`` only affect deterministic
    ordering.  The first source passages are concatenated verbatim (bounded by
    ``max_chars``), so a later hydrator can always recover their canonical
    provenance.
    """
    if not release_id.strip():
        raise ValueError("release_id is required")
    if min(max_communities, max_documents, max_passages, max_chars) < 1:
        raise ValueError("summary limits must be positive")
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        community_id = str(row.get("community_id") or "").strip()
        if not community_id:
            continue
        if str(row.get("release_id") or release_id) != release_id:
            raise ValueError("community row release_id does not match requested release")
        if not str(row.get("document_id") or "").strip() or not str(row.get("passage_id") or "").strip():
            raise ValueError("community rows require document_id and passage_id")
        if not str(row.get("text") or "").strip():
            continue
        grouped[community_id].append(row)
    summaries: list[CommunitySummary] = []
    for community_id in sorted(grouped)[:max_communities]:
        members = sorted(
            grouped[community_id],
            key=lambda row: (
                int(row.get("ordinal") or 0),
                str(row.get("document_id") or ""),
                str(row.get("passage_id") or ""),
            ),
        )
        selected = members[:max_passages]
        fragments: list[str] = []
        passage_ids: list[str] = []
        for row in selected:
            text = " ".join(str(row.get("text") or "").split())
            remaining = max_chars - sum(len(fragment) for fragment in fragments)
            if remaining <= 0:
                break
            fragments.append(text[:remaining])
            passage_ids.append(str(row["passage_id"]))
        text = "\n\n".join(fragment for fragment in fragments if fragment)
        if not text or not passage_ids:
            continue
        summary = CommunitySummary(
            community_id=community_id,
            release_id=release_id,
            title=str(selected[0].get("title") or community_id),
            document_ids=tuple(dict.fromkeys(str(row["document_id"]) for row in selected))[:max_documents],
            text=text,
            source_passage_ids=tuple(passage_ids),
            content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
        summary.validate()
        summaries.append(summary)
    return tuple(summaries)


def drift_search(
    query: str,
    summaries: Sequence[CommunitySummary],
    *,
    max_hits: int = 3,
    max_rounds: int = 2,
) -> tuple[GlobalSearchHit, ...]:
    """Select communities with bounded iterative novelty (DRIFT-style).

    The first round scores query overlap.  Later rounds add only terms found
    in already selected summaries, rewarding new communities that expand the
    evidence vocabulary.  This is a navigation stage: callers must hydrate
    ``document_ids`` and re-run canonical citation verification.
    """
    normalized = " ".join(query.split())
    if not normalized or not summaries or max_hits < 1 or max_rounds < 1:
        return ()
    for summary in summaries:
        summary.validate()
    query_terms = _terms(normalized)
    if not query_terms:
        return ()
    remaining = list(summaries)
    selected: list[GlobalSearchHit] = []
    expansion_terms = set(query_terms)
    for current_round in range(max_rounds):
        scored: list[tuple[float, CommunitySummary, tuple[str, ...]]] = []
        for summary in remaining:
            haystack = _terms(f"{summary.title} {summary.text}")
            matched = tuple(sorted(haystack & expansion_terms))
            direct = len(haystack & query_terms)
            novelty = len(haystack & expansion_terms - query_terms)
            score = direct * 2.0 + novelty * 0.35 + len(matched) * 0.1
            if score > 0:
                scored.append((score, summary, matched))
        scored.sort(key=lambda item: (-item[0], item[1].community_id))
        if not scored:
            break
        take = min(max_hits - len(selected), len(scored))
        for score, summary, matched in scored[:take]:
            selected.append(GlobalSearchHit(summary, score, current_round + 1, matched))
            expansion_terms.update(_terms(summary.text))
            remaining.remove(summary)
        if len(selected) >= max_hits:
            break
    return tuple(selected)
