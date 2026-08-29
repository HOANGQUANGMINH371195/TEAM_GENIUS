"use client";

import { type FormEvent, useEffect, useState } from "react";
import { compareBenefitScenarios, draftBenefitCalculation, type BenefitCalculationInput, type BenefitCalculationResult, type CalculatorDraftResponse } from "../../lib/api";
import { FeatureShell } from "../../components/feature-shell";

type ScenarioForm = { label: string; coveredCost: string; rate: string; spend: string; threshold: string; years: string };
const initial: ScenarioForm = { label: "Kịch bản cơ bản", coveredCost: "", rate: "", spend: "0", threshold: "", years: "" };

function money(value: string) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(parsed) : value;
}

export default function CalculatorPage() {
  const [scenarios, setScenarios] = useState<ScenarioForm[]>([initial, { ...initial, label: "Kịch bản 5 năm liên tục" }]);
  const [results, setResults] = useState<Array<{ label: string; calculation: BenefitCalculationResult }>>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [question, setQuestion] = useState(() => typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("question") ?? "");
  const [draft, setDraft] = useState<CalculatorDraftResponse | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);

  function update(index: number, field: keyof ScenarioForm, value: string) {
    setScenarios((current) => current.map((scenario, itemIndex) => itemIndex === index ? { ...scenario, [field]: value } : scenario));
  }

  async function loadDraft(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const value = question.trim();
    if (!value || draftLoading) return;
    setDraftLoading(true);
    setError("");
    try {
      setDraft(await draftBenefitCalculation(value));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Không thể lấy dữ liệu nguồn");
    } finally {
      setDraftLoading(false);
    }
  }

  useEffect(() => {
    const initialQuestion = new URLSearchParams(window.location.search).get("question") ?? "";
    if (initialQuestion) void draftBenefitCalculation(initialQuestion).then(setDraft).catch(() => undefined);
  }, []);

  function applyDraftValue(value: string, unit: "percent" | "vnd", scenarioIndex: number) {
    update(scenarioIndex, unit === "percent" ? "rate" : "coveredCost", value);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(""); setResults([]); setLoading(true);
    try {
      const payload = scenarios.map((scenario, index) => {
        const calculation: BenefitCalculationInput = { covered_cost: scenario.coveredCost, base_rate_percent: scenario.rate, copayment_spend: scenario.spend || "0" };
        if (Boolean(scenario.threshold) !== Boolean(scenario.years)) {
          throw new Error(`Kịch bản ${index + 1}: cần nhập cả ngưỡng và số năm tham gia liên tục`);
        }
        if (scenario.threshold && scenario.years) {
          calculation.copayment_threshold = scenario.threshold;
          calculation.continuous_years = scenario.years;
          calculation.required_years = "5";
          calculation.threshold_rate_percent = "100";
        }
        return { label: scenario.label.trim() || `Kịch bản ${index + 1}`, calculation };
      });
      const response = await compareBenefitScenarios(payload);
      setResults(response.results);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Không thể tính các kịch bản");
    } finally { setLoading(false); }
  }

  return (
    <FeatureShell active="calculator" eyebrow="Công cụ tính toán" title="So sánh kịch bản BHYT" description="Đặt các phương án cạnh nhau bằng cùng một công thức, với mức hưởng do bạn xác nhận từ văn bản hiện hành.">
      <div className="bhyt-feature-grid">
        <section className="bhyt-feature-card bhyt-calculator-draft">
          <div className="bhyt-feature-card-heading"><span className="bhyt-feature-step">00</span><div><h2>Bắt đầu từ câu hỏi</h2><p>Dán câu hỏi từ chat để tìm các đoạn luật và con số được nêu rõ trong nguồn.</p></div></div>
          <form className="bhyt-draft-form" onSubmit={loadDraft}>
            <label htmlFor="calculator-question">Câu hỏi cần so sánh</label>
            <div className="bhyt-draft-row"><input id="calculator-question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Ví dụ: So sánh mức hưởng khi đúng tuyến và trái tuyến" /><button className="bhyt-feature-secondary" type="submit" disabled={draftLoading || !question.trim()}>{draftLoading ? "Đang tìm…" : "Lấy dữ liệu nguồn"}</button></div>
          </form>
          {draft ? <div className="bhyt-draft-result"><p className="bhyt-feature-muted">{draft.message}</p>{draft.values.length ? <div className="bhyt-draft-values" aria-label="Giá trị được nêu trong nguồn">{draft.values.map((item, index) => <div className="bhyt-draft-value" key={`${item.value}-${item.unit}-${index}`}><strong>{item.value}{item.unit === "percent" ? "%" : " ₫"}</strong><span>từ nguồn {item.evidence_index + 1}</span><div><button type="button" onClick={() => applyDraftValue(item.value, item.unit, 0)}>Kịch bản 1</button><button type="button" onClick={() => applyDraftValue(item.value, item.unit, 1)}>Kịch bản 2</button></div></div>)}</div> : null}{draft.evidence.length ? <details className="bhyt-draft-evidence"><summary>Xem {draft.evidence.length} đoạn nguồn đã tìm thấy</summary>{draft.evidence.map((item, index) => <blockquote key={`${item.quote}-${index}`}><strong>{item.title || "Nguồn pháp lý"}</strong>{item.section_title ? <span>{item.section_title}</span> : null}<p>{item.quote}</p>{item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer">Mở nguồn chính thức ↗</a> : null}</blockquote>)}</details> : null}</div> : null}
        </section>
        <form className="bhyt-feature-card bhyt-calculator-form" onSubmit={submit}>
          <div className="bhyt-feature-card-heading"><span className="bhyt-feature-step">01</span><div><h2>Thông số kịch bản</h2><p>Nhập số liệu đã được xác minh. Đơn vị tiền là VNĐ.</p></div></div>
          {scenarios.map((scenario, index) => (
            <fieldset className="bhyt-scenario-fieldset" key={`${index}-${scenario.label}`}>
              <legend><span>{index + 1}</span><input aria-label="Tên kịch bản" value={scenario.label} onChange={(event) => update(index, "label", event.target.value)} /></legend>
              <div className="bhyt-form-grid">
                <label>Chi phí trong phạm vi<input required inputMode="decimal" min="0" type="number" step="0.01" value={scenario.coveredCost} onChange={(event) => update(index, "coveredCost", event.target.value)} placeholder="Ví dụ 1.000.000" /></label>
                <label>Mức hưởng cơ bản (%)<input required inputMode="decimal" min="0" max="100" type="number" step="0.01" value={scenario.rate} onChange={(event) => update(index, "rate", event.target.value)} placeholder="Ví dụ 80" /></label>
                <label>Cùng chi trả tích lũy<input inputMode="decimal" min="0" type="number" step="0.01" value={scenario.spend} onChange={(event) => update(index, "spend", event.target.value)} /></label>
                <label>Ngưỡng cùng chi trả<input inputMode="decimal" min="0" type="number" step="0.01" value={scenario.threshold} onChange={(event) => update(index, "threshold", event.target.value)} placeholder="Bỏ trống nếu không áp dụng" /></label>
                <label>Số năm tham gia liên tục<input inputMode="decimal" min="0" type="number" step="0.1" value={scenario.years} onChange={(event) => update(index, "years", event.target.value)} placeholder="Bắt buộc khi có ngưỡng" /></label>
              </div>
            </fieldset>
          ))}
          <button className="bhyt-feature-primary" type="submit" disabled={loading}>{loading ? "Đang tính…" : "So sánh hai kịch bản"}<span aria-hidden="true">→</span></button>
          {error ? <p className="bhyt-feature-error" role="alert">{error}</p> : null}
        </form>
        <section className="bhyt-feature-card bhyt-calculator-results" aria-live="polite">
          <div className="bhyt-feature-card-heading"><span className="bhyt-feature-step">02</span><div><h2>Kết quả đối chiếu</h2><p>Phép tính chính xác, không tự suy đoán mức hưởng.</p></div></div>
          {!results.length ? <div className="bhyt-feature-empty"><span aria-hidden="true">◌</span><p>Nhập số liệu và chạy so sánh để xem số tiền quỹ chi trả, người bệnh cùng chi trả và điều kiện ngưỡng.</p></div> : <div className="bhyt-result-list">{results.map((item) => <ResultCard key={item.label} label={item.label} result={item.calculation} />)}</div>}
          <p className="bhyt-feature-footnote">Kết quả chỉ có giá trị tham khảo theo các thông số đầu vào. Quyền lợi thực tế cần đối chiếu văn bản và hồ sơ cụ thể.</p>
        </section>
      </div>
    </FeatureShell>
  );
}

function ResultCard({ label, result }: { label: string; result: BenefitCalculationResult }) {
  return <article className="bhyt-result-card"><div className="bhyt-result-card-title"><h3>{label}</h3><span className={result.threshold_met ? "is-positive" : "is-neutral"}>{result.threshold_met ? "Đạt ngưỡng" : "Mức cơ bản"}</span></div><div className="bhyt-result-amounts"><div><small>Quỹ BHYT chi trả</small><strong>{money(result.insurer_pays)} <em>₫</em></strong></div><div><small>Người bệnh cùng chi trả</small><strong>{money(result.patient_pays)} <em>₫</em></strong></div></div><dl><div><dt>Mức áp dụng</dt><dd>{result.applied_rate_percent}%</dd></div><div><dt>Chi phí trong phạm vi</dt><dd>{money(result.covered_cost)} ₫</dd></div></dl></article>;
}
