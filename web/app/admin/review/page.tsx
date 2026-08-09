"use client";

import { useMemo, useState } from "react";
import { reviewFixtures } from "../../../lib/review-mock-data";
import { ReviewDetail as ReviewDetailType, ReviewDomain, ReviewStatus } from "../../../lib/review-types";
import { ReviewDetail } from "../../../components/admin/review-detail";
import { ReviewQueue } from "../../../components/admin/review-queue";

export default function AdminReviewPage() {
  const [reviews, setReviews] = useState<ReviewDetailType[]>(reviewFixtures);
  const [selectedId, setSelectedId] = useState(reviewFixtures[0].id);
  const [domainFilter, setDomainFilter] = useState<"all" | ReviewDomain>("all");
  const [statusFilter, setStatusFilter] = useState<"all" | ReviewStatus>("all");
  const [notice, setNotice] = useState("");

  const filteredReviews = useMemo(() => reviews.filter((review) => (domainFilter === "all" || review.domain === domainFilter) && (statusFilter === "all" || review.status === statusFilter)), [reviews, domainFilter, statusFilter]);
  const selectedReview = reviews.find((review) => review.id === selectedId) ?? filteredReviews[0];

  function decide(status: "accepted" | "rejected", note?: string) {
    if (!selectedReview) return;
    setReviews((current) => current.map((review) => review.id === selectedReview.id ? {
      ...review,
      status,
      audit: [...review.audit, { id: `${review.id}-${status}`, action: status, actor: "Minh Hải · Admin", at: "vừa xong", note }],
    } : review));
    setNotice(status === "accepted" ? "Đã chấp nhận trên bản mô phỏng." : "Đã từ chối trên bản mô phỏng.");
  }

  return <main className="admin-review-page">
    <div className="admin-mock-banner"><span className="admin-banner-mark">⌁</span><div><strong>Mô phỏng review</strong><span>Accept / reject chỉ đổi trạng thái trên màn hình — chưa ghi vào GraphRAG.</span></div><span className="admin-banner-code">LOCAL DATASET · v0.1</span></div>
    <div className="admin-review-layout">
      <ReviewQueue reviews={filteredReviews} selectedId={selectedReview?.id ?? ""} domainFilter={domainFilter} statusFilter={statusFilter} onSelect={setSelectedId} onDomainFilter={setDomainFilter} onStatusFilter={setStatusFilter} />
      {selectedReview ? <ReviewDetail review={selectedReview} onDecision={decide} /> : <div className="admin-empty-detail"><span className="admin-empty-icon">◌</span><h1>Không có thay đổi cần xem</h1><p>Thử đổi bộ lọc để xem lại các review khác.</p></div>}
    </div>
    {notice ? <div className="admin-toast" role="status">✓ {notice}</div> : null}
  </main>;
}
