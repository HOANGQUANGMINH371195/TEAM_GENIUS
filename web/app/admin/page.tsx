"use client";

import { useCallback, useEffect, useState } from "react";
import { fetchAdminObservability, type AdminObservabilityResponse, type ObservabilityRange } from "../../lib/api";

const RANGES: Array<{ value: ObservabilityRange; label: string }> = [
  { value: "today", label: "Hôm nay" },
  { value: "7d", label: "7 ngày" },
  { value: "30d", label: "30 ngày" },
  { value: "90d", label: "90 ngày" },
];

function formatNumber(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 0 }).format(value)
    : "Chưa quan sát";
}

function formatPercent(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "Chưa quan sát";
}

function formatLatency(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${Math.round(value)} ms` : "Chưa quan sát";
}

function formatCost(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(value)
    : "Chưa quan sát";
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("vi-VN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function metric(response: AdminObservabilityResponse | null, key: keyof AdminObservabilityResponse["summary"]): number | null {
  const value = response?.summary?.[key]?.value;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function DataTable({ data }: { data: AdminObservabilityResponse["series"] }) {
  return <details className="admin-viz-table"><summary>Xem bảng dữ liệu</summary><div className="admin-viz-table-scroll"><table><caption className="sr-only">Dữ liệu Langfuse theo thời gian</caption><thead><tr><th scope="col">Thời điểm</th><th scope="col">Requests</th><th scope="col">Lỗi</th><th scope="col">P95 latency</th><th scope="col">Tokens</th><th scope="col">Chi phí</th></tr></thead><tbody>{data.map((point, index) => <tr key={`${point.timestamp}-${index}`}><th scope="row">{formatTime(point.timestamp)}</th><td>{formatNumber(point.requests)}</td><td>{formatNumber(point.errors)}</td><td>{formatLatency(point.p95_latency_ms)}</td><td>{formatNumber(point.total_tokens)}</td><td>{formatCost(point.total_cost_usd)}</td></tr>)}</tbody></table></div></details>;
}

function TrafficChart({ data }: { data: AdminObservabilityResponse["series"] }) {
  const max = Math.max(...data.map((point) => point.requests ?? 0), 1);
  return <section className="admin-viz-card" aria-labelledby="admin-traffic-title"><div className="admin-viz-heading"><div><p className="admin-eyebrow">Nhịp hoạt động</p><h2 id="admin-traffic-title">Requests theo thời gian</h2></div><span className="admin-viz-key"><i className="admin-viz-dot is-teal" />Requests</span></div>{data.length === 0 ? <p className="admin-viz-empty">Chưa có điểm dữ liệu.</p> : <div className="admin-bars" role="img" aria-label="Biểu đồ requests theo thời gian">{data.map((point, index) => <div className="admin-bar-group" key={`${point.timestamp}-${index}`} tabIndex={0} title={`${formatTime(point.timestamp)}: ${formatNumber(point.requests)} requests`}><div className="admin-bar-track"><span style={{ height: `${Math.max(point.requests ? 4 : 1, ((point.requests ?? 0) / max) * 100)}%` }} /></div><small>{new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit" }).format(new Date(point.timestamp))}</small></div>)}</div>}<DataTable data={data} /></section>;
}

function LatencyChart({ data }: { data: AdminObservabilityResponse["series"] }) {
  const values = data.map((point) => point.p95_latency_ms ?? 0);
  const max = Math.max(...values, 1);
  return <section className="admin-viz-card" aria-labelledby="admin-latency-title"><div className="admin-viz-heading"><div><p className="admin-eyebrow">Độ trễ</p><h2 id="admin-latency-title">P95 latency</h2></div><span className="admin-viz-key"><i className="admin-viz-dot is-sky" />Milliseconds</span></div>{data.length === 0 ? <p className="admin-viz-empty">Chưa có điểm dữ liệu.</p> : <div className="admin-line-chart" role="img" aria-label="Biểu đồ p95 latency theo thời gian"><svg viewBox="0 0 600 180" preserveAspectRatio="none" aria-hidden="true"><path className="admin-chart-grid" d="M0 150H600M0 95H600M0 40H600" /><polyline points={values.map((value, index) => `${data.length === 1 ? 300 : (index / (data.length - 1)) * 600},${150 - (value / max) * 110}`).join(" ")} /></svg><div className="admin-chart-labels">{data.map((point, index) => <span key={`${point.timestamp}-${index}`} tabIndex={0} title={`${formatTime(point.timestamp)}: ${formatLatency(point.p95_latency_ms)}`}>{new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit" }).format(new Date(point.timestamp))}</span>)}</div></div>}<DataTable data={data} /></section>;
}

export default function AdminIndexPage() {
  const [range, setRange] = useState<ObservabilityRange>("7d");
  const [data, setData] = useState<AdminObservabilityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try { setData(await fetchAdminObservability(range)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể tải dữ liệu giám sát"); } finally { setLoading(false); }
  }, [range]);
  useEffect(() => {
    const timer = setTimeout(() => void load(), 0);
    return () => clearTimeout(timer);
  }, [load]);
  const series = data?.series ?? [];
  return <main className={`admin-dashboard${loading && data ? " is-refreshing" : ""}`}>
    <header className="admin-dashboard-hero"><div><p className="admin-dashboard-kicker"><span />Telemetry / Langfuse</p><h1>Nhìn rõ hệ thống đang chạy</h1><p className="admin-dashboard-intro">Số liệu vận hành lấy trực tiếp từ Langfuse. Không có dữ liệu giả.</p></div><button className="admin-dashboard-refresh" type="button" onClick={() => void load()} disabled={loading}><span className={loading ? "is-spinning" : ""}>↻</span>{loading ? "Đang tải..." : "Làm mới"}</button></header>
    <div className="admin-dashboard-toolbar" role="group" aria-label="Bộ lọc dữ liệu"><span className="admin-dashboard-toolbar-label">Khoảng thời gian</span>{RANGES.map((item) => <button className={range === item.value ? "is-active" : ""} key={item.value} type="button" onClick={() => setRange(item.value)} aria-pressed={range === item.value}>{item.label}</button>)}{data?.updated_at ? <span className="admin-dashboard-updated">Cập nhật {formatTime(data.updated_at)}</span> : null}</div>
    {error ? <div className="admin-dashboard-alert" role="alert"><strong>Không thể tải telemetry</strong><span>{error}</span><button type="button" onClick={() => void load()}>Thử lại</button></div> : null}
    {data && !data.available ? <div className="admin-dashboard-unavailable" role="status"><span className="admin-dashboard-unavailable-mark">!</span><div><strong>Chưa có telemetry khả dụng</strong><p>{data.reason}</p><small>Kiểm tra cấu hình Langfuse trên backend.</small></div></div> : null}
    {loading && !data ? <div className="admin-dashboard-skeleton" aria-label="Đang tải dữ liệu"><span /><span /><span /></div> : null}
    <section className="admin-kpi-grid" aria-label="Chỉ số chính"><article className="admin-kpi-card is-hero"><span className="admin-kpi-label">Requests đã ghi nhận</span><strong>{formatNumber(metric(data, "requests"))}</strong><small>{data?.available ? "Trong khoảng đã chọn" : "Chưa quan sát"}</small></article><article className="admin-kpi-card"><span className="admin-kpi-label">Tỷ lệ lỗi</span><strong>{formatPercent(metric(data, "error_rate"))}</strong><small>Observation có mức lỗi</small></article><article className="admin-kpi-card"><span className="admin-kpi-label">P95 latency</span><strong>{formatLatency(metric(data, "p95_latency_ms"))}</strong><small>Thời gian phản hồi</small></article><article className="admin-kpi-card"><span className="admin-kpi-label">Tổng tokens</span><strong>{formatNumber(metric(data, "total_tokens"))}</strong><small>Input + output</small></article><article className="admin-kpi-card"><span className="admin-kpi-label">Chi phí ghi nhận</span><strong>{formatCost(metric(data, "total_cost_usd"))}</strong><small>USD từ Langfuse</small></article></section>
    {data?.available ? <div className="admin-viz-grid"><TrafficChart data={series} /><LatencyChart data={series} /></div> : null}
    {data?.available && data.breakdowns.length > 0 ? <section className="admin-viz-card admin-breakdown-card" aria-labelledby="admin-breakdown-title"><div className="admin-viz-heading"><div><p className="admin-eyebrow">Phân bổ hoạt động</p><h2 id="admin-breakdown-title">Theo operation</h2></div></div><div className="admin-breakdown-list">{data.breakdowns.map((item, index) => <div className="admin-breakdown-row" key={`${item.name}-${index}`}><span>{item.name}</span><strong>{formatNumber(item.requests)}</strong></div>)}</div></section> : null}
    <footer className="admin-dashboard-footnote"><span className="admin-dashboard-live-dot" />Nguồn dữ liệu: Langfuse Metrics API <span>·</span> Chỉ admin được xem</footer>
  </main>;
}
