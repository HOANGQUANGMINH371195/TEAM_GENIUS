"""VBPL document ingestion into PostgreSQL, Qdrant, and Neo4j."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
import unicodedata
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from src.config import get_settings
from src.db.session import session_scope
from src.integrations.embeddings import get_embedding_model
from src.integrations.neo4j import Neo4jGraphStore
from src.integrations.qdrant import QdrantVectorStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _neo4j_transaction_context(session: Any):
    """Await transaction factory before entering transaction context."""
    transaction = session.begin_transaction()
    if inspect.isawaitable(transaction):
        transaction = await transaction
    try:
        yield transaction
    except BaseException:
        await transaction.rollback()
        raise
    else:
        await transaction.commit()


REFERENCE_TYPE_MAP = {
    3: "CAN_CU",
    10: "BI_SUA_DOI",
    11: "SUA_DOI",
    20: "THAY_THE",
    21: "BI_THAY_THE",
    30: "HUONG_DAN",
    31: "DUOC_HUONG_DAN",
    40: "QUY_DINH_LIEN_QUAN",
}

_STAGE_NAMES = ("supabase", "qdrant", "neo4j")


def _canonical_reference_type(value: Any) -> str:
    """Preserve arbitrary API referenceType values as deterministic JSON."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _relationship_id(document_id: str, reference: dict[str, Any]) -> str:
    """Build stable identity from source, target, API ID, and raw type."""
    identity = {
        "source_id": document_id,
        "target_id": str(reference.get("target_id") or ""),
        "reference_id": str(reference.get("reference_id") or ""),
        "reference_type": reference.get("reference_type"),
    }
    if not identity["reference_id"]:
        identity["reference_provisions"] = reference.get("reference_provisions", [])
    digest = hashlib.sha256(_canonical_reference_type(identity).encode("utf-8")).hexdigest()
    return f"vbpl:{digest}"


def _relationship_display(value: Any) -> str:
    """Readable relationship value while retaining unknown API types."""
    if type(value) is int:
        return f"REF_TYPE_{value}"
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _canonical_reference_type(value)


def _safe_relationship_label(value: Any) -> str:
    """Generate collision-resistant label when type-specific queries need it."""
    raw_json = _canonical_reference_type(value)
    readable = _relationship_display(value)
    ascii_value = unicodedata.normalize("NFKD", readable).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_value).strip("_") or "RELATED"
    digest = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()[:12]
    label = f"REL_VBPL_{slug[:36]}_{digest}"
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", label):
        raise ValueError("generated relationship label is unsafe")
    return label


def _safe_error(error: Exception) -> str:
    """Return bounded error text without connection credentials or tracebacks."""
    value = str(error).replace("\r", " ").replace("\n", " ").strip()
    for marker in ("postgresql+asyncpg://", "postgresql://", "password=", "api_key="):
        index = value.lower().find(marker.lower())
        if index >= 0:
            value = value[:index].rstrip(" ,;:")
    return value[:1000] or error.__class__.__name__


def _append_error(result: dict[str, Any], stage: str, error: Exception) -> None:
    message = f"{stage}: {_safe_error(error)}"
    result["error"] = f"{result['error']}; {message}" if result["error"] else message


def _stage_status(result: dict[str, Any], stage: str, status: str) -> None:
    if stage not in _STAGE_NAMES or status not in {"success", "skipped", "failed"}:
        raise ValueError("Invalid ingest stage status")
    result[stage] = status


def _finalize_status(result: dict[str, Any]) -> None:
    statuses = [result[name] for name in _STAGE_NAMES]
    if all(value == "success" for value in statuses):
        result["status"] = "success"
    elif any(value == "success" for value in statuses):
        result["status"] = "partial"
    else:
        result["status"] = "failed"


