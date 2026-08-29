"use client";

import { useCallback, useState } from "react";
import { VbplDiscovery } from "../../../components/admin/vbpl-discovery";
import { VbplIngestModal } from "../../../components/admin/vbpl-ingest-modal";
import type { VbplIngestJob } from "../../../lib/api";
import "./vbpl.css";

export default function VbplPage() {
  const [ingestDocIds, setIngestDocIds] = useState<string[] | null>(null);
  const [ingestJob, setIngestJob] = useState<VbplIngestJob | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const closeModal = useCallback(() => {
    // Closing is presentation-only. Keep job state so reopening never cancels work.
    setIngestDocIds(null);
  }, []);

  const openIngest = useCallback((ids: string[]) => {
    // New selection starts a new job; completed job remains available only through reopen action.
    setIngestJob(null);
    setIngestDocIds(ids);
  }, []);

  const handleJobChange = useCallback((job: VbplIngestJob) => {
    setIngestJob(job);
    if (job.status === "succeeded" || job.status === "partial") setReloadToken((token) => token + 1);
  }, []);

  return <main className="vbpl-page">
    <VbplDiscovery onIngest={openIngest} reloadToken={reloadToken} />
    {ingestDocIds ? <VbplIngestModal docIds={ingestDocIds} job={ingestJob} onJobChange={handleJobChange} onClose={closeModal} /> : null}
    {ingestJob ? <button className="vbpl-reopen-button" type="button" onClick={() => setIngestDocIds(ingestJob.items.map((item) => item.doc_id))} aria-label="Mở lại tiến trình nạp dữ liệu">{ingestJob.status === "succeeded" || ingestJob.status === "partial" || ingestJob.status === "failed" ? "Xem kết quả nạp" : "Mở tiến trình nạp"}<span aria-hidden="true">↗</span></button> : null}
  </main>;
}
