"""Evidence-gap planner used to gate optional multi-hop expansion."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from src.models.graph import RetrievalResult

_TOKEN = re.compile(r"[0-9A-Za-zÀ-ỹĐđ]+", re.IGNORECASE)
_STOP = {"và", "là", "có", "được", "cho", "của", "theo", "trong", "với", "khi", "này", "một", "các", "người", "gì"}


@dataclass(frozen=True)
class GroundedPlan:
    enabled: bool
    missing_facts: tuple[str, ...]
    fanout: int = 3
    depth: int = 2

    def as_dict(self) -> dict[str, object]:
        return {"enabled": self.enabled, "missing_facts": list(self.missing_facts), "fanout": self.fanout, "depth": self.depth}


def evidence_gap_plan(query: str, evidence: Sequence[RetrievalResult], *, enabled: bool = True) -> GroundedPlan:
    """Return only query-derived gaps; no answer or domain fact is invented."""
    if not enabled:
        return GroundedPlan(False, ())
    terms = {t.casefold() for t in _TOKEN.findall(query) if len(t) > 2 and t.casefold() not in _STOP}
    available = {t.casefold() for item in evidence for t in _TOKEN.findall(f"{item.section_title} {item.content}")}
    missing = tuple(sorted(terms - available, key=lambda value: (-len(value), value))[:3])
    return GroundedPlan(bool(missing) and bool(evidence), missing)