def html_to_visible_text(html: str) -> str:
    """Strip non-visible HTML and normalize document text."""
    text_content = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.IGNORECASE | re.DOTALL)
    text_content = re.sub(r"<script[^>]*>.*?</script>", "", text_content, flags=re.IGNORECASE | re.DOTALL)
    text_content = re.sub(r"<br\s*/?>", "\n", text_content, flags=re.IGNORECASE)
    text_content = re.sub(r"</p>", "\n", text_content, flags=re.IGNORECASE)
    text_content = re.sub(r"</div>", "\n", text_content, flags=re.IGNORECASE)
    text_content = re.sub(r"<[^>]+>", " ", text_content)
    text_content = re.sub(r"&nbsp;", " ", text_content)
    text_content = re.sub(r"&amp;", "&", text_content)
    text_content = re.sub(r"&lt;", "<", text_content)
    text_content = re.sub(r"&gt;", ">", text_content)
    text_content = re.sub(r"[ \t]+", " ", text_content)
    text_content = re.sub(r"\n{3,}", "\n\n", text_content)
    return text_content.strip()


def chunk_text(text_content: str, max_words: int = 320, overlap: int = 32) -> list[str]:
    """Split text into overlapping word-based chunks."""
    if max_words <= overlap:
        raise ValueError("max_words must be greater than overlap")
    words = text_content.split()
    chunks: list[str] = []
    step = max_words - overlap
    for start in range(0, len(words), step):
        chunk = " ".join(words[start : start + max_words])
        if chunk:
            chunks.append(chunk)
        if start + max_words >= len(words):
            break
    return chunks


