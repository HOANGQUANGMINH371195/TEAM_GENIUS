"use client";

import { FormEvent, useState } from "react";

import { ChatCitation, sendChatMessage } from "../lib/api";

const quickQuestions = [
  "Thủ tục chuyển tuyến KCB?",
  "Quyền lợi BHYT 5 năm liên tục?",
  "Tra cứu mã thẻ BHYT",
];

const initialQuestion = "Mức hưởng BHYT khi đi khám trái tuyến năm 2026 như thế nào?";

export default function HomePage() {
  const [question, setQuestion] = useState(initialQuestion);
  const [activeQuestion, setActiveQuestion] = useState(initialQuestion);
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<ChatCitation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = question.trim();
    if (!message || loading) return;

    setActiveQuestion(message);
    setAnswer("");
    setCitations([]);
    setLoading(true);
    setError("");
    try {
      const result = await sendChatMessage(message);
      setAnswer(result.response);
      setCitations(result.citations);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Không thể gửi câu hỏi");
    } finally {
      setLoading(false);
    }
  }

  function chooseQuickQuestion(value: string) {
    setQuestion(value);
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">+</div>
          <div>
            <p className="brand-name">BHYT AI</p>
            <p className="brand-caption">Tra cứu dễ hiểu</p>
          </div>
        </div>

        <nav className="primary-nav" aria-label="Điều hướng chính">
          <a className="nav-item nav-item-active" href="#tra-cuu"><span className="nav-icon">⌕</span>Tra cứu</a>
          <a className="nav-item" href="#thu-tuc"><span className="nav-icon">◌</span>Thủ tục</a>
          <a className="nav-item" href="/admin/review"><span className="nav-icon">⌘</span>Duyệt tri thức</a>
          <a className="nav-item" href="#tro-giup"><span className="nav-icon">?</span>Trợ giúp</a>
        </nav>

        <div className="sidebar-footer">
          <a className="service-link" href="#cong-dich-vu"><span className="service-icon">↗</span>Cổng Dịch Vụ Công</a>
          <div className="profile-row">
            <div className="avatar">M</div>
            <div><p className="profile-name">Minh Hải</p><p className="profile-meta">Tài khoản cá nhân</p></div>
            <button className="ghost-icon" aria-label="Mở tùy chọn tài khoản">···</button>
          </div>
        </div>
      </aside>

      <section className="workspace" id="tra-cuu">
        <header className="topbar">
          <div><p className="eyebrow">Cơ sở dữ liệu quốc gia</p><h1>Trợ lý BHYT</h1></div>
          <div className="topbar-actions">
            <button className="status-pill" type="button"><span className="status-dot" />Đang hiệu lực</button>
            <button className="icon-button" aria-label="Cài đặt">◒</button>
            <button className="icon-button" aria-label="Đăng xuất">↪</button>
          </div>
        </header>

        <div className="content-grid">
          <div className="answer-column">
            <section className="question-block">
              <p className="question-label">Câu hỏi của bạn</p>
              <h2>{activeQuestion}</h2>
              <div className="question-meta"><span>Tra cứu văn bản pháp quy</span><span>•</span><span>GraphRAG</span></div>
            </section>

            <section className="answer-card" aria-live="polite" aria-busy={loading}>
              <div className="answer-card-header">
                <div className="assistant-avatar">✦</div>
                <div><p className="answer-kicker">Bảo Hiểm Y Tế Việt Nam</p><h3>Trợ lý AI</h3></div>
                <span className={`verified-badge${citations.length ? "" : " is-muted"}`}>{citations.length ? "Đã xác minh" : "Chờ nguồn"}</span>
              </div>

              {loading ? <p className="answer-intro answer-loading">Đang tra cứu văn bản và quan hệ pháp lý liên quan...</p> : null}
              {error ? <p className="answer-intro answer-error">{error}</p> : null}
              {!loading && !error && answer ? <p className="answer-intro answer-response">{answer}</p> : null}
              {!loading && !error && !answer ? <p className="answer-intro">Nhập câu hỏi để tra cứu thông tin BHYT và viện phí từ nguồn pháp lý đã lập chỉ mục.</p> : null}

              <div className="answer-footer">
                <p>{citations.length ? `Đã đối chiếu ${citations.length} nguồn evidence từ GraphRAG.` : "Câu trả lời chỉ được hiển thị khi có nguồn phù hợp."}</p>
                {citations.length ? <span className="citation-count">{String(citations.length).padStart(2, "0")} nguồn</span> : null}
              </div>
            </section>

            <section className="follow-up" id="tro-giup">
              <div><p className="eyebrow">Bạn muốn biết thêm?</p><h3>Hỏi tiếp về quyền lợi BHYT</h3></div>
              <div className="quick-links">
                {quickQuestions.map((item) => <button key={item} type="button" onClick={() => chooseQuickQuestion(item)}>{item} <span>→</span></button>)}
              </div>
            </section>
          </div>

          <aside className="reference-panel" id="thu-tuc">
            <div className="panel-heading"><div><p className="eyebrow">Nguồn tham khảo</p><h2>Thư viện pháp lý</h2></div><span className="source-count">{String(citations.length).padStart(2, "0")}</span></div>
            {citations.length ? citations.map((citation) => <CitationCard key={citation.chunk_id} citation={citation} />) : <p className="source-empty">Nguồn trích dẫn sẽ xuất hiện sau khi API trả về evidence phù hợp.</p>}
            <div className="panel-divider" />
            <div className="help-card"><div className="help-icon">✳</div><div><p className="help-title">Cần hỗ trợ trực tuyến?</p><p className="help-copy">Kết nối chuyên viên để được hướng dẫn thủ tục.</p><button className="help-link" type="button">Mở hỗ trợ →</button></div></div>
          </aside>
        </div>

        <form className="composer" onSubmit={submitQuestion}>
          <span className="composer-icon">✦</span>
          <input aria-label="Nhập câu hỏi về BHYT" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Nhập câu hỏi về BHYT..." disabled={loading} />
          <button type="submit" aria-label="Gửi câu hỏi" disabled={loading || !question.trim()}>{loading ? "Đang tra cứu" : "Gửi câu hỏi"} <span>↗</span></button>
        </form>
      </section>
    </main>
  );
}

function CitationCard({ citation }: { citation: ChatCitation }) {
  return <article className="source-card source-card-active"><div className="source-card-topline"><span className="source-type">Evidence</span><span className="source-arrow">↗</span></div><h3>{citation.title || citation.document_id}</h3>{citation.section_title ? <p className="source-section">{citation.section_title}</p> : null}<p>{citation.quote}</p><div className="source-tags"><span>{citation.chunk_id}</span>{citation.channels.map((channel) => <span key={channel}>{channel}</span>)}</div></article>;
}
