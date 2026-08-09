"use client";

import { FormEvent, useState } from "react";

const quickQuestions = [
  "Thủ tục chuyển tuyến KCB?",
  "Quyền lợi BHYT 5 năm liên tục?",
  "Tra cứu mã thẻ BHYT",
];

export default function HomePage() {
  const [question, setQuestion] = useState(
    "Mức hưởng BHYT khi đi khám trái tuyến năm 2026 như thế nào?",
  );
  const [activeQuestion, setActiveQuestion] = useState(question);

  function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (question.trim()) setActiveQuestion(question.trim());
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            +
          </div>
          <div>
            <p className="brand-name">BHYT AI</p>
            <p className="brand-caption">Tra cứu dễ hiểu</p>
          </div>
        </div>

        <nav className="primary-nav" aria-label="Điều hướng chính">
          <a className="nav-item nav-item-active" href="#tra-cuu">
            <span className="nav-icon">⌕</span>
            Tra cứu
          </a>
          <a className="nav-item" href="#thu-tuc">
            <span className="nav-icon">◌</span>
            Thủ tục
          </a>
          <a className="nav-item" href="/admin/review">
            <span className="nav-icon">⌘</span>
            Duyệt tri thức
          </a>
          <a className="nav-item" href="#tro-giup">
            <span className="nav-icon">?</span>
            Trợ giúp
          </a>
        </nav>

        <div className="sidebar-footer">
          <a className="service-link" href="#cong-dich-vu">
            <span className="service-icon">↗</span>
            Cổng Dịch Vụ Công
          </a>
          <div className="profile-row">
            <div className="avatar">M</div>
            <div>
              <p className="profile-name">Minh Hải</p>
              <p className="profile-meta">Tài khoản cá nhân</p>
            </div>
            <button className="ghost-icon" aria-label="Mở tùy chọn tài khoản">
              ···
            </button>
          </div>
        </div>
      </aside>

      <section className="workspace" id="tra-cuu">
        <header className="topbar">
          <div>
            <p className="eyebrow">Cơ sở dữ liệu quốc gia</p>
            <h1>Trợ lý BHYT</h1>
          </div>
          <div className="topbar-actions">
            <button className="status-pill" type="button">
              <span className="status-dot" />
              Đang hiệu lực
            </button>
            <button className="icon-button" aria-label="Cài đặt">
              ◒
            </button>
            <button className="icon-button" aria-label="Đăng xuất">
              ↪
            </button>
          </div>
        </header>

        <div className="content-grid">
          <div className="answer-column">
            <section className="question-block">
              <p className="question-label">Câu hỏi của bạn</p>
              <h2>{activeQuestion}</h2>
              <div className="question-meta">
                <span>Tra cứu văn bản pháp quy</span>
                <span>•</span>
                <span>Cập nhật 06/08/2026</span>
              </div>
            </section>

            <section className="answer-card" aria-live="polite">
              <div className="answer-card-header">
                <div className="assistant-avatar">✦</div>
                <div>
                  <p className="answer-kicker">Bảo Hiểm Y Tế Việt Nam</p>
                  <h3>Trợ lý AI</h3>
                </div>
                <span className="verified-badge">Đã xác minh</span>
              </div>

              <p className="answer-intro">
                Chào bạn, theo quy định hiện hành và lộ trình dự kiến đến năm 2026,
                mức hưởng BHYT khi đi khám chữa bệnh trái tuyến được quy định như sau:
              </p>

              <div className="law-callout">
                <div className="law-icon">§</div>
                <div>
                  <p className="law-title">Nghị định 146/2018/NĐ-CP</p>
                  <p className="law-subtitle">Điều 14 · Mức hưởng khi KCB không đúng tuyến</p>
                </div>
                <button className="text-button" type="button">
                  Mở văn bản ↗
                </button>
              </div>

              <div className="answer-section">
                <p className="section-label">Mức hưởng KCB trái tuyến · Nội trú</p>
                <div className="benefit-grid">
                  <BenefitCard label="Tuyến huyện" value="100%" note="Khám và điều trị" />
                  <BenefitCard label="Tuyến tỉnh" value="100%" note="Từ 01/01/2021" />
                  <BenefitCard label="Tuyến trung ương" value="40%" note="Chi phí nội trú" accent />
                </div>
              </div>

              <div className="notice-box">
                <span className="notice-symbol">!</span>
                <p>
                  Mức hưởng trên áp dụng cho chi phí điều trị nội trú. KCB ngoại trú trái tuyến
                  tỉnh và trung ương hiện chưa được quỹ BHYT chi trả.
                </p>
              </div>

              <div className="answer-footer">
                <p>Trích xuất từ Cơ sở dữ liệu Quốc gia về Văn bản Pháp luật.</p>
                <button className="outline-button" type="button">
                  Xem toàn văn
                </button>
              </div>
            </section>

            <section className="follow-up" id="tro-giup">
              <div>
                <p className="eyebrow">Bạn muốn biết thêm?</p>
                <h3>Hỏi tiếp về quyền lợi BHYT</h3>
              </div>
              <div className="quick-links">
                {quickQuestions.map((item) => (
                  <button key={item} type="button" onClick={() => setQuestion(item)}>
                    {item} <span>→</span>
                  </button>
                ))}
              </div>
            </section>
          </div>

          <aside className="reference-panel" id="thu-tuc">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">Nguồn tham khảo</p>
                <h2>Thư viện pháp lý</h2>
              </div>
              <span className="source-count">03</span>
            </div>

            <article className="source-card source-card-active">
              <div className="source-card-topline">
                <span className="source-type">Văn bản chính</span>
                <span className="source-arrow">↗</span>
              </div>
              <h3>Nghị định 146/2018/NĐ-CP</h3>
              <p>Quy định chi tiết và hướng dẫn biện pháp thi hành một số điều của Luật bảo hiểm y tế.</p>
              <div className="source-tags">
                <span>Điều 14</span>
                <span>Đang hiệu lực</span>
              </div>
            </article>

            <article className="source-card">
              <div className="source-card-topline">
                <span className="source-type">Luật liên quan</span>
                <span className="source-arrow">↗</span>
              </div>
              <h3>Luật BHYT sửa đổi 2014</h3>
              <p>Điều 22 · Mức hưởng bảo hiểm y tế khi khám chữa bệnh không đúng tuyến.</p>
            </article>

            <article className="source-card">
              <div className="source-card-topline">
                <span className="source-type">Hồ sơ cá nhân</span>
                <span className="source-arrow">→</span>
              </div>
              <h3>Lịch sử khám chữa bệnh</h3>
              <p>Xem lại các lần tra cứu và thông tin đã lưu.</p>
            </article>

            <div className="panel-divider" />
            <div className="help-card">
              <div className="help-icon">✳</div>
              <div>
                <p className="help-title">Cần hỗ trợ trực tuyến?</p>
                <p className="help-copy">Kết nối chuyên viên để được hướng dẫn thủ tục.</p>
                <button className="help-link" type="button">Mở hỗ trợ →</button>
              </div>
            </div>
          </aside>
        </div>

        <form className="composer" onSubmit={submitQuestion}>
          <span className="composer-icon">✦</span>
          <input
            aria-label="Nhập câu hỏi về BHYT"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Nhập câu hỏi về BHYT..."
          />
          <button type="submit" aria-label="Gửi câu hỏi">
            Gửi câu hỏi <span>↗</span>
          </button>
        </form>
      </section>
    </main>
  );
}

function BenefitCard({
  label,
  value,
  note,
  accent = false,
}: {
  label: string;
  value: string;
  note: string;
  accent?: boolean;
}) {
  return (
    <div className={`benefit-card${accent ? " benefit-card-accent" : ""}`}>
      <p>{label}</p>
      <strong>{value}</strong>
      <span>{note}</span>
    </div>
  );
}
