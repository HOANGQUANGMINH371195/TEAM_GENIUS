"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchVbplDiscover,
  fetchVbplSyncStatus,
  startVbplSync,
  type VbplDiscoveryEnvelope,
  type VbplDiscoveryItem,
  type VbplRefreshStatus,
} from "../../lib/api";

type Props = {
  onIngest: (docIds: string[]) => void;
  reloadToken?: number;
};

const EMPTY_ENVELOPE: VbplDiscoveryEnvelope = {
  items: [],
  last_synced_at: null,
  stale: true,
  refresh_status: "idle",
};

const BHYT_KEYWORDS = ["bảo hiểm y tế", "viện phí", "bhyt", "khám chữa bệnh", "bảo hiểm xã hội"];
const TERMINAL_SYNC_STATUSES: VbplRefreshStatus[] = ["succeeded", "failed"];

function isHealthRelated(item: VbplDiscoveryItem): boolean {
  if (item.is_health_related) return true;
  const searchable = `${item.title} ${item.summary}`.toLocaleLowerCase("vi");
  return BHYT_KEYWORDS.some((keyword) => searchable.includes(keyword));
}

function formatDate(value: string | null): string {
  if (!value) return "Chưa có lần đồng bộ";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("vi-VN", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function formatIssueDate(value: string | null | undefined): string {
  const match = value?.trim().match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!match) return "—";
  const [, year, month, day] = match;
  const date = new Date(Number(year), Number(month) - 1, Number(day));
  if (date.getFullYear() !== Number(year) || date.getMonth() !== Number(month) - 1 || date.getDate() !== Number(day)) return "—";
  return new Intl.DateTimeFormat("vi-VN", { dateStyle: "medium" }).format(date);
}

function statusLabel(status: VbplRefreshStatus): string {
  switch (status) {
    case "queued": return "Đang xếp hàng";
    case "running": return "Đang đồng bộ";
    case "succeeded": return "Đã cập nhật";
    case "failed": return "Đồng bộ lỗi";
    default: return "Sẵn sàng";
  }
}

function ingestionLabel(status: VbplDiscoveryItem["ingestion_status"]): string {
  switch (status) {
    case "queued": return "Đang chờ nạp";
    case "running": return "Đang nạp";
    case "succeeded": return "Đã có trong kho";
    case "partial": return "Nạp một phần";
    case "failed": return "Nạp lỗi";
    default: return "Chưa nạp";
  }
}

export function VbplDiscovery({ onIngest, reloadToken = 0 }: Props) {
  const [envelope, setEnvelope] = useState<VbplDiscoveryEnvelope>(EMPTY_ENVELOPE);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [healthOnly, setHealthOnly] = useState(false);
  const [syncStatus, setSyncStatus] = useState<VbplRefreshStatus>("idle");
  const syncTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const syncRun = useRef(0);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const nextEnvelope = await fetchVbplDiscover();
      setEnvelope(nextEnvelope);
      setSyncStatus(nextEnvelope.refresh_status);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể tải danh sách văn bản mới");
      // Keep last successful envelope visible. Operators can retry without losing context.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => void loadDocuments(), 0);
    return () => clearTimeout(timer);
  }, [loadDocuments, reloadToken]);

  useEffect(() => () => {
    syncRun.current += 1;
    if (syncTimer.current) clearTimeout(syncTimer.current);
  }, []);

  async function pollSync(pollUrl: string, runId: number): Promise<void> {
    try {
      const result = await fetchVbplSyncStatus(pollUrl);
      if (runId !== syncRun.current) return;
      setSyncStatus(result.status);
      if (TERMINAL_SYNC_STATUSES.includes(result.status)) {
        setSyncing(false);
        if (result.status === "succeeded") await loadDocuments();
        else setError("Nguồn VBPL không hoàn tất đồng bộ. Hãy thử lại sau.");
        return;
      }
      syncTimer.current = setTimeout(() => void pollSync(result.poll_url || pollUrl, runId), 1600);
    } catch (reason) {
      if (runId !== syncRun.current) return;
      setSyncing(false);
      setSyncStatus("failed");
      setError(reason instanceof Error ? reason.message : "Không thể kiểm tra tiến trình đồng bộ");
    }
  }

  const filteredItems = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("vi");
    return envelope.items.filter((item) => {
      if (healthOnly && !isHealthRelated(item)) return false;
      if (!normalizedQuery) return true;
      return `${item.title} ${item.so_ky_hieu} ${item.issuing_body} ${item.summary}`
        .toLocaleLowerCase("vi")
        .includes(normalizedQuery);
    });
  }, [envelope.items, healthOnly, query]);

  const selectableFilteredIds = filteredItems
    .filter((item) => item.ingestion_status !== "succeeded")
    .map((item) => item.doc_id);
  const selectedVisibleCount = selectableFilteredIds.filter((id) => selected.has(id)).length;

  const toggleSelect = (docId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(docId)) next.delete(docId);
      else if (next.size < 10) next.add(docId);
      return next;
    });
  };

  const selectVisible = () => {
    setSelected((current) => {
      const next = new Set(current);
      for (const docId of selectableFilteredIds) {
        if (next.size >= 10) break;
        next.add(docId);
      }
      return next;
    });
  };

  const clearSelection = () => setSelected(new Set());

  const handleSync = async () => {
    if (syncing) return;
    if (syncTimer.current) clearTimeout(syncTimer.current);
    const runId = syncRun.current + 1;
    syncRun.current = runId;
    setSyncing(true);
    setSyncStatus("queued");
    setError(null);
    try {
      const accepted = await startVbplSync();
      setSyncStatus(accepted.status);
      void pollSync(accepted.poll_url || accepted.refresh_id, runId);
    } catch (reason) {
      setSyncing(false);
      setSyncStatus("failed");
      setError(reason instanceof Error ? reason.message : "Không thể bắt đầu đồng bộ VBPL");
    }
  };

  const handleIngest = () => {
    if (selected.size === 0) return;
    if (selected.size > 10) {
      setError("Mỗi lần nạp tối đa 10 văn bản.");
      return;
    }
    onIngest(Array.from(selected));
  };

  const allVisibleSelected = selectableFilteredIds.length > 0 && selectedVisibleCount === selectableFilteredIds.length;
  const displayStatus = syncing ? syncStatus : envelope.refresh_status;

  return (
    <section className="vbpl-discovery" aria-labelledby="vbpl-page-title">
      <header className="vbpl-hero">
        <div className="vbpl-hero-copy">
          <p className="vbpl-kicker"><span className="vbpl-kicker-rule" />Kho văn bản pháp quy</p>
          <h1 id="vbpl-page-title">Nguồn mới cho GraphRAG</h1>
          <p>Chọn văn bản chính thức từ VBPL để đưa vào kho tri thức BHYT. Mỗi đợt nạp được theo dõi riêng qua ba giai đoạn.</p>
        </div>
        <div className="vbpl-sync-card" aria-live="polite">
          <div className="vbpl-sync-card-topline"><span className={`vbpl-sync-dot is-${displayStatus}`} aria-hidden="true" /><span>{statusLabel(displayStatus)}</span></div>
          <strong>{formatDate(envelope.last_synced_at)}</strong>
          <button className="vbpl-sync-button" type="button" onClick={handleSync} disabled={syncing}>
            <SyncIcon spinning={syncing} />{syncing ? "Đang đồng bộ..." : "Đồng bộ nguồn"}
          </button>
        </div>
      </header>

      {envelope.stale ? <div className="vbpl-stale-note" role="status"><span aria-hidden="true">!</span><div><strong>Dữ liệu có thể đã cũ.</strong><span>Đồng bộ nguồn để nhận danh sách VBPL mới nhất.</span></div></div> : null}
      {error ? <div className="vbpl-error" role="alert"><span aria-hidden="true">!</span><div><strong>Không thể hoàn tất thao tác</strong><p>{error}</p></div><button type="button" onClick={() => void loadDocuments()}>Thử lại</button></div> : null}

      <div className="vbpl-toolbar">
        <div className="vbpl-toolbar-heading"><span className="vbpl-eyebrow">Danh mục phát hiện</span><strong>{envelope.items.length} văn bản</strong></div>
        <div className="vbpl-toolbar-controls">
          <label className="vbpl-search"><SearchIcon /><span className="sr-only">Tìm trong danh mục</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm tên, số hiệu, cơ quan..." /></label>
          <label className="vbpl-check-filter"><input type="checkbox" checked={healthOnly} onChange={(event) => setHealthOnly(event.target.checked)} /> <span>Chỉ hiện văn bản BHYT</span></label>
        </div>
      </div>

      <div className="vbpl-selection-tools">
        <div><button className="vbpl-text-button" type="button" onClick={selectVisible} disabled={selectableFilteredIds.length === 0 || allVisibleSelected}>Chọn danh sách hiện tại</button><button className="vbpl-text-button is-quiet" type="button" onClick={clearSelection} disabled={selected.size === 0}>Bỏ chọn</button></div>
        <span>{selected.size}/10 đã chọn</span>
      </div>

      <div className="vbpl-table-shell">
        {loading && envelope.items.length === 0 ? <div className="vbpl-loading" role="status"><span className="vbpl-loader" aria-hidden="true" /><p>Đang tải danh sách văn bản...</p></div> : null}
        {!loading && filteredItems.length === 0 ? <div className="vbpl-empty"><span className="vbpl-empty-mark" aria-hidden="true">§</span><h2>Chưa có văn bản phù hợp</h2><p>Thử đổi từ khóa hoặc đồng bộ lại nguồn VBPL.</p></div> : null}
        {filteredItems.length > 0 ? <div className="vbpl-table-scroll"><table className="vbpl-table"><caption className="sr-only">Danh sách văn bản pháp luật phát hiện từ VBPL</caption><thead><tr><th scope="col" className="vbpl-table-select"><span className="sr-only">Chọn</span><input aria-label="Chọn tất cả văn bản hiện tại" type="checkbox" checked={allVisibleSelected} onChange={allVisibleSelected ? clearSelection : selectVisible} /></th><th scope="col">Văn bản</th><th scope="col">Ngày ban hành</th><th scope="col">Cơ quan</th><th scope="col">Trạng thái kho</th></tr></thead><tbody>{filteredItems.map((item) => {
          const imported = item.ingestion_status === "succeeded";
          return <tr key={item.doc_id} className={`${isHealthRelated(item) ? "is-health" : ""}${selected.has(item.doc_id) ? " is-selected" : ""}`}>
            <td className="vbpl-table-select"><input aria-label={`Chọn ${item.title}`} type="checkbox" checked={selected.has(item.doc_id)} disabled={imported} onChange={() => toggleSelect(item.doc_id)} /></td>
            <td><div className="vbpl-document-cell"><span className={`vbpl-document-mark${isHealthRelated(item) ? " is-health" : ""}`} aria-hidden="true">§</span><div><strong>{item.title || "Chưa có tiêu đề"}</strong><span className="vbpl-document-meta"><code>{item.so_ky_hieu || "Chưa có số hiệu"}</code>{isHealthRelated(item) ? <em>BHYT</em> : null}</span>{item.summary ? <small>{item.summary}</small> : null}</div></div></td>
            <td className="vbpl-date">{formatIssueDate(item.issue_date)}</td>
            <td className="vbpl-agency">{item.issuing_body || "—"}</td>
            <td><span className={`vbpl-ingestion-badge is-${item.ingestion_status ?? "not_imported"}`}><span aria-hidden="true" />{ingestionLabel(item.ingestion_status)}</span></td>
          </tr>;
        })}</tbody></table></div> : null}
      </div>

      <footer className={`vbpl-batch-bar${selected.size > 0 ? " is-visible" : ""}`} aria-live="polite">
        <div><span className="vbpl-batch-count">{selected.size}</span><div><strong>{selected.size > 0 ? "Văn bản sẵn sàng để nạp" : "Chưa chọn văn bản"}</strong><span>{selected.size > 0 ? "Nội dung sẽ đi qua database, embedding và relationships." : "Chọn một hoặc nhiều dòng để bắt đầu."}</span></div></div>
        <button className="vbpl-primary-button" type="button" onClick={handleIngest} disabled={selected.size === 0}>Mở quy trình nạp <span aria-hidden="true">→</span></button>
      </footer>
    </section>
  );
}

function SyncIcon({ spinning }: { spinning: boolean }) {
  return <svg className={spinning ? "is-spinning" : ""} aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M20 11a8.1 8.1 0 0 0-14.4-4.9L4 8" /><path d="M4 4v4h4" /><path d="M4 13a8.1 8.1 0 0 0 14.4 4.9L20 16" /><path d="M20 20v-4h-4" /></svg>;
}

function SearchIcon() {
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 5 5" /></svg>;
}
