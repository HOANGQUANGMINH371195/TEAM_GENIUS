"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchVbplJob,
  ingestVbplDocuments,
  retryVbplJob,
  type VbplIngestItem,
  type VbplIngestJob,
  type VbplStage,
  type VbplStageName,
  type VbplStageStatus,
} from "../../lib/api";

type Props = {
  docIds: string[];
  job: VbplIngestJob | null;
  onJobChange: (job: VbplIngestJob) => void;
  onClose: () => void;
};

const STAGES: Array<{ name: VbplStageName; label: string; detail: string }> = [
  { name: "database", label: "Cơ sở dữ liệu", detail: "Lưu văn bản và các đoạn trích" },
  { name: "embedding", label: "Embedding / vector DB", detail: "Tạo vector tìm kiếm ngữ nghĩa" },
  { name: "relationships", label: "Quan hệ GraphRAG", detail: "Nối văn bản vào đồ thị tri thức" },
];
const TERMINAL_JOB_STATUSES = new Set(["succeeded", "partial", "failed"]);
const POLL_DELAY_MS = 1400;

function stageMap(item: VbplIngestItem): Record<VbplStageName, VbplStage> {
  const map = {} as Record<VbplStageName, VbplStage>;
  if (Array.isArray(item.stages)) {
    for (const stage of item.stages) map[stage.stage] = stage;
  } else {
    Object.assign(map, item.stages);
  }
  return map;
}

function fallbackStage(name: VbplStageName): VbplStage {
  return { stage: name, status: "pending", attempt: 0, metrics: {}, error: "", retryable: false };
}

function statusText(status: VbplStageStatus): string {
  switch (status) {
    case "running": return "Đang chạy";
    case "succeeded": return "Hoàn tất";
    case "failed": return "Thất bại";
    case "skipped": return "Bỏ qua";
    default: return "Đang chờ";
  }
}

function jobText(status: VbplIngestJob["status"]): string {
  switch (status) {
    case "queued": return "Đang xếp hàng";
    case "running": return "Đang xử lý";
    case "succeeded": return "Đã hoàn tất";
    case "partial": return "Hoàn tất một phần";
    case "failed": return "Cần xử lý lỗi";
  }
}

function metricText(metrics: Record<string, unknown>): string {
  const entries = Object.entries(metrics).filter(([, value]) => value !== null && value !== undefined && value !== "");
  return entries.slice(0, 2).map(([key, value]) => `${key}: ${String(value)}`).join(" · ");
}

