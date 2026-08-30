"use client";

import { useState } from "react";
import Link from "next/link";
import { AuthRoute } from "./auth-route";
import { BhytSidebar, BhytSidebarIcon, type BhytSidebarItem } from "./bhyt-sidebar";

export function FeatureShell({
  active,
  eyebrow,
  title,
  description,
  children,
}: {
  active: Exclude<BhytSidebarItem, "chat">;
  eyebrow: string;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  return (
    <AuthRoute>
      <main className={`bhyt-app bhyt-feature-app${sidebarCollapsed ? " is-sidebar-collapsed" : ""}`}>
        <button className={`bhyt-mobile-backdrop${mobileMenuOpen ? " is-visible" : ""}`} type="button" aria-label="Đóng menu" tabIndex={mobileMenuOpen ? 0 : -1} onClick={() => setMobileMenuOpen(false)} />
        <BhytSidebar active={active} collapsed={sidebarCollapsed} mobileMenuOpen={mobileMenuOpen} onToggle={() => setSidebarCollapsed((current) => !current)} onCloseMobile={() => setMobileMenuOpen(false)} />
        <section className="bhyt-feature-main" aria-labelledby="bhyt-feature-title">
          <header className="bhyt-feature-header">
            <div className="bhyt-feature-header-leading">
              <button className="bhyt-mobile-menu" type="button" aria-label="Mở menu" aria-expanded={mobileMenuOpen} onClick={() => setMobileMenuOpen(true)}><BhytSidebarIcon name="menu" /></button>
              <div>
                <p className="bhyt-feature-eyebrow">{eyebrow}</p>
                <h1 id="bhyt-feature-title">{title}</h1>
                <p className="bhyt-feature-description">{description}</p>
              </div>
            </div>
            <Link className="bhyt-feature-chat-link" href="/">Đặt câu hỏi <span aria-hidden="true">↗</span></Link>
          </header>
          <div className="bhyt-feature-content">{children}</div>
        </section>
      </main>
    </AuthRoute>
  );
}
