"use client";

import { type FormEvent, useState } from "react";
import Link from "next/link";
import { fetchLegalTimeline, type LegalTimelineResponse } from "../../lib/api";
import { FeatureShell } from "../../components/feature-shell";

const stateLabels: Record<string, string> = { not_yet_effective: "Chưa có hiệu lực", effective: "Đang có hiệu lực", expired: "Đã hết hiệu lực", unknown: "Chưa đủ dữ liệu" };
const relationLabels: Record<string, string> = { "Sửa đổi, bổ sung": "Sửa đổi / bổ sung", "Thay thế": "Thay thế", "Bãi bỏ": "Bãi bỏ", "Dẫn chiếu": "Dẫn chiếu", "Căn cứ": "Căn cứ" };

function dateLabel(value: string) {
  if (!value) return "Chưa cập nhật";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("vi-VN", { dateStyle: "medium" }).format(date);
}

export default function TimelinePage() {
  const [documentNumber, setDocumentNumber] = useState("");
  const [asOf, setAsOf] = useState("");
  const [timeline, setTimeline] = useState<LegalTimelineResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setTimeline(null); setLoading(true);
    try { setTimeline(await fetchLegalTimeline(documentNumber.trim(), asOf || undefined)); }
    catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "Không thể tải dòng thời gian"); }
    finally { setLoading(false); }
  }

  return <FeatureShell active="timeline" eyebrow="Tra cứu hiệu lực" title="Dòng thời gian pháp lý" description="Theo dõi văn bản gốc, văn bản sửa đổi và quan hệ dẫn chiếu trong release hiện hành.">
    <div className="bhyt-feature-card bhyt-timeline-search"><form onSubmit={submit}><label>Số hoặc ký hiệu văn bản<input required maxLength={80} value={documentNumber} onChange={(event) => setDocumentNumber(event.target.value)} placeholder="Ví dụ: 51/2024/QH15" /></label><label>Ngày cần đối chiếu<input type="date" value={asOf} onChange={(event) => setAsOf(event.target.value)} /></label><button className="bhyt-feature-primary" type="submit" disabled={loading}>{loading ? "Đang đối chiếu…" : "Xem dòng thời gian"}<span aria-hidden="true">→</span></button></form><p className="bhyt-feature-hint">Ngày bỏ trống sẽ dùng ngày hiện tại. Kết quả chỉ hiển thị metadata công khai, không lộ mã lưu trữ.</p></div>
    {error ? <p className="bhyt-feature-error" role="alert">{error}</p> : null}
    {timeline ? <TimelineResult timeline={timeline} /> : <div className="bhyt-feature-card bhyt-feature-empty bhyt-timeline-empty"><span aria-hidden="true">◷</span><p>Nhập số/ký hiệu một văn bản để dựng chuỗi hiệu lực và quan hệ pháp lý.</p></div>}
  </FeatureShell>;
}

function TimelineResult({ timeline }: { timeline: LegalTimelineResponse }) {
  const query = timeline.query_document;
  return <div className="bhyt-timeline-result"><section className="bhyt-feature-card bhyt-timeline-summary"><div><p className="bhyt-feature-eyebrow">Văn bản đang tra cứu</p><h2>{query.document_number}</h2><p>{query.title || "Chưa có tiêu đề công khai"}</p></div><span className={`bhyt-status-pill ${query.state_at_date}`}>{stateLabels[query.state_at_date] ?? stateLabels.unknown}</span><dl><div><dt>Ban hành</dt><dd>{dateLabel(query.issued_at)}</dd></div><div><dt>Có hiệu lực từ</dt><dd>{dateLabel(query.effective_from)}</dd></div><div><dt>Đối chiếu tại</dt><dd>{dateLabel(timeline.as_of)}</dd></div></dl>{timeline.degraded ? <p className="bhyt-feature-warning" role="status">Đồ thị quan hệ đang tạm gián đoạn. Metadata canonical vẫn được hiển thị; hãy thử lại để lấy đủ chuỗi quan hệ.</p> : null}</section><section className="bhyt-feature-card"><div className="bhyt-feature-card-heading"><span className="bhyt-feature-step">01</span><div><h2>Chuỗi văn bản</h2><p>{timeline.documents.length} văn bản đã được đối chiếu trong release hiện tại.</p></div></div><ol className="bhyt-timeline-list">{timeline.documents.map((document, index) => <li key={`${document.document_number}-${index}`}><span className="bhyt-timeline-dot" aria-hidden="true" /><div><div className="bhyt-timeline-item-head"><strong>{document.document_number}</strong><span className={`bhyt-status-pill ${document.state_at_date}`}>{stateLabels[document.state_at_date] ?? stateLabels.unknown}</span></div><p>{document.title || "Chưa có tiêu đề công khai"}</p><small>Hiệu lực: {dateLabel(document.effective_from)}{document.effective_to ? ` – ${dateLabel(document.effective_to)}` : ""}</small>{document.viewer_url ? <Link href={document.viewer_url}>Xem văn bản gốc ↗</Link> : null}</div></li>)}</ol></section><section className="bhyt-feature-card"><div className="bhyt-feature-card-heading"><span className="bhyt-feature-step">02</span><div><h2>Quan hệ pháp lý</h2><p>Các quan hệ đã được hydrate về văn bản canonical.</p></div></div>{timeline.events.length ? <div className="bhyt-event-table">{timeline.events.map((event, index) => <div className="bhyt-event-row" key={`${event.source_document_number}-${event.relation}-${event.target_document_number}-${index}`}><strong>{event.source_document_number}</strong><span className={event.adverse ? "is-adverse" : ""}>{relationLabels[event.relation] ?? event.relation}</span><strong>{event.target_document_number}</strong></div>)}</div> : <p className="bhyt-feature-muted">Chưa có quan hệ đã duyệt cho văn bản này.</p>}</section></div>;
}
