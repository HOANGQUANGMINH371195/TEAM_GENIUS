"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchDocumentHtml } from "../../lib/api";

export default function DocumentPage() {
  const [html, setHtml] = useState("");
  const [error, setError] = useState("");
  const [number] = useState(() => typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("number") || "");
  useEffect(() => {
    if (!number) { Promise.resolve().then(() => setError("Thiếu số/ký hiệu văn bản")); return; }
    fetchDocumentHtml(number).then(setHtml).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Không thể tải văn bản"));
  }, [number]);
  return <main className="document-viewer" aria-live="polite">
    <Link href="/">← Quay lại tra cứu</Link>
    <h1>Văn bản nguồn</h1>
    {error ? <p role="alert">{error}</p> : html ? <div dangerouslySetInnerHTML={{ __html: html }} /> : <p>Đang tải văn bản…</p>}
  </main>;
}
