from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from src.services.vbpl_cache import VbplCache

logger = logging.getLogger(__name__)

CACHE_FILE = Path("data/vbpl_session_cache.json")
SESSION_LOCK = "vbpl:session:refresh"


class VbplSessionError(Exception):
    """Raised when VBPL session is expired or invalid."""


class VbplSessionManager:
    """VBPL Session Manager handling cookie caching and Next-Action header."""

    @classmethod
    def _load_cache(cls) -> dict[str, Any]:
        if CACHE_FILE.exists():
            try:
                return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    @classmethod
    def _save_cache(cls, data: dict[str, Any]) -> None:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @classmethod
    async def _load_session(cls) -> dict[str, Any]:
        return await VbplCache.get_session() or cls._load_cache()

    @classmethod
    async def _save_session(cls, data: dict[str, Any]) -> None:
        if VbplCache.configured():
            await VbplCache.set_session(data)
        else:
            cls._save_cache(data)

    @classmethod
    async def fetch_latest_documents(cls) -> list[dict[str, Any]]:
        cache = await cls._load_session()
        cookies = cache.get("cookies", {})
        next_action = cache.get("next_action", "")

        headers = {
            "Accept": "text/x-component",
            "Content-Type": "text/plain;charset=UTF-8",
            "Next-Action": next_action,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = '[{"sortDirection":"desc","sortBy":"issueDate","pageSize":10,"pageNumber":1,"isNew":true}]'
            resp = await client.post("https://vbpl.vn/", headers=headers, cookies=cookies, content=payload)

            # Session expired or Next-Action changed after Next.js redeploy
            if resp.status_code in (401, 403, 404):
                raise VbplSessionError(f"HTTP {resp.status_code}")

            resp.raise_for_status()

            # Parse RSC flight format: lines like "1:{...json...}"
            # The document list lives in the line starting with "1:" which
            # contains {"total", "pageNumber", "pageSize", "items": [...]}.
            lines = resp.text.splitlines()
            for line in lines:
                if not re.match(r"^\d+:", line):
                    continue
                try:
                    content = line.split(":", 1)[1]
                    data = json.loads(content)
                    items = cls._extract_items(data)
                    if items:
                        return items
                except (json.JSONDecodeError, ValueError):
                    continue

            # If no items parsed, the RSC format likely changed
            raise VbplSessionError("Could not parse items from RSC response")

    @classmethod
    def _extract_items(cls, data: Any) -> list[dict[str, Any]]:
        """Recursively find the items array from RSC flight data."""
        if isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                return data["items"]
            for v in data.values():
                found = cls._extract_items(v)
                if found:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = cls._extract_items(item)
                if found:
                    return found
        return []

    @classmethod
    async def fetch_document_detail(cls, doc_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/{doc_id}")
            resp.raise_for_status()
            raw = resp.json()
            # Actual API returns {"success": true, "data": {...}}
            data = raw.get("data", raw)

            # Normalize content_html from nested documentContent.content
            content_html = ""
            doc_content = data.get("documentContent")
            if isinstance(doc_content, dict):
                content_html = doc_content.get("content", "") or ""
            elif isinstance(doc_content, str):
                content_html = doc_content

            # Normalize references to a flat list while retaining source IDs,
            # provisions, and every raw referenceType from official API.
            references = []
            for ref in data.get("references", []) or []:
                if not isinstance(ref, dict):
                    continue
                target = ref.get("targetDocument") or {}
                if not isinstance(target, dict):
                    target = {}
                raw_type = ref.get("referenceType")
                references.append({
                    "reference_id": str(ref.get("id") or ref.get("referenceId") or ""),
                    "reference_type": raw_type,
                    "reference_type_json": json.dumps(
                        raw_type,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "reference_type_name": ref.get("referenceTypeName", ""),
                    "reference_provisions": ref.get("referenceProvisions", []),
                    "target_id": str(target.get("id", "")),
                    "target_doc_num": target.get("docNum", ""),
                    "target_title": target.get("title", ""),
                    "target_issue_date": target.get("issueDate", ""),
                    "target_status": target.get("status", ""),
                })

            references = [
                reference for reference in references if reference["target_id"]
            ]

            return {
                "id": data.get("id"),
                "title": data.get("title", ""),
                "doc_num": data.get("docNum", ""),
                "doc_type": (data.get("docType") or {}).get("name", ""),
                "doc_type_code": (data.get("docType") or {}).get("code", ""),
                "issue_date": data.get("issueDate", ""),
                "effective_from": data.get("effFrom", ""),
                "effective_to": data.get("effTo"),
                "public_date": data.get("publicDate", ""),
                "legal_status": (data.get("effStatus") or {}).get("name", ""),
                "legal_status_code": (data.get("effStatus") or {}).get("code", ""),
                "issuing_body": data.get("agencyName", ""),
                "content_html": content_html,
                "references": references,
            }

    @classmethod
    async def refresh_session(cls) -> None:
        async with VbplCache.lock(SESSION_LOCK, 120) as acquired:
            if not acquired:
                return
            await cls._refresh_session_locked()

    @classmethod
    async def _refresh_session_locked(cls) -> None:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                )
                page = await context.new_page()
                next_action = ""

                def capture_next_action(request: Any) -> None:
                    nonlocal next_action
                    value = request.headers.get("Next-Action", "")
                    if re.fullmatch(r"[a-f0-9]{40}", value or ""):
                        next_action = value

                # Listen before navigation; Next.js emits the action request
                # during the initial page load, not after it.
                page.on("request", capture_next_action)
                await page.goto("https://vbpl.vn/", wait_until="networkidle")
                await page.wait_for_timeout(500)

                cookies = await context.cookies()
                cookie_dict = {cookie["name"]: cookie["value"] for cookie in cookies}
                if not next_action:
                    # Keep a compatibility fallback for deployments where the
                    # action request is issued by a late client-side render.
                    content = await page.content()
                    matches = re.findall(r"[a-f0-9]{40}", content)
                    next_action = matches[0] if matches else ""
                if not next_action:
                    raise VbplSessionError("Next-Action header was not captured")

                await cls._save_session({
                    "cookies": cookie_dict,
                    "next_action": next_action,
                })
            finally:
                await browser.close()

    @classmethod
    async def fetch_with_fallback(cls) -> list[dict[str, Any]]:
        try:
            return await cls.fetch_latest_documents()
        except VbplSessionError as error:
            logger.warning(
                "fetch_latest_documents failed, trying session refresh: %s",
                type(error).__name__,
            )
            await cls.refresh_session()
            return await cls.fetch_latest_documents()

    @classmethod
    async def close(cls) -> None:
        """Compatibility hook for workers; cache clients are request-scoped."""
        return None


__all__ = ["VbplSessionError", "VbplSessionManager"]
