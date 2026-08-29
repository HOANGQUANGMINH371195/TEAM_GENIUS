"use client";

import Link from "next/link";
import { AuthRoute } from "./auth-route";
import { useAuth } from "../lib/auth-context";

type FeatureKey = "calculator" | "timeline" | "eligibility";

const navigation: Array<{ key: FeatureKey; href: string; label: string; icon: string }> = [
  { key: "calculator", href: "/calculator", label: "So sánh kịch bản", icon: "⇄" },
  { key: "timeline", href: "/timeline", label: "Dòng thời gian pháp lý", icon: "◷" },
  { key: "eligibility", href: "/eligibility", label: "Checklist điều kiện", icon: "✓" },
];

export function FeatureShell({
  active,
  eyebrow,
  title,
  description,
  children,
}: {
  active: FeatureKey;
  eyebrow: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <AuthRoute>
      <div className="bhyt-feature-app">
        <aside className="bhyt-feature-sidebar" aria-label="Công cụ BHYT">
          <Link className="bhyt-feature-brand" href="/">
            <span className="bhyt-feature-brand-mark" aria-hidden="true">✦</span>
            <span><strong>BHYT AI</strong><small>Trợ lý y tế số</small></span>
          </Link>
          <Link className="bhyt-feature-back" href="/">← Tra cứu hội thoại</Link>
          <nav className="bhyt-feature-nav" aria-label="Điều hướng công cụ">
            {navigation.map((item) => (
              <Link key={item.key} className={`bhyt-feature-nav-item${active === item.key ? " is-active" : ""}`} href={item.href} aria-current={active === item.key ? "page" : undefined}>
                <span aria-hidden="true">{item.icon}</span>{item.label}
              </Link>
            ))}
          </nav>
          <div className="bhyt-feature-sidebar-note">
            <strong>Phạm vi an toàn</strong>
            <span>Công cụ chỉ xử lý dữ liệu bạn cung cấp và nguồn pháp lý đã được xác minh.</span>
          </div>
          <AccountFooter />
        </aside>
        <main className="bhyt-feature-main">
          <header className="bhyt-feature-header">
            <div>
              <p className="bhyt-feature-eyebrow">{eyebrow}</p>
              <h1>{title}</h1>
              <p className="bhyt-feature-description">{description}</p>
            </div>
            <Link className="bhyt-feature-chat-link" href="/">Đặt câu hỏi <span aria-hidden="true">↗</span></Link>
          </header>
          <div className="bhyt-feature-content">{children}</div>
        </main>
      </div>
    </AuthRoute>
  );
}

function AccountFooter() {
  const { user, signOut } = useAuth();
  return (
    <div className="bhyt-feature-account">
      <span className="bhyt-feature-avatar" aria-hidden="true">{(user?.displayName || user?.email || "U").slice(0, 1).toUpperCase()}</span>
      <span className="bhyt-feature-account-name">{user?.displayName || user?.email || "Tài khoản"}</span>
      <button type="button" onClick={() => signOut()} aria-label="Đăng xuất">↪</button>
    </div>
  );
}
