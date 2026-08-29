"use client";

import { type FormEvent, useRef, useState } from "react";
import Link from "next/link";
import { fetchEligibilityChecklist, type EligibilityChecklistResponse, type EligibilityChecklistField, type EligibilityTopic } from "../../lib/api";
import { FeatureShell } from "../../components/feature-shell";

const topics: Array<{ value: EligibilityTopic; label: string; description: string }> = [
  { value: "benefit", label: "Xác định mức hưởng", description: "Các tình tiết cần có trước khi truy xuất quy định." },
  { value: "five_year", label: "BHYT 5 năm liên tục", description: "Kiểm tra mốc tham gia và khoản cùng chi trả." },
  { value: "referral", label: "Chuyển cơ sở khám chữa bệnh", description: "Xác định giấy chuyển và tuyến điều trị." },
  { value: "emergency", label: "Điều trị cấp cứu", description: "Tách trường hợp cấp cứu khỏi quy trình thông thường." },
  { value: "student_contribution", label: "Học sinh, sinh viên", description: "Chuẩn bị thông tin năm học và cơ sở giáo dục." },
];

export default function EligibilityPage() {
  const [topic, setTopic] = useState<EligibilityTopic>("benefit");
  const [facts, setFacts] = useState<Record<string, string | boolean>>({});
  const [checklist, setChecklist] = useState<EligibilityChecklistResponse | null>(null);
  const [error, setError] = useState(""); const [loading, setLoading] = useState(false);
  const conversationIdRef = useRef(crypto.randomUUID());
  const selectedTopic = topics.find((item) => item.value === topic) ?? topics[0];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setLoading(true);
    try { setChecklist(await fetchEligibilityChecklist(topic, facts, conversationIdRef.current)); }
    catch (reason: unknown) { setError(reason instanceof Error ? reason.message : "Không thể tạo checklist"); }
    finally { setLoading(false); }
  }
  function changeTopic(value: EligibilityTopic) { setTopic(value); setFacts({}); setChecklist(null); setError(""); }
  function setFact(key: string, value: string | boolean) { setFacts((current) => ({ ...current, [key]: value })); }

  return <FeatureShell active="eligibility" eyebrow="Chuẩn bị hồ sơ" title="Checklist điều kiện BHYT" description="Trả lời đúng các tình tiết có thể làm thay đổi kết quả trước khi đặt câu hỏi pháp lý.">
    <div className="bhyt-feature-card bhyt-topic-selector"><div><p className="bhyt-feature-eyebrow">Bạn muốn kiểm tra điều gì?</p><h2>{selectedTopic.label}</h2><p>{selectedTopic.description}</p></div><select value={topic} onChange={(event) => changeTopic(event.target.value as EligibilityTopic)} aria-label="Chủ đề checklist">{topics.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>
    <div className="bhyt-feature-grid bhyt-checklist-grid"><form className="bhyt-feature-card" onSubmit={submit}><div className="bhyt-feature-card-heading"><span className="bhyt-feature-step">01</span><div><h2>Thông tin tình huống</h2><p>Chỉ hỏi những dữ kiện có thể làm thay đổi quy định áp dụng.</p></div></div>{!checklist ? <div className="bhyt-checklist-start"><span aria-hidden="true">✓</span><p>Bấm bắt đầu để nhận danh sách câu hỏi phù hợp với chủ đề.</p></div> : checklist.missing.length ? <div className="bhyt-checklist-fields">{checklist.missing.map((field) => <FieldInput key={field.key} field={field} value={facts[field.key]} onChange={setFact} />)}</div> : <div className="bhyt-checklist-complete"><span aria-hidden="true">✓</span><h3>Đã đủ thông tin đầu vào</h3><p>Hệ thống có thể truy xuất quy định hiện hành cho tình huống này.</p></div>}<button className="bhyt-feature-primary" type="submit" disabled={loading}>{loading ? "Đang kiểm tra…" : checklist?.missing.length ? "Cập nhật checklist" : checklist?.complete ? "Kiểm tra lại" : "Bắt đầu checklist"}<span aria-hidden="true">→</span></button>{error ? <p className="bhyt-feature-error" role="alert">{error}</p> : null}</form><section className="bhyt-feature-card bhyt-checklist-status" aria-live="polite"><div className="bhyt-feature-card-heading"><span className="bhyt-feature-step">02</span><div><h2>Tiến độ</h2><p>Checklist không tự kết luận quyền lợi.</p></div></div>{!checklist ? <div className="bhyt-feature-empty"><span aria-hidden="true">○</span><p>Các câu trả lời sẽ được giữ trong phiên hội thoại để bạn không phải nhập lại.</p></div> : <><div className="bhyt-progress"><span style={{ width: `${Math.round((checklist.accepted_fact_keys.length / Math.max(checklist.accepted_fact_keys.length + checklist.missing.length, 1)) * 100)}%` }} /></div><p className="bhyt-progress-label"><strong>{checklist.accepted_fact_keys.length}</strong> dữ kiện đã xác nhận · <strong>{checklist.missing.length}</strong> còn thiếu</p>{checklist.next_question ? <p className="bhyt-next-question">Tiếp theo: {checklist.next_question}</p> : null}{checklist.complete ? <div className="bhyt-checklist-cta"><p>Giờ bạn có thể đặt câu hỏi để nhận phân tích kèm căn cứ pháp lý.</p><Link className="bhyt-feature-secondary" href={`/?conversation_id=${encodeURIComponent(checklist.conversation_id)}`}>Đặt câu hỏi với hồ sơ này ↗</Link></div> : null}</>}</section></div>
  </FeatureShell>;
}

function FieldInput({ field, value, onChange }: { field: EligibilityChecklistField; value: string | boolean | undefined; onChange: (key: string, value: string | boolean) => void }) {
  const common = { value: String(value ?? ""), onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => onChange(field.key, field.input_type === "boolean" ? event.target.value === "true" : event.target.value) };
  return <label className="bhyt-checklist-field">{field.label}{field.input_type === "boolean" || field.input_type === "select" ? <select {...common}><option value="">Chọn…</option>{field.input_type === "boolean" ? <><option value="true">Có</option><option value="false">Không</option></> : field.options.map((option) => <option key={option} value={option}>{option}</option>)}</select> : <input {...common} type={field.input_type} /> }<small>{field.reason}</small></label>;
}
