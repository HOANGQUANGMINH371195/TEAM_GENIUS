"use client";

import { type FormEvent, useState } from "react";
import Link from "next/link";

import {
  fetchLegalTimeline,
  type LegalTimelineResponse,
} from "../../lib/api";

const stateLabels: Record<string, string> = {
  not_yet_effective: "Chưa có hiệu lực tại ngày tra cứu",
  effective: "Có hiệu lực theo khoảng ngày đã xác minh",
  expired: "Đã hết khoảng hiệu lực tại ngày tra cứu",
  unknown: "Chưa đủ metadata để xác định",
};

export default function TimelinePage() {
  const [documentNumber, setDocumentNumber] = useState("");
  const [asOf, setAsOf] = useState("");
  const [timeline, setTimeline] = useState<LegalTimelineResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setTimeline(null);
    setLoading(true);
    try {
      setTimeline(await fetchLegalTimeline(documentNumber.trim(), asOf || undefined));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Không thể tải dòng thời gian");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="document-viewer" aria-live="polite">
      <Link href="/">← Quay lại tra cứu</Link>
      <h1>Dòng thời gian pháp lý</h1>
      <p>Tra cứu quan hệ sửa đổi, thay thế và dẫn chiếu. Mọi nút graph đều được đối chiếu lại với văn bản canonical.</p>
      <form onSubmit={submit} style={{ display: "grid", gap: "0.75rem", maxWidth: 640 }}>
        <label>
          Số/ký hiệu văn bản
          <input required maxLength={80} value={documentNumber} onChange={(event) => setDocumentNumber(event.target.value)} placeholder="51/2024/QH15" />
        </label>
        <label>
          Ngày cần tra cứu
          <input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} />
        </label>
        <button type="submit" disabled={loading}>{loading ? "Đang đối chiếu…" : "Xem dòng thời gian"}</button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
      {timeline ? (
        <section aria-label="Kết quả dòng thời gian">
          <h2>{timeline.query_document.document_number} — {timeline.query_document.title}</h2>
          <p>{stateLabels[timeline.query_document.state_at_date]}</p>
          {timeline.degraded ? <p role="status">Neo4j đang gián đoạn; metadata văn bản vẫn được hiển thị nhưng chuỗi quan hệ có thể chưa đầy đủ.</p> : null}
          <h3>Các văn bản trong chuỗi</h3>
          <ol>
            {timeline.documents.map((document) => (
              <li key={document.document_number}>
                <strong>{document.document_number}</strong> — {document.title}<br />
                <span>{stateLabels[document.state_at_date]}</span>{" "}
                {document.viewer_url ? <Link href={document.viewer_url}>Xem bản gốc</Link> : null}
              </li>
            ))}
          </ol>
          <h3>Quan hệ pháp lý</h3>
          {timeline.events.length ? (
            <ul>
              {timeline.events.map((item) => (
                <li key={`${item.source_document_number}-${item.relation}-${item.target_document_number}`}>
                  {item.source_document_number} → <strong>{item.relation}</strong> → {item.target_document_number}
                </li>
              ))}
            </ul>
          ) : <p>Chưa có quan hệ đã duyệt cho văn bản này trong release hiện tại.</p>}
        </section>
      ) : null}
    </main>
  );
}