export function VbplIngestModal({ docIds, job, onJobChange, onClose }: Props) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<VbplStageName | "all" | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollRun = useRef(0);

  const pollJobRef = useRef<(jobUrl: string, runId: number) => Promise<void>>(async () => undefined);
  const pollJob = useCallback(async (jobUrl: string, runId: number) => {
    try {
      const nextJob = await fetchVbplJob(jobUrl);
      if (runId !== pollRun.current) return;
      onJobChange(nextJob);
      if (TERMINAL_JOB_STATUSES.has(nextJob.status)) return;
      pollTimer.current = setTimeout(() => void pollJobRef.current(nextJob.poll_url || nextJob.job_id, runId), POLL_DELAY_MS);
    } catch (reason) {
      if (runId !== pollRun.current) return;
      setError(reason instanceof Error ? reason.message : "Không thể kiểm tra tiến trình nạp dữ liệu");
      // Polling can resume on next render/open, preserving job state and close behavior.
    }
  }, [onJobChange]);

  useEffect(() => {
    pollJobRef.current = pollJob;
  }, [pollJob]);

  useEffect(() => () => {
    pollRun.current += 1;
    if (pollTimer.current) clearTimeout(pollTimer.current);
  }, []);

  useEffect(() => {
    if (!job || TERMINAL_JOB_STATUSES.has(job.status)) return;
    const runId = pollRun.current + 1;
    pollRun.current = runId;
    void pollJob(job.poll_url || job.job_id, runId);
  }, [job, pollJob]);

  const beginIngest = async () => {
    if (starting || docIds.length === 0) return;
    setStarting(true);
    setError(null);
    setErrorRequestId(null);
    try {
      const accepted = await ingestVbplDocuments(docIds);
      // Idempotent requests can return an existing terminal job. Fetch its
      // durable snapshot instead of fabricating queued stages in the UI.
      const durableJob = await fetchVbplJob(accepted.poll_url || accepted.job_id);
      onJobChange(durableJob);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể bắt đầu nạp văn bản");
      setErrorRequestId(reason instanceof Error && "requestId" in reason ? String(reason.requestId || "") || null : null);
    } finally {
      setStarting(false);
    }
  };

  const retryStage = async (stage?: VbplStageName) => {
    if (!job || retrying) return;
    setRetrying(stage ?? "all");
    setError(null);
    try {
      const accepted = await retryVbplJob(job.job_id, stage);
      const refreshed = await fetchVbplJob(accepted.poll_url || accepted.job_id);
      onJobChange({ ...refreshed, poll_url: accepted.poll_url || refreshed.poll_url });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể thử lại giai đoạn này");
    } finally {
      setRetrying(null);
    }
  };

  const isConfirm = !job;
  const isTerminal = !!job && TERMINAL_JOB_STATUSES.has(job.status);
  const failedStages = useMemo(() => {
    if (!job) return [];
    return job.items.flatMap((item) => STAGES.filter(({ name }) => {
      const stage = stageMap(item)[name];
      return stage?.status === "failed" && stage.retryable;
    }).map(({ name }) => name));
  }, [job]);
  const uniqueFailedStages = Array.from(new Set(failedStages));
  const succeededCount = job?.succeeded_items ?? job?.items.filter((item) => item.status === "succeeded").length ?? 0;
  const totalCount = job?.total_items ?? job?.items.length ?? docIds.length;

  return <div className="vbpl-modal-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="vbpl-modal" role="dialog" aria-modal="true" aria-labelledby="vbpl-ingest-title">
      <header className="vbpl-modal-header"><div><p className="vbpl-eyebrow">Quy trình nạp dữ liệu</p><h2 id="vbpl-ingest-title">Đưa văn bản vào kho tri thức</h2><span>{totalCount} văn bản · Dataset <code>{job?.dataset_id || "sẽ được xác định khi bắt đầu"}</code></span></div><button className="vbpl-close-button" type="button" onClick={onClose} aria-label="Đóng cửa sổ, không hủy tiến trình">×</button></header>
      <div className="vbpl-modal-body">
        {error ? <div className="vbpl-modal-error" role="alert"><strong>Không thể cập nhật</strong><span>{error}</span>{errorRequestId ? <small>Mã yêu cầu: {errorRequestId}</small> : null}</div> : null}
        {isConfirm ? <div className="vbpl-confirm-panel"><span className="vbpl-confirm-mark" aria-hidden="true">↗</span><div><h3>Đã chọn {docIds.length} văn bản</h3><p>Tiến trình chạy bất đồng bộ. Có thể đóng cửa sổ; trạng thái vẫn được giữ lại để mở lại sau.</p></div></div> : <div className={`vbpl-job-summary is-${job.status}`} role="status"><div><span className="vbpl-job-status-dot" aria-hidden="true" /><strong>{jobText(job.status)}</strong><span>{succeededCount}/{totalCount} văn bản hoàn tất</span></div><code>{job.job_id}</code></div>}
        {!isConfirm && job ? <div className="vbpl-job-list">{job.items.map((item) => <JobItem key={item.doc_id} item={item} retrying={retrying} onRetry={retryStage} />)}</div> : null}
        {isConfirm ? <div className="vbpl-stage-preview"><p className="vbpl-eyebrow">Ba giai đoạn được theo dõi</p><StageTimeline item={undefined} compact /></div> : null}
        {isTerminal && uniqueFailedStages.length > 0 ? <div className="vbpl-retry-panel"><div><strong>Một số giai đoạn chưa hoàn tất</strong><span>Thử lại từ giai đoạn lỗi để giữ kết quả đã thành công.</span></div><div className="vbpl-retry-actions">{uniqueFailedStages.map((stage) => <button key={stage} type="button" onClick={() => void retryStage(stage)} disabled={retrying !== null}>{retrying === stage ? "Đang thử..." : `Thử lại ${stage}`}</button>)}<button className="is-primary" type="button" onClick={() => void retryStage()} disabled={retrying !== null}>{retrying === "all" ? "Đang thử..." : "Thử lại toàn bộ lỗi"}</button></div></div> : null}
      </div>
      <footer className="vbpl-modal-footer">{isConfirm ? <><button className="vbpl-secondary-button" type="button" onClick={onClose}>Hủy</button><button className="vbpl-primary-button" type="button" onClick={() => void beginIngest()} disabled={starting}>{starting ? "Đang bắt đầu..." : "Bắt đầu nạp"}<span aria-hidden="true">→</span></button></> : <><span className="vbpl-close-hint">Đóng cửa sổ không hủy tiến trình</span>{isTerminal ? <button className="vbpl-primary-button" type="button" onClick={onClose}>Hoàn tất <span aria-hidden="true">✓</span></button> : <button className="vbpl-secondary-button" type="button" onClick={onClose}>Đóng cửa sổ</button>}</>}</footer>
    </section>
  </div>;
}

