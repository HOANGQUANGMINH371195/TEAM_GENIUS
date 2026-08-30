"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AuthRoute } from "../../components/auth-route";
import { fetchDocumentHtml } from "../../lib/api";

type ViewerStatus = "loading" | "ready" | "error";

export function DocumentViewer({ documentNumber }: { documentNumber: string }) {
  return <AuthRoute><DocumentViewerContent documentNumber={documentNumber} /></AuthRoute>;
}

function DocumentViewerContent({ documentNumber }: { documentNumber: string }) {
  const [status, setStatus] = useState<ViewerStatus>(documentNumber ? "loading" : "error");
  const [html, setHtml] = useState("");
  const [error, setError] = useState(documentNumber ? "" : "Thiếu số/ký hiệu văn bản");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const requestRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const initialFocusRef = useRef(false);

  const load = useCallback(() => {
    requestRef.current?.abort();
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;

    if (!documentNumber) {
      setHtml("");
      setError("Thiếu số/ký hiệu văn bản");
      setStatus("error");
      return;
    }

    const controller = new AbortController();
    requestRef.current = controller;
    setHtml("");
    setError("");
    setStatus("loading");

    void fetchDocumentHtml(documentNumber, controller.signal)
      .then((nextHtml) => {
        if (controller.signal.aborted || requestId !== requestIdRef.current) return;
        if (!nextHtml.trim()) throw new Error("Văn bản không có nội dung để hiển thị");
        setHtml(nextHtml);
        setStatus("ready");
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted || requestId !== requestIdRef.current) return;
        setError(reason instanceof Error ? reason.message : "Không thể tải văn bản");
        setStatus("error");
      })
      .finally(() => {
        if (requestId === requestIdRef.current) requestRef.current = null;
      });
  }, [documentNumber]);

  useEffect(() => {
    const timer = window.setTimeout(() => load(), 0);
    return () => {
      window.clearTimeout(timer);
      requestRef.current?.abort();
      requestIdRef.current += 1;
      requestRef.current = null;
    };
  }, [load]);

  useEffect(() => {
    if (initialFocusRef.current || (status !== "ready" && status !== "error")) return;
    initialFocusRef.current = true;
    window.requestAnimationFrame(() => headingRef.current?.focus({ preventScroll: true }));
  }, [status]);

  return (
    <main className="document-viewer" aria-labelledby="document-title">
      <div className="document-viewer-shell">
        <div className="document-viewer-toolbar">
          <Link href="/">← Quay lại tra cứu</Link>
          <span className="document-viewer-folio">BẢN HTML CANONICAL</span>
        </div>
        <header className="document-viewer-header">
          <div>
            <p className="document-viewer-eyebrow">Hồ sơ văn bản</p>
            <h1 id="document-title" ref={headingRef} tabIndex={-1}>{documentNumber ? `Văn bản nguồn · ${documentNumber}` : "Văn bản nguồn"}</h1>
            <p>Toàn văn đã được làm sạch từ nguồn pháp lý đang có hiệu lực.</p>
          </div>
          <span className="document-viewer-badge">MediPay AI · VBPL</span>
        </header>
        <section className="document-viewer-content" aria-labelledby="document-content-title" aria-busy={status === "loading"}>
          <div className="document-viewer-content-heading">
            <div><p>Phần đọc</p><h2 id="document-content-title">Toàn văn văn bản</h2></div>
            {documentNumber ? <code>{documentNumber}</code> : null}
          </div>
          {status === "loading" ? <div className="document-viewer-state" role="status"><span className="document-viewer-loader" aria-hidden="true" /><p>Đang tải văn bản…</p></div> : null}
          {status === "error" ? <div className="document-viewer-state is-error" role="alert"><strong>Không thể tải văn bản</strong><p>{error}</p>{documentNumber ? <button type="button" onClick={load}>Thử tải lại văn bản</button> : null}</div> : null}
          {status === "ready" ? <div className="document-html" aria-label="Nội dung toàn văn văn bản" dangerouslySetInnerHTML={{ __html: html }} /> : null}
        </section>
      </div>
    </main>
  );
}
