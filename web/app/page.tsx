"use client";

import { FormEvent, forwardRef, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import Link from "next/link";
import { useGSAP } from "@gsap/react";
import gsap from "gsap";

import { ChatCitation, fetchDocumentHtml, sendChatMessageStream } from "../lib/api";
import { AuthRoute } from "../components/auth-route";
import { BhytSidebar } from "../components/bhyt-sidebar";

type ChatMessage = { role: "user" | "assistant"; content: string; id: string; citations?: ChatCitation[] };
type DocumentPreview = { citation: ChatCitation; html: string };
type IconName = "arrow" | "book" | "check" | "chevron" | "close" | "document" | "menu" | "new" | "search" | "shield" | "spark" | "stop";

const topicCards = [
  {
    eyebrow: "Quyền lợi dài hạn",
    title: "BHYT 5 năm liên tục",
    description: "Điều kiện và quyền lợi khi cùng chi trả vượt mức quy định.",
    question: "Quyền lợi BHYT 5 năm liên tục được tính như thế nào?",
    icon: "shield" as const,
    tone: "mint",
  },
  {
    eyebrow: "Mức đóng",
    title: "Đóng bao nhiêu, ai hỗ trợ?",
    description: "Tra cứu mức đóng theo nhóm hộ gia đình, học sinh và người lao động.",
    question: "Mức đóng BHYT hiện nay và mức hỗ trợ theo từng nhóm là bao nhiêu?",
    icon: "document" as const,
    tone: "sky",
  },
  {
    eyebrow: "Thủ tục khám chữa bệnh",
    title: "Chuyển tuyến đúng quy trình",
    description: "Hồ sơ cần có, thời hạn giấy chuyển tuyến và nơi tiếp nhận.",
    question: "Thủ tục chuyển tuyến khám chữa bệnh BHYT gồm những gì?",
    icon: "arrow" as const,
    tone: "blue",
  },
  {
    eyebrow: "Phạm vi hưởng",
    title: "Khám trái tuyến",
    description: "Hiểu mức hưởng nội trú, ngoại trú theo từng tuyến điều trị.",
    question: "Mức hưởng BHYT khi đi khám trái tuyến năm 2026 như thế nào?",
    icon: "book" as const,
    tone: "amber",
  },
];

function splitAnswer(value: string) {
  const lines = value.trim().split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const isDebugLine = (line: string) => {
    const normalized = line.replace(/[*_`]/g, "").trim();
    return /^(?:nguồn(?:\s+trích dẫn)?|sources?|citations?)\s*:/i.test(normalized)
      || /^evidence\s+(?:hiện\s+có|available)\b/i.test(normalized)
      || /^(?:căn cứ pháp lý|nguồn tham khảo|nguồn trích dẫn):?$/i.test(normalized);
  };
  const cleanLine = (line: string) => line
    .replace(/^#{1,4}\s+/, "")
    .replace(/^(?:[-*•]|\d+[.)])\s+/, "")
    .replace(/^\*\*(.+)\*\*:?$/, "$1")
    .trim();
  const contentLines = lines.map(cleanLine).filter((line) => line && !isDebugLine(line));
  return {
    summary: contentLines[0] ?? "",
    details: contentLines.slice(1).filter((line) => !/^(?:nội dung|chi tiết|điều kiện|quy định):?$/i.test(line)),
  };
}

function formatInlineMarkdown(value: string) {
  return value.split(/(\*\*[^*]+\*\*)/g).map((part, index) => part.startsWith("**") && part.endsWith("**") ? <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong> : part);
}

function isComparisonQuestion(value: string) {
  return /\b(?:so sánh|đối chiếu|khác nhau|phương án|kịch bản)\b|\bvs\.?\b/i.test(value);
}

export default function HomePage() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [streamStage, setStreamStage] = useState("started");
  const [error, setError] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [expandedCitationIds, setExpandedCitationIds] = useState<string[]>([]);
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null);
  const [documentPreview, setDocumentPreview] = useState<DocumentPreview | null>(null);
  const [documentLoading, setDocumentLoading] = useState(false);
  const [documentError, setDocumentError] = useState("");
  const streamRef = useRef<HTMLDivElement>(null);
  const welcomeRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const drawerCloseRef = useRef<HTMLButtonElement>(null);
  const drawerReturnFocusRef = useRef<HTMLElement | null>(null);
  const documentModalRef = useRef<HTMLElement>(null);
  const documentCloseRef = useRef<HTMLButtonElement>(null);
  const documentReturnFocusRef = useRef<HTMLElement | null>(null);
  const documentAbortRef = useRef<AbortController | null>(null);
  const streamAbortRef = useRef<AbortController | null>(null);
  const conversationIdRef = useRef(crypto.randomUUID());

  useEffect(() => {
    const candidate = new URLSearchParams(window.location.search).get("conversation_id") ?? "";
    if (/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(candidate)) {
      conversationIdRef.current = candidate;
    }
  }, []);

  const activeMessage = messages.find((message) => message.id === activeMessageId && message.role === "assistant");
  const activeEvidence = (activeMessage?.citations ?? []).map((citation, index) => ({ ...citation, evidenceId: `${activeMessage?.id ?? "active"}-${index}` }));
  const documentPreviewOpen = documentPreview !== null;

  useGSAP(() => {
    if (!welcomeRef.current || messages.length || typeof window === "undefined") return;
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      const intro = gsap.timeline();
      intro.from(".bhyt-hero-copy > *", { opacity: 0, y: 24, duration: 0.72, stagger: 0.08, ease: "power3.out", clearProps: "opacity,transform" });
      gsap.fromTo(".bhyt-hero-visual", { opacity: 0.45, scale: 0.84 }, {
        opacity: 1,
        scale: 1,
        duration: .9,
        ease: "power3.out",
        clearProps: "opacity,transform",
      });
      intro.fromTo(".bhyt-topic-card", { opacity: 0, y: 32, scale: 0.97 }, {
        opacity: 1,
        y: 0,
        scale: 1,
        duration: .58,
        stagger: .07,
        ease: "power3.out",
        clearProps: "opacity,transform",
      }, "-=.2");
    });
    return () => media.revert();
  }, { scope: welcomeRef, dependencies: [messages.length], revertOnUpdate: true });

  useEffect(() => {
    streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (!evidenceOpen) return;
    const previousOverflow = document.body.style.overflow;
    drawerReturnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.style.overflow = "hidden";
    drawerCloseRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setEvidenceOpen(false);
      if (event.key !== "Tab") return;
      const focusable = drawerRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])');
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
      drawerReturnFocusRef.current?.focus();
    };
  }, [evidenceOpen]);

  useEffect(() => {
    if (!documentPreviewOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    documentCloseRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        documentAbortRef.current?.abort();
        documentAbortRef.current = null;
        setDocumentPreview(null);
        setDocumentLoading(false);
        setDocumentError("");
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = documentModalRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])');
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
      documentReturnFocusRef.current?.focus();
    };
  }, [documentPreviewOpen]);

  function createNewChat() {
    if (loading) return;
    streamAbortRef.current?.abort();
    conversationIdRef.current = crypto.randomUUID();
    setMessages([]);
    setQuestion("");
    setError("");
    setActiveMessageId(null);
    setExpandedCitationIds([]);
    setEvidenceOpen(false);
    closeDocumentPreview();
  }

  function chooseTopic(value: string) {
    setQuestion(value);
    window.setTimeout(() => inputRef.current?.focus(), 40);
  }

  function toggleEvidenceDrawer(message: ChatMessage) {
    if (evidenceOpen && activeMessageId === message.id) {
      setEvidenceOpen(false);
      return;
    }
    setActiveMessageId(message.id);
    setExpandedCitationIds([]);
    setEvidenceOpen(true);
    if (evidenceOpen) window.setTimeout(() => drawerCloseRef.current?.focus(), 0);
  }

  function openDocumentPreview(citation: ChatCitation) {
    const documentNumber = citation.document_number?.trim();
    if (!documentNumber) return;
    documentAbortRef.current?.abort();
    const controller = new AbortController();
    documentAbortRef.current = controller;
    documentReturnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setEvidenceOpen(false);
    setDocumentPreview({ citation, html: "" });
    setDocumentLoading(true);
    setDocumentError("");
    void fetchDocumentHtml(documentNumber, controller.signal)
      .then((html) => {
        if (controller.signal.aborted || documentAbortRef.current !== controller) return;
        if (!html.trim()) throw new Error("Văn bản không có nội dung để hiển thị");
        setDocumentPreview({ citation, html });
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted || documentAbortRef.current !== controller) return;
        setDocumentError(reason instanceof Error ? reason.message : "Không thể tải văn bản");
      })
      .finally(() => {
        if (documentAbortRef.current === controller) {
          documentAbortRef.current = null;
          setDocumentLoading(false);
        }
      });
  }

  function closeDocumentPreview() {
    documentAbortRef.current?.abort();
    documentAbortRef.current = null;
    setDocumentPreview(null);
    setDocumentLoading(false);
    setDocumentError("");
  }

  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = question.trim();
    if (!message || loading) return;
    const userMessage: ChatMessage = { id: `user-${Date.now()}`, role: "user", content: message };
    setMessages((current) => [...current, userMessage]);
    setQuestion("");
    setError("");
    setLoading(true);
    setStreamStage("started");
    setActiveMessageId(null);
    setExpandedCitationIds([]);
    setEvidenceOpen(false);
    const controller = new AbortController();
    streamAbortRef.current = controller;
    try {
      const result = await sendChatMessageStream(
        message,
        (streamEvent) => {
          if (streamEvent.type === "status") setStreamStage(streamEvent.stage);
          if (streamEvent.type === "final") setStreamStage("verified");
        },
        {
          conversationId: conversationIdRef.current,
          turnId: crypto.randomUUID(),
        },
        controller.signal,
      );
      setMessages((current) => [...current, { id: `assistant-${Date.now()}`, role: "assistant", content: result.response, citations: result.citations }]);
    } catch (requestError) {
      if (!(requestError instanceof DOMException && requestError.name === "AbortError")) {
        setError(requestError instanceof Error ? requestError.message : "Không thể gửi câu hỏi");
      }
    } finally {
      streamAbortRef.current = null;
      setLoading(false);
    }
  }

  function cancelRequest() {
    streamAbortRef.current?.abort();
    setStreamStage("cancelled");
  }

  return (
    <AuthRoute>
    <main className={`bhyt-app${sidebarCollapsed ? " is-sidebar-collapsed" : ""}`}>
      <button className={`bhyt-mobile-backdrop${mobileMenuOpen ? " is-visible" : ""}`} type="button" aria-label="Đóng menu" tabIndex={mobileMenuOpen ? 0 : -1} onClick={() => setMobileMenuOpen(false)} />

      <BhytSidebar active="chat" collapsed={sidebarCollapsed} mobileMenuOpen={mobileMenuOpen} onToggle={() => setSidebarCollapsed((current) => !current)} onCloseMobile={() => setMobileMenuOpen(false)} />

      <section className="bhyt-workspace" id="main-chat">
        <header className="bhyt-topbar">
          <div className="bhyt-topbar-leading">
            <button className="bhyt-mobile-menu" type="button" aria-label="Mở menu" aria-expanded={mobileMenuOpen} onClick={() => setMobileMenuOpen(true)}><Icon name="menu" /></button>
            <div>
              <p className="bhyt-topbar-kicker">Tra cứu văn bản pháp quy</p>
              <h2>Trợ lý BHYT</h2>
            </div>
          </div>
          <div className="bhyt-topbar-actions">
            <span className="bhyt-live-status"><span />Dữ liệu đang hiệu lực</span>
            <button className="bhyt-source-button" type="button" disabled={!activeEvidence.length} aria-label="Mở nguồn trích dẫn" aria-expanded={evidenceOpen} onClick={() => setEvidenceOpen(true)}><Icon name="document" /><span>{activeEvidence.length ? `${activeEvidence.length} nguồn` : "Nguồn"}</span></button>
            <button className="bhyt-new-chat" type="button" onClick={createNewChat} disabled={loading}><Icon name="new" /><span>Đoạn chat mới</span></button>
          </div>
        </header>

        <div className="bhyt-chat-viewport" ref={streamRef} aria-live="polite">
          {!messages.length && !loading ? (
            <WelcomeScreen ref={welcomeRef} onChooseTopic={chooseTopic} onFocusComposer={() => inputRef.current?.focus()} />
          ) : (
            <div className="bhyt-message-stream">
              {messages.map((message, index) => message.role === "user" ? (
                <div className="bhyt-message-row is-user" key={message.id}>
                  <div className="bhyt-message-meta">Bạn <span>vừa hỏi</span></div>
                  <div className="bhyt-user-bubble">{message.content}</div>
                </div>
              ) : (
                <AssistantMessage key={message.id} message={message} comparisonQuestion={messages[index - 1]?.role === "user" && isComparisonQuestion(messages[index - 1].content) ? messages[index - 1].content : ""} evidenceDrawerOpen={evidenceOpen && activeMessageId === message.id} onEvidenceToggle={toggleEvidenceDrawer} />
              ))}
              {loading ? <LoadingMessage stage={streamStage} onCancel={cancelRequest} /> : null}
              {error ? <div className="bhyt-error" role="alert">{error}</div> : null}
              {!loading && messages.some((message) => message.role === "assistant") ? (
                <section className="bhyt-follow-up" aria-labelledby="follow-up-title">
                  <div><p>Tiếp tục tìm hiểu</p><h3 id="follow-up-title">Một số câu hỏi liên quan</h3></div>
                  <div>{topicCards.slice(0, 3).map((item) => <button key={item.title} type="button" onClick={() => chooseTopic(item.question)}>{item.title}<Icon name="arrow" /></button>)}</div>
                </section>
              ) : null}
            </div>
          )}
        </div>

        <form className="bhyt-composer" onSubmit={submitQuestion}>
          <Icon name="search" />
          <input ref={inputRef} aria-label="Nhập câu hỏi về bảo hiểm y tế" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Hỏi về quyền lợi, mức đóng hoặc thủ tục BHYT..." disabled={loading} />
          <button className={loading ? "bhyt-stop-button" : undefined} type={loading ? "button" : "submit"} aria-label={loading ? "Dừng tra cứu" : "Gửi câu hỏi"} title={loading ? "Dừng tra cứu" : undefined} onClick={loading ? cancelRequest : undefined} disabled={!loading && !question.trim()}>
            {loading ? <Icon name="stop" /> : <>Gửi câu hỏi<Icon name="arrow" /></>}
          </button>
        </form>
      </section>

      <button className={`bhyt-drawer-backdrop${evidenceOpen ? " is-visible" : ""}`} type="button" aria-label="Đóng nguồn trích dẫn" tabIndex={evidenceOpen ? 0 : -1} onClick={() => setEvidenceOpen(false)} />
      <aside ref={drawerRef} className={`bhyt-evidence-drawer${evidenceOpen ? " is-open" : ""}`} id="evidence-drawer" role="dialog" aria-modal="true" aria-hidden={!evidenceOpen} aria-labelledby="evidence-title">
        {evidenceOpen ? <>
          <div className="bhyt-drawer-header">
            <div><p>Nguồn kiểm chứng</p><h2 id="evidence-title">Văn bản liên quan</h2></div>
            <button ref={drawerCloseRef} type="button" aria-label="Đóng nguồn trích dẫn" onClick={() => setEvidenceOpen(false)}><Icon name="close" /></button>
          </div>
          <div className="bhyt-drawer-context"><Icon name="shield" /><p>{activeMessage ? `Có ${activeEvidence.length} nguồn được dùng cho câu trả lời đang chọn.` : "Nguồn trích dẫn sẽ xuất hiện sau khi bạn mở nguồn từ một câu trả lời."}</p></div>
          <div className="bhyt-evidence-list">
            {activeEvidence.length ? activeEvidence.map((citation) => (
              <CitationCard key={citation.evidenceId} citation={citation} expanded={expandedCitationIds.includes(citation.evidenceId)} onToggle={() => setExpandedCitationIds((current) => current.includes(citation.evidenceId) ? current.filter((id) => id !== citation.evidenceId) : [...current, citation.evidenceId])} onOpenDocument={openDocumentPreview} />
            )) : <div className="bhyt-empty-evidence"><Icon name="document" /><p>Chưa có nguồn nào được chọn.</p><span>Mở nguồn ngay dưới câu trả lời của trợ lý để kiểm tra căn cứ.</span></div>}
          </div>
        </> : null}
      </aside>

      <button className={`bhyt-document-modal-backdrop${documentPreviewOpen ? " is-visible" : ""}`} type="button" aria-label="Đóng văn bản nguồn" tabIndex={documentPreviewOpen ? 0 : -1} onClick={closeDocumentPreview} />
      <aside ref={documentModalRef} className={`bhyt-document-modal${documentPreviewOpen ? " is-open" : ""}`} id="document-modal" role="dialog" aria-modal="true" aria-hidden={!documentPreviewOpen} aria-labelledby="document-modal-title">
        {documentPreview ? <>
          <div className="bhyt-document-modal-header">
            <div><p>Văn bản nguồn</p><h2 id="document-modal-title">{documentPreview.citation.title || "Văn bản pháp quy"}</h2><code>{documentPreview.citation.document_number}</code></div>
            <button ref={documentCloseRef} type="button" aria-label="Đóng văn bản nguồn" onClick={closeDocumentPreview}><Icon name="close" /></button>
          </div>
          <div className="bhyt-document-modal-body" aria-busy={documentLoading}>
            {documentLoading ? <div className="bhyt-document-modal-state" role="status"><span className="document-viewer-loader" aria-hidden="true" /><p>Đang tải toàn văn văn bản…</p></div> : null}
            {documentError ? <div className="bhyt-document-modal-state is-error" role="alert"><strong>Không thể tải văn bản</strong><p>{documentError}</p><button type="button" onClick={() => openDocumentPreview(documentPreview.citation)}>Thử tải lại văn bản</button></div> : null}
            {!documentLoading && !documentError && documentPreview.html ? <div className="document-html bhyt-document-html" aria-label="Nội dung toàn văn văn bản" dangerouslySetInnerHTML={{ __html: documentPreview.html }} /> : null}
          </div>
        </> : null}
      </aside>
    </main>
    </AuthRoute>
  );
}

const WelcomeScreen = forwardRef<HTMLDivElement, { onChooseTopic: (question: string) => void; onFocusComposer: () => void }>(function WelcomeScreen({ onChooseTopic, onFocusComposer }, ref) {
  return (
    <div className="bhyt-welcome" ref={ref}>
      <section className="bhyt-hero" aria-labelledby="welcome-title">
        <div className="bhyt-hero-copy">
          <p className="bhyt-hero-kicker">Trợ lý hành chính y tế đáng tin cậy</p>
          <h1 id="welcome-title">Hiểu đúng quyền lợi BHYT, ngay khi bạn cần.</h1>
          <p className="bhyt-hero-description">Hỏi bằng ngôn ngữ tự nhiên, nhận câu trả lời dễ hiểu và kiểm tra lại từng nguồn pháp lý được sử dụng.</p>
          <div className="bhyt-hero-actions">
            <button className="is-primary" type="button" onClick={onFocusComposer}>Đặt câu hỏi ngay <Icon name="arrow" /></button>
            <button className="is-secondary" type="button" onClick={() => document.getElementById("suggested-topics")?.scrollIntoView({ behavior: "smooth" })}>Khám phá chủ đề <Icon name="book" /></button>
          </div>
        </div>
        <div className="bhyt-hero-visual" aria-label="Minh họa hồ sơ bảo hiểm y tế được xác minh">
          <div className="bhyt-visual-orbit orbit-one" />
          <div className="bhyt-visual-orbit orbit-two" />
          <div className="bhyt-medical-card main-card">
            <div className="bhyt-card-heading"><span><Icon name="shield" /></span><div><small>Hồ sơ BHYT</small><strong>Đã đối chiếu quyền lợi</strong></div></div>
            <div className="bhyt-card-lines"><span /><span /><span /></div>
            <div className="bhyt-card-confirm"><span><Icon name="shield" /></span>Nguồn pháp lý đã xác minh</div>
          </div>
          <div className="bhyt-medical-card source-card-mini"><Icon name="document" /><div><small>Văn bản hiệu lực</small><strong>Nguồn chính thống</strong></div></div>
          <div className="bhyt-medical-card care-card"><span className="bhyt-care-mark"><Icon name="spark" /></span><div><strong>Trợ lý BHYT</strong><small>Giải thích theo từng trường hợp</small></div></div>
        </div>
      </section>

      <section className="bhyt-topic-section" id="suggested-topics" aria-labelledby="topics-title">
        <div className="bhyt-section-heading"><div><p>Gợi ý để bắt đầu</p><h2 id="topics-title">Bạn đang quan tâm điều gì?</h2></div><span>Chọn một chủ đề để điền câu hỏi mẫu</span></div>
        <div className="bhyt-topic-grid">
          {topicCards.map((item) => (
            <button className={`bhyt-topic-card is-${item.tone}`} key={item.title} type="button" onClick={() => onChooseTopic(item.question)}>
              <span className="bhyt-topic-icon"><Icon name={item.icon} /></span>
              <span className="bhyt-topic-copy"><small>{item.eyebrow}</small><strong>{item.title}</strong><span>{item.description}</span></span>
              <span className="bhyt-topic-arrow"><Icon name="arrow" /></span>
            </button>
          ))}
        </div>
      </section>

      <div className="bhyt-trust-rail" aria-label="Các đặc điểm của hệ thống">
        <div><span>Nguồn pháp lý có kiểm chứng</span><i /> <span>Dữ liệu theo phiên bản hiệu lực</span><i /> <span>Giải thích bằng ngôn ngữ dễ hiểu</span><i /> <span>Bảo vệ thông tin người dùng</span><i /> <span>Nguồn pháp lý có kiểm chứng</span><i /> <span>Dữ liệu theo phiên bản hiệu lực</span></div>
      </div>
    </div>
  );
});

function AssistantMessage({ message, comparisonQuestion, evidenceDrawerOpen, onEvidenceToggle }: { message: ChatMessage; comparisonQuestion: string; evidenceDrawerOpen: boolean; onEvidenceToggle: (message: ChatMessage) => void }) {
  const parsed = splitAnswer(message.content);
  const messageRef = useRef<HTMLDivElement>(null);

  useGSAP(() => {
    const media = gsap.matchMedia();
    media.add("(prefers-reduced-motion: no-preference)", () => {
      gsap.from(".bhyt-answer-tier", { opacity: 0, y: 22, duration: .55, stagger: .09, ease: "power3.out" });
    });
    return () => media.revert();
  }, { scope: messageRef });

  return (
    <div className="bhyt-message-row is-assistant" ref={messageRef}>
      <span className="bhyt-assistant-avatar" aria-hidden="true"><Icon name="spark" /></span>
      <div className="bhyt-assistant-message">
        <div className="bhyt-message-meta"><strong>Trợ lý BHYT</strong><span>Bảo hiểm y tế Việt Nam</span>{message.citations?.length ? <em><Icon name="shield" />Đã kiểm chứng</em> : null}</div>
        <div className="bhyt-assistant-content">
          <section className="bhyt-answer-tier bhyt-answer-callout" aria-label="Kết luận">
            <div className="bhyt-tier-heading"><span><Icon name="spark" /></span><p>Kết luận</p></div>
            <div>{formatInlineMarkdown(parsed.summary)}</div>
          </section>

          {parsed.details.length ? <section className="bhyt-answer-tier bhyt-rule-section" aria-label="Điều kiện và quy định">
            <div className="bhyt-tier-heading"><span><Icon name="book" /></span><p>Điều kiện và quy định</p></div>
            <ul>{parsed.details.map((detail, index) => <li key={`${detail}-${index}`}><span><Icon name="check" /></span><p>{formatInlineMarkdown(detail)}</p></li>)}</ul>
          </section> : null}

          {message.citations?.length ? <section className="bhyt-answer-tier bhyt-legal-section" aria-label="Căn cứ pháp lý">
            <div className="bhyt-tier-heading"><span><Icon name="document" /></span><p>Căn cứ pháp lý</p></div>
            <button className="bhyt-evidence-summary-button" type="button" aria-expanded={evidenceDrawerOpen} aria-controls="evidence-drawer" onClick={() => onEvidenceToggle(message)}><Icon name="book" /><span><strong>{message.citations.length}</strong> căn cứ pháp lý trích dẫn</span><Icon name="chevron" /></button>
          </section> : null}
          {comparisonQuestion ? <Link className="bhyt-comparison-cta" href={`/calculator?question=${encodeURIComponent(comparisonQuestion)}`}>Mở bảng so sánh từ câu hỏi này <span aria-hidden="true">→</span></Link> : null}
        </div>
      </div>
    </div>
  );
}

function LoadingMessage({ stage, onCancel }: { stage: string; onCancel: () => void }) {
  const labels: Record<string, string> = {
    started: "Đang khởi động truy vấn...",
    retrieve_vectors: "Đang tìm văn bản liên quan...",
    assemble_context: "Đang tổng hợp căn cứ...",
    verify_evidence: "Đang kiểm tra nguồn...",
    generate: "Đang soạn câu trả lời...",
    guardrail: "Đang kiểm tra độ chính xác...",
    verified: "Đã kiểm chứng câu trả lời.",
    cancelled: "Đã hủy truy vấn.",
  };
  return <div className="bhyt-message-row is-assistant"><span className="bhyt-assistant-avatar" aria-hidden="true"><Icon name="spark" /></span><div className="bhyt-assistant-message"><div className="bhyt-message-meta"><strong>Trợ lý BHYT</strong><span>Đang tra cứu</span></div><div className="bhyt-typing"><span /><span /><span /><p>{labels[stage] ?? labels.started}</p><button className="bhyt-stop-button" type="button" aria-label="Dừng tra cứu" title="Dừng tra cứu" onClick={onCancel}><Icon name="stop" /></button></div></div></div>;
}

function CitationCard({ citation, expanded, onToggle, onOpenDocument }: { citation: ChatCitation & { evidenceId: string }; expanded: boolean; onToggle: () => void; onOpenDocument: (citation: ChatCitation) => void }) {
  return (
    <article className={`bhyt-evidence-card${expanded ? " is-expanded" : ""}`}>
      <button type="button" onClick={onToggle} aria-expanded={expanded} aria-controls={`evidence-${citation.evidenceId}`}>
        <span className="bhyt-evidence-icon"><Icon name="document" /></span>
        <span className="bhyt-evidence-copy"><small>{citation.document_number || "Nguồn pháp lý"}</small><strong>{citation.title || "Văn bản nguồn"}</strong><span>{citation.quote}</span></span>
        <span className="bhyt-evidence-chevron"><Icon name="chevron" /></span>
      </button>
      {expanded ? <div className="bhyt-evidence-content" id={`evidence-${citation.evidenceId}`}><p>{citation.quote}</p>{citation.section_title ? <strong>{citation.section_title}</strong> : null}{citation.document_number ? <button className="bhyt-evidence-document-link" type="button" onClick={() => onOpenDocument(citation)} aria-label={`Xem toàn văn ${citation.document_number}`}>Xem toàn văn văn bản <span aria-hidden="true">↗</span></button> : null}{citation.source_url ? <a href={citation.source_url} target="_blank" rel="noreferrer">Mở nguồn chính thức <span aria-hidden="true">↗</span></a> : null}</div> : null}
    </article>
  );
}

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    arrow: <><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></>,
    book: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v16H6.5A2.5 2.5 0 0 0 4 21.5z"/><path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v16h4.5a2.5 2.5 0 0 1 2.5 2.5z"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    close: <><path d="m6 6 12 12"/><path d="M18 6 6 18"/></>,
    document: <><path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 12h6M9 16h6"/></>,
    stop: <rect x="7" y="7" width="10" height="10" rx="1.5" />,
    menu: <><path d="M4 7h16M4 12h16M4 17h16"/></>,
    new: <><path d="M12 5v14M5 12h14"/></>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    shield: <><path d="M12 2 20 5v6c0 5-3.4 9-8 11-4.6-2-8-6-8-11V5z"/><path d="m9 12 2 2 4-5"/></>,
    spark: <><path d="M12 2c.6 5 2 7.4 7 8-5 .6-6.4 3-7 8-.6-5-2-7.4-7-8 5-.6 6.4-3 7-8Z"/><path d="M19 16c.2 1.7.8 2.8 2.5 3-1.7.2-2.3 1.3-2.5 3-.2-1.7-.8-2.8-2.5-3 1.7-.2 2.3-1.3 2.5-3Z"/></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