function JobItem({ item, retrying, onRetry }: { item: VbplIngestItem; retrying: VbplStageName | "all" | null; onRetry: (stage?: VbplStageName) => void }) {
  const stages = stageMap(item);
  return <article className={`vbpl-job-item is-${item.status}`}>
    <div className="vbpl-job-item-heading"><div><span className="vbpl-job-index" aria-hidden="true">{item.doc_id.slice(-4)}</span><strong>{item.doc_id}</strong></div><span className={`vbpl-item-status is-${item.status}`}>{item.status === "succeeded" ? "Đã nạp" : item.status === "partial" ? "Một phần" : item.status === "failed" ? "Lỗi" : item.status === "running" ? "Đang chạy" : "Đang chờ"}</span></div>
    <StageTimeline item={item} />
    {item.error ? <p className="vbpl-item-error" role="alert">{item.error}</p> : null}
    {STAGES.some(({ name }) => stages[name]?.status === "failed" && stages[name]?.retryable) ? <div className="vbpl-item-retry">{STAGES.filter(({ name }) => stages[name]?.status === "failed" && stages[name]?.retryable).map(({ name }) => <button key={name} type="button" onClick={() => onRetry(name)} disabled={retrying !== null}>{retrying === name ? "Đang thử..." : `Thử lại ${name}`}</button>)}</div> : null}
  </article>;
}

function StageTimeline({ item, compact = false }: { item: VbplIngestItem | undefined; compact?: boolean }) {
  return <ol className={`vbpl-stage-timeline${compact ? " is-compact" : ""}`} aria-label="Tiến trình ba giai đoạn">{STAGES.map(({ name, label, detail }, index) => {
    const stage = item ? stageMap(item)[name] ?? fallbackStage(name) : fallbackStage(name);
    const status = stage.status;
    return <li className={`vbpl-stage is-${status}`} key={name}>
      <span className="vbpl-stage-marker" aria-hidden="true">{status === "succeeded" ? "✓" : status === "failed" ? "!" : status === "running" ? <span className="vbpl-stage-spinner" /> : index + 1}</span>
      <div className="vbpl-stage-copy"><strong>{label}</strong><span>{statusText(status)}{stage.attempt > 0 ? ` · lần ${stage.attempt}` : ""}</span>{!compact && metricText(stage.metrics) ? <small>{metricText(stage.metrics)}</small> : null}{!compact && stage.error ? <small className="vbpl-stage-error">{stage.error}</small> : null}</div>
      {!compact ? <span className="vbpl-stage-detail">{detail}</span> : null}
    </li>;
  })}</ol>;
}

