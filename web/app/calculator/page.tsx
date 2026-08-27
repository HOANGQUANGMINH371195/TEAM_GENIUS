"use client";

import { type FormEvent, useState } from "react";
import Link from "next/link";
import { compareBenefitScenarios, type BenefitCalculationInput } from "../../lib/api";

export default function CalculatorPage() {
  const [coveredCost, setCoveredCost] = useState("");
  const [baseRate, setBaseRate] = useState("");
  const [spend, setSpend] = useState("");
  const [threshold, setThreshold] = useState("");
  const [years, setYears] = useState("");
  const [results, setResults] = useState<Array<{ label: string; calculation: Record<string, unknown> }>>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setResults([]);
    const base: BenefitCalculationInput = {
      covered_cost: coveredCost,
      base_rate_percent: baseRate,
      copayment_spend: "0",
    };
    const thresholdScenario: BenefitCalculationInput = {
      ...base,
      copayment_spend: spend,
      copayment_threshold: threshold,
      continuous_years: years,
      required_years: "5",
      threshold_rate_percent: "100",
    };
    setLoading(true);
    try {
      const response = await compareBenefitScenarios([
        { label: "Mức hưởng cơ bản", calculation: base },
        { label: "Kịch bản có ngưỡng liên tục", calculation: thresholdScenario },
      ]);
      setResults(response.results);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Không thể tính kịch bản");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="document-viewer" aria-live="polite">
      <Link href="/">← Quay lại tra cứu</Link>
      <h1>So sánh kịch bản BHYT</h1>
      <p>Nhập các giá trị đã được xác nhận từ nguồn pháp lý. Công cụ chỉ làm phép tính, không tự chọn mức hưởng.</p>
      <form onSubmit={submit} style={{ display: "grid", gap: "0.75rem", maxWidth: 640 }}>
        <label>Chi phí trong phạm vi: <input required value={coveredCost} onChange={(event) => setCoveredCost(event.target.value)} /></label>
        <label>Mức hưởng cơ bản (%): <input required value={baseRate} onChange={(event) => setBaseRate(event.target.value)} /></label>
        <fieldset>
          <legend>Thông tin kịch bản ngưỡng (tuỳ chọn theo hồ sơ)</legend>
          <label>Cùng chi trả tích lũy: <input required value={spend} onChange={(event) => setSpend(event.target.value)} /></label>
          <label>Ngưỡng cùng chi trả: <input required value={threshold} onChange={(event) => setThreshold(event.target.value)} /></label>
          <label>Số năm liên tục: <input required value={years} onChange={(event) => setYears(event.target.value)} /></label>
        </fieldset>
        <button type="submit" disabled={loading}>{loading ? "Đang tính…" : "So sánh"}</button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
      {results.length ? (
        <section aria-label="Kết quả kịch bản">
          {results.map((item) => (
            <article key={item.label}>
              <h2>{item.label}</h2>
              <pre>{JSON.stringify(item.calculation, null, 2)}</pre>
            </article>
          ))}
        </section>
      ) : null}
    </main>
  );
}
