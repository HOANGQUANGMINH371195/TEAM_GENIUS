from __future__ import annotations

from pydantic import BaseModel, Field


class Entity(BaseModel):
    name: str
    entity_type: str = "unknown"
    description: str = ""


class Relation(BaseModel):
    source: str
    target: str
    relation_type: str
    description: str = ""


class RetrievalResult(BaseModel):
    chunk_id: str
    content: str
    source: str = ""
    score: float = 0.0
    entities: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    source: str
    chunk_id: str = ""
    quote: str = ""