class VbplIngestService:
    @classmethod
    async def ingest_document(cls, vbpl_id: str, doc_detail: dict[str, Any]) -> dict[str, Any]:
        """Ingest one normalized VBPL document into all GraphRAG stores."""
        result: dict[str, Any] = {
            "doc_id": vbpl_id,
            "status": "failed",
            "supabase": "skipped",
            "qdrant": "skipped",
            "neo4j": "skipped",
            "error": "",
            "chunks_count": 0,
        }

        title = str(doc_detail.get("title") or "")
        so_ky_hieu = str(doc_detail.get("doc_num") or "")
        content_html = str(doc_detail.get("content_html") or "")
        content_text = html_to_visible_text(content_html)
        if not content_text:
            error = ValueError("documentContent is empty after HTML-to-text extraction")
            _append_error(result, "document", error)
            return result

        chunks = chunk_text(content_text)
        result["chunks_count"] = len(chunks)
        doc_id = vbpl_id
        root_unit_id = f"{doc_id}:root"
        content_text_sha256 = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
        content_html_sha256 = hashlib.sha256(content_html.encode("utf-8")).hexdigest()
        payload = {
            "metadata": {
                "so_ky_hieu": so_ky_hieu,
                "doc_type": doc_detail.get("doc_type", ""),
                "doc_type_code": doc_detail.get("doc_type_code", ""),
                "issue_date": doc_detail.get("issue_date", ""),
                "effective_from": doc_detail.get("effective_from", ""),
                "effective_to": doc_detail.get("effective_to"),
                "public_date": doc_detail.get("public_date", ""),
                "legal_status": doc_detail.get("legal_status", ""),
                "legal_status_code": doc_detail.get("legal_status_code", ""),
                "answer_ready": True,
                "is_external": False,
                "metadata_provenance": "official_vbpl",
                "issuing_body": doc_detail.get("issuing_body", ""),
                "source_url": f"https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/{vbpl_id}",
                "ingested_at": datetime.now(UTC).isoformat(),
            },
        }
        references = doc_detail.get("references", []) or []

        # 1. PostgreSQL source-of-truth transaction.
        try:
            async with session_scope() as session:
                dataset_result = await session.execute(
                    text("SELECT active_dataset_id FROM public.dataset_state WHERE singleton = true")
                )
                dataset_id = dataset_result.scalar_one_or_none()
                if not dataset_id:
                    raise RuntimeError("No active dataset_id found")

                await session.execute(
                    text("""
                    INSERT INTO public.documents (
                        dataset_id, id, title, is_external, content_text, text_sha256,
                        content_available, raw_html, raw_html_sha256, raw_html_encoding,
                        categories, facets, payload
                    ) VALUES (
                        :dataset_id, :id, :title, false, :content_text, :text_sha256,
                        true, :raw_html, :raw_html_sha256, 'utf-8',
                        :categories, CAST(:facets AS jsonb), CAST(:payload AS jsonb)
                    )
                    ON CONFLICT (dataset_id, id) DO UPDATE SET
                        title = EXCLUDED.title,
                        content_text = EXCLUDED.content_text,
                        text_sha256 = EXCLUDED.text_sha256,
                        content_available = true,
                        raw_html = EXCLUDED.raw_html,
                        raw_html_sha256 = EXCLUDED.raw_html_sha256,
                        payload = EXCLUDED.payload,
                        is_external = false
                    """),
                    {
                        "dataset_id": dataset_id,
                        "id": doc_id,
                        "title": title,
                        "content_text": content_text,
                        "text_sha256": content_text_sha256,
                        "raw_html": content_html,
                        "raw_html_sha256": content_html_sha256,
                        "categories": ["vbpl", "legal_document"],
                        "facets": json.dumps([]),
                        "payload": json.dumps(payload),
                    },
                )

                first_500 = content_text[:500]
                await session.execute(
                    text("""
                    INSERT INTO public.legal_units (
                        dataset_id, unit_id, document_id, parent_unit_id,
                        unit_type, ordinal_raw, label, heading, text,
                        source_selector, source_fragment_sha256, text_sha256,
                        parse_method, parse_confidence, parser_version, payload
                    ) VALUES (
                        :dataset_id, :unit_id, :document_id, NULL,
                        'document_root', '', :label, :heading, :text,
                        '', '', :text_sha256,
                        'vbpl-ingest', 1.0, 'vbpl-ingest-v1', '{}'::jsonb
                    )
                    ON CONFLICT (dataset_id, unit_id) DO UPDATE SET
                        text = EXCLUDED.text,
                        text_sha256 = EXCLUDED.text_sha256
                    """),
                    {
                        "dataset_id": dataset_id,
                        "unit_id": root_unit_id,
                        "document_id": doc_id,
                        "label": so_ky_hieu or title[:80],
                        "heading": title,
                        "text": first_500,
                        "text_sha256": hashlib.sha256(first_500.encode("utf-8")).hexdigest(),
                    },
                )

                for idx, chunk_text_item in enumerate(chunks):
                    chunk_id = f"{doc_id}:chunk:{idx}"
                    chunk_sha256 = hashlib.sha256(chunk_text_item.encode("utf-8")).hexdigest()
                    offset = content_text.find(chunk_text_item)
                    source_start = max(0, offset)
                    source_end = source_start + len(chunk_text_item)
                    await session.execute(
                        text("""
                        INSERT INTO public.chunks (
                            dataset_id, chunk_id, id, source_key, document_id, chunk_order,
                            unit_id, source_start, source_end, text, section_title,
                            text_sha256, parser_version, chunker_version,
                            lexical_eligible, semantic_eligible,
                            embedding_input_text, embedding_input_sha256,
                            payload
                        ) VALUES (
                            :dataset_id, :chunk_id, :id, :source_key, :document_id, :chunk_order,
                            :unit_id, :source_start, :source_end, :text, :section_title,
                            :text_sha256, :parser_version, :chunker_version,
                            true, true,
                            :embedding_input_text, :embedding_input_sha256,
                            CAST(:payload AS jsonb)
                        )
                        ON CONFLICT (dataset_id, chunk_id) DO UPDATE SET
                            text = EXCLUDED.text,
                            text_sha256 = EXCLUDED.text_sha256,
                            embedding_input_text = EXCLUDED.embedding_input_text,
                            embedding_input_sha256 = EXCLUDED.embedding_input_sha256
                        """),
                        {
                            "dataset_id": dataset_id,
                            "chunk_id": chunk_id,
                            "id": chunk_id,
                            "source_key": f"{vbpl_id}:chunk:{idx}",
                            "document_id": doc_id,
                            "chunk_order": idx,
                            "unit_id": root_unit_id,
                            "source_start": source_start,
                            "source_end": source_end,
                            "text": chunk_text_item,
                            "section_title": "",
                            "text_sha256": chunk_sha256,
                            "parser_version": "vbpl-ingest-v1",
                            "chunker_version": "vbpl-ingest-v1",
                            "embedding_input_text": chunk_text_item,
                            "embedding_input_sha256": chunk_sha256,
                            "payload": json.dumps({"chunk_index": idx, "total_chunks": len(chunks)}),
                        },
                    )
                await session.commit()
                verification = await session.execute(
                    text("""
                    SELECT
                        (SELECT count(*) FROM public.documents
                         WHERE dataset_id = :dataset_id AND id = :document_id) AS document_count,
                        (SELECT count(*) FROM public.chunks
                         WHERE dataset_id = :dataset_id AND document_id = :document_id) AS chunk_count
                    """),
                    {"dataset_id": dataset_id, "document_id": doc_id},
                )
                document_count, chunk_count = verification.one()
                if int(document_count) != 1 or int(chunk_count) != len(chunks):
                    raise RuntimeError(
                        "PostgreSQL ingest verification failed: "
                        f"documents={document_count}, chunks={chunk_count}, expected_chunks={len(chunks)}"
                    )
            _stage_status(result, "supabase", "success")
        except Exception as error:
            logger.error("PostgreSQL ingest failed for %s", vbpl_id, exc_info=error)
            _stage_status(result, "supabase", "failed")
            _append_error(result, "supabase", error)
            _finalize_status(result)
            return result

        # 2. Qdrant embedding and vector upsert.
        qdrant = None
        dataset_id = str(dataset_id)
        try:
            qdrant = QdrantVectorStore()
            embed_model = get_embedding_model()
            from qdrant_client.models import PointStruct

            vectors = await embed_model.embed_queries(chunks)
            points = []
            for idx, (chunk_text_item, vector) in enumerate(zip(chunks, vectors)):
                chunk_id = f"{doc_id}:chunk:{idx}"
                chunk_sha256 = hashlib.sha256(chunk_text_item.encode("utf-8")).hexdigest()
                points.append(
                    PointStruct(
                        id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id)),
                        vector=list(vector),
                        payload={
                            "document_id": doc_id,
                            "passage_id": chunk_id,
                            "dataset_id": dataset_id,
                            "text": chunk_text_item,
                            "unit_id": root_unit_id,
                            "input_sha256": chunk_sha256,
                            "answer_ready": True,
                        },
                    )
                )
            if points:
                await qdrant.client.upsert(collection_name=qdrant.collection, points=points, wait=True)

            settings = get_settings()
            async with session_scope() as session:
                metadata_result = await session.execute(
                    text("""
                    UPDATE public.chunks
                    SET embedding_model = :embedding_model,
                        embedding_dimensions = :embedding_dimensions,
                        embedded_input_sha256 = embedding_input_sha256,
                        embedding_created_at = :embedding_created_at
                    WHERE dataset_id = :dataset_id AND document_id = :document_id
                    """),
                    {
                        "embedding_model": settings.embedding_model,
                        "embedding_dimensions": settings.embedding_dimensions,
                        "embedding_created_at": datetime.now(UTC),
                        "dataset_id": dataset_id,
                        "document_id": doc_id,
                    },
                )
                if metadata_result.rowcount != len(chunks):
                    raise RuntimeError(
                        f"Embedding metadata updated {metadata_result.rowcount} of {len(chunks)} chunks"
                    )
                await session.commit()
            _stage_status(result, "qdrant", "success")
        except Exception as error:
            logger.error("Qdrant ingest failed for %s", vbpl_id, exc_info=error)
            _stage_status(result, "qdrant", "failed")
            _append_error(result, "qdrant", error)
        finally:
            if qdrant is not None:
                try:
                    await qdrant.close()
                except Exception as error:
                    logger.warning("Qdrant close failed for %s: %s", vbpl_id, _safe_error(error))

        # 3. Neo4j document and reference graph.
        neo4j = None
        try:
            neo4j = Neo4jGraphStore()
            async with neo4j.session_context(database=neo4j.database) as session:
                async with _neo4j_transaction_context(session) as transaction:
                    await transaction.run(
                        """
                        MERGE (d:Document {graph_id: $graph_id})
                        SET d.id = $doc_id,
                            d.dataset_id = $dataset_id,
                            d.graph_id = $graph_id,
                            d.title = $title,
                            d.so_ky_hieu = $so_ky_hieu,
                            d.doc_type = $doc_type,
                            d.issuing_body = $issuing_body,
                            d.issue_date = $issue_date,
                            d.legal_status = $legal_status,
                            d.source = 'vbpl',
                            d.name = $title,
                            d.node_kind = 'canonical_document'
                        """,
                        graph_id=f"{dataset_id}:{doc_id}",
                        doc_id=doc_id,
                        dataset_id=dataset_id,
                        title=title,
                        so_ky_hieu=so_ky_hieu,
                        doc_type=doc_detail.get("doc_type", ""),
                        issuing_body=doc_detail.get("issuing_body", ""),
                        issue_date=doc_detail.get("issue_date", ""),
                        legal_status=doc_detail.get("legal_status", ""),
                    )
                    await transaction.run(
                        """
                        MATCH (d:Document {graph_id: $source_graph_id})-[old]->()
                        WHERE old.dataset_id = $dataset_id AND type(old) <> 'ALIAS_OF'
                        DELETE old
                        """,
                        source_graph_id=f"{dataset_id}:{doc_id}",
                        dataset_id=dataset_id,
                    )
                    for ref in references:
                        target_id = str(ref.get("target_id") or "")
                        if not target_id:
                            continue
                        raw_type = ref.get("reference_type")
                        canonical_type = _canonical_reference_type(raw_type)
                        relationship_id = _relationship_id(doc_id, ref)
                        relationship_label = _safe_relationship_label(raw_type)
                        await transaction.run(
                            f"""
                            MATCH (d:Document {{graph_id: $source_graph_id}})
                            MERGE (t:Document {{graph_id: $target_graph_id}})
                            ON CREATE SET t.id = $target_id,
                                          t.dataset_id = $dataset_id,
                                          t.graph_id = $target_graph_id,
                                          t.so_ky_hieu = $target_doc_num,
                                          t.title = $target_title,
                                          t.name = $target_title,
                                          t.source = 'vbpl_reference',
                                          t.node_kind = 'reference_only',
                                          t.retrieval_scope = 'reference_only'
                            MERGE (d)-[r:`{relationship_label}` {{relationship_id: $relationship_id}}]->(t)
                            SET r.dataset_id = $dataset_id,
                                r.graph_id = $relationship_id,
                                r.relationship_type = $relationship_type,
                                r.reference_type_json = $canonical_type,
                                r.reference_type_code = $raw_type,
                                r.reference_id = $reference_id,
                                r.reference_type_name = $reference_type_name,
                                r.reference_provisions = $reference_provisions,
                                r.target_doc_num = $target_doc_num,
                                r.target_title = $target_title,
                                r.source_document_id = $doc_id,
                                r.target_document_id = $target_id,
                                r.reference_origin = 'vbpl',
                                r.serving_status = 'approved_evidence',
                                r.serving_qualification = 'official_vbpl_reference'
                            """,
                            source_graph_id=f"{dataset_id}:{doc_id}",
                            target_graph_id=f"{dataset_id}:{target_id}",
                            doc_id=doc_id,
                            target_id=target_id,
                            dataset_id=dataset_id,
                            target_doc_num=ref.get("target_doc_num", ""),
                            target_title=ref.get("target_title", ""),
                            relationship_id=relationship_id,
                            relationship_label=relationship_label,
                            canonical_type=canonical_type,
                            relationship_type=str(ref.get("reference_type_name") or _relationship_display(raw_type)),
                            raw_type=str(raw_type) if raw_type is not None else "",
                            reference_id=str(ref.get("reference_id") or ""),
                            reference_type_name=str(ref.get("reference_type_name") or ""),
                            reference_provisions=_canonical_reference_type(
                                ref.get("reference_provisions", [])
                            ),
                        )
            _stage_status(result, "neo4j", "success")
        except Exception as error:
            logger.error("Neo4j ingest failed for %s", vbpl_id, exc_info=error)
            _stage_status(result, "neo4j", "failed")
            _append_error(result, "neo4j", error)
        finally:
            if neo4j is not None:
                try:
                    await neo4j.close()
                except Exception as error:
                    logger.warning("Neo4j close failed for %s: %s", vbpl_id, _safe_error(error))

        _finalize_status(result)
        return result


__all__ = ["VbplIngestService", "chunk_text", "html_to_visible_text"]
