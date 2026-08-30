"use client";

import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";
import { useAuth } from "../lib/auth-context";

export type BhytSidebarItem = "chat" | "calculator" | "timeline" | "eligibility";
type SidebarIconName = "chat" | "check" | "chevron" | "document" | "history" | "logout" | "menu" | "shield" | "spark" | "user";

type BhytSidebarProps = {
  active: BhytSidebarItem;
  collapsed: boolean;
  mobileMenuOpen: boolean;
  onToggle: () => void;
  onCloseMobile: () => void;
};

export function BhytSidebar({ active, collapsed, mobileMenuOpen, onToggle, onCloseMobile }: BhytSidebarProps) {
  const { user, signOut } = useAuth();

  return (
    <aside className={`bhyt-sidebar${mobileMenuOpen ? " is-mobile-open" : ""}`} aria-label="Điều hướng chính">
      <div className="bhyt-brand-row">
        <a className="bhyt-brand" href={active === "chat" ? "#main-chat" : "/"} aria-label="MediPay AI - Trang tra cứu">
          <span className="bhyt-brand-symbol" aria-hidden="true"><BhytSidebarIcon name="spark" /></span>
          <span className="bhyt-brand-copy"><strong>MediPay AI</strong><small>Trợ lý y tế số</small></span>
        </a>
        <button className="bhyt-collapse-button" type="button" aria-label={collapsed ? "Mở rộng menu" : "Thu gọn menu"} aria-expanded={!collapsed} onClick={onToggle}><BhytSidebarIcon name="chevron" /></button>
      </div>

      <nav className="bhyt-nav" onClick={onCloseMobile}>
        <a className={`bhyt-nav-item${active === "chat" ? " is-active" : ""}`} href={active === "chat" ? "#main-chat" : "/"} aria-current={active === "chat" ? "page" : undefined}><BhytSidebarIcon name="chat" /><span>Tra cứu BHYT</span></a>
        <Link className={`bhyt-nav-item${active === "calculator" ? " is-active" : ""}`} href="/calculator" aria-current={active === "calculator" ? "page" : undefined}><BhytSidebarIcon name="document" /><span>So sánh kịch bản</span></Link>
        <Link className={`bhyt-nav-item${active === "timeline" ? " is-active" : ""}`} href="/timeline" aria-current={active === "timeline" ? "page" : undefined}><BhytSidebarIcon name="history" /><span>Dòng thời gian pháp lý</span></Link>
        <Link className={`bhyt-nav-item${active === "eligibility" ? " is-active" : ""}`} href="/eligibility" aria-current={active === "eligibility" ? "page" : undefined}><BhytSidebarIcon name="check" /><span>Checklist điều kiện</span></Link>
      </nav>

      <div className="bhyt-sidebar-footer">
        {user ? (
          <div className="bhyt-user-info">
            {user.photoURL ? <Image src={user.photoURL} alt="" width={32} height={32} unoptimized className="bhyt-user-avatar" /> : <BhytSidebarIcon name="user" />}
            <span><strong>{user.displayName || user.email}</strong><small>{user.role === "admin" ? "Quản trị viên" : "Người dùng"}</small></span>
            <button type="button" className="bhyt-logout-btn" onClick={() => signOut()} title="Đăng xuất"><BhytSidebarIcon name="logout" /></button>
          </div>
        ) : null}
        <div className="bhyt-support-note"><BhytSidebarIcon name="shield" /><span><strong>Hỗ trợ tra cứu BHYT</strong><small>Thông tin được đối chiếu từ nguồn pháp lý</small></span></div>
      </div>
    </aside>
  );
}

export function BhytSidebarIcon({ name }: { name: SidebarIconName }) {
  const paths: Record<SidebarIconName, ReactNode> = {
    chat: <><path d="M21 12a8 8 0 0 1-8 8H6l-4 2 1.5-4A9 9 0 1 1 21 12Z" /><path d="M8 12h.01M12 12h.01M16 12h.01" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    chevron: <path d="m9 18 6-6-6-6" />,
    document: <><path d="M6 2h8l4 4v16H6z" /><path d="M14 2v5h5M9 12h6M9 16h6" /></>,
    history: <><path d="M3 12a9 9 0 1 0 3-6.7L3 8" /><path d="M3 3v5h5M12 7v5l3 2" /></>,
    logout: <><path d="M10 5H5v14h5M14 8l4 4-4 4M8 12h10" /></>,
    menu: <><path d="M4 7h16M4 12h16M4 17h16" /></>,
    shield: <><path d="M12 2 20 5v6c0 5-3.4 9-8 11-4.6-2-8-6-8-11V5z" /><path d="m9 12 2 2 4-5" /></>,
    spark: <><path d="M12 2c.6 5 2 7.4 7 8-5 .6-6.4 3-7 8-.6-5-2-7.4-7-8 5-.6 6.4-3 7-8Z" /><path d="M19 16c.2 1.7.8 2.8 2.5 3-1.7.2-2.3 1.3-2.5 3-.2-1.7-.8-2.8-2.5-3 1.7-.2 2.3-1.3 2.5-3Z" /></>,
    user: <><circle cx="12" cy="8" r="3" /><path d="M5 21a7 7 0 0 1 14 0" /></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
