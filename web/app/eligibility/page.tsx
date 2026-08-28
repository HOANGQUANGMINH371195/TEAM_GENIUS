"use client";

import { type FormEvent, useRef, useState } from "react";
import Link from "next/link";

import {
  fetchEligibilityChecklist,
  type EligibilityChecklistResponse,
  type EligibilityTopic,
} from "../../lib/api";

const topics: Array<{ value: EligibilityTopic; label: string }> = [
  { value: "benefit", label: "Xác định thông tin để tra mức hưởng" },
  { value: "five_year", label: "BHYT 5 năm liên tục" },
  { value: "referral", label: "Chuyển cơ sở khám chữa bệnh" },
  { value: "emergency", label: "Điều trị cấp cứu" },
  { value: "student_contribution", label: "Mức đóng của học sinh, sinh viên" },
];

export default function EligibilityPage() {
  const [topic, setTopic] = useState<EligibilityTopic>("benefit");
  const [facts, setFacts] = useState<Record<string, string | boolean>>({});
  const [checklist, setChecklist] = useState<EligibilityChecklistResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const conversationIdRef = useRef(crypto.randomUUID());

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      setChecklist(await fetchEligibilityChecklist(topic, facts, conversationIdRef.current));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Không thể tạo checklist");
    } finally {
      setLoading(false);
    }
  }

  function changeTopic(value: EligibilityTopic) {
    setTopic(value);
    setFacts({});
    setChecklist(null);
  }

  return (
    <main className="document-viewer" aria-live="polite">
      <Link href="/">← Quay lại tra cứu</Link>
      <h1>Checklist điều kiện BHYT</h1>
      <p>Công cụ chỉ thu thập tình tiết có thể làm thay đổi kết quả. Quy định và mức hưởng luôn được truy xuất lại từ văn bản hiện hành.</p>
      <form onSubmit={submit} style={{ display: "grid", gap: "0.75rem", maxWidth: 720 }}>
        <label>
          Nội dung cần kiểm tra
          <select value={topic} onChange={(event) => changeTopic(event.target.value as EligibilityTopic)}>
            {topics.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
        </label>
        {(checklist?.missing ?? []).map((field) => (
          <label key={field.key}>
            {field.label}
            {field.input_type === "boolean" ? (
              <select
                value={String(facts[field.key] ?? "")}
                onChange={(event) => setFacts((current) => ({ ...current, [field.key]: event.target.value === "true" }))}
              >
                <option value="">Chọn…</option>
                <option value="true">Có</option>
                <option value="false">Không</option>
              </select>
            ) : field.input_type === "select" ? (
              <select
                value={String(facts[field.key] ?? "")}
                onChange={(event) => setFacts((current) => ({ ...current, [field.key]: event.target.value }))}
              >
                <option value="">Chọn…</option>
                {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            ) : (
              <input
                type={field.input_type}
                value={String(facts[field.key] ?? "")}
                onChange={(event) => setFacts((current) => ({ ...current, [field.key]: event.target.value }))}
              />
            )}
            <small>{field.reason}</small>
          </label>
        ))}
        <button type="submit" disabled={loading}>{loading ? "Đang kiểm tra…" : checklist ? "Cập nhật checklist" : "Bắt đầu"}</button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
      {checklist?.complete ? (
        <section>
          <h2>Đã đủ tình tiết đầu vào</h2>
          <p>Bước tiếp theo là truy xuất quy định hiện hành và căn cứ pháp lý; checklist này không tự kết luận quyền lợi.</p>
          {checklist.facts_persisted ? (
            <Link href={`/?conversation_id=${encodeURIComponent(checklist.conversation_id)}`}>
              Đặt câu hỏi với các tình tiết vừa xác nhận
            </Link>
          ) : <p>Server chưa áp dụng migration lưu tình tiết; bạn vẫn có thể hỏi nhưng cần nhập lại các thông tin này.</p>}
        </section>
      ) : checklist ? <p>Còn {checklist.missing.length} tình tiết cần xác nhận.</p> : null}
    </main>
  );
}
