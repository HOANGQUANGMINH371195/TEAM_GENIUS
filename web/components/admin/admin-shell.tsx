"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useAuth } from "../../lib/auth-context";

type AdminIconName = "archive" | "chart" | "chevron" | "logout" | "shield" | "user";

const dashboardPath = "/admin";
const vbplPath = "/admin/vbpl";

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, signOut } = useAuth();

  function handleLogout() {
    signOut();
    router.replace("/login");
  }

  const isVbpl = pathname.startsWith(vbplPath);

  return (
    <div className="admin-portal">
      <aside className="admin-shell-sidebar" aria-label="Điều hướng quản trị">
        <Link className="admin-shell-brand" href={dashboardPath} aria-label="MediPay AI - Khu vực quản trị">
          <span className="admin-shell-brand-mark"><AdminIcon name="shield" /></span>
          <span><strong>MediPay AI</strong><small>Quản trị hệ thống</small></span>
        </Link>

        <nav className="admin-shell-nav">
          <p>Không gian làm việc</p>
          <Link className={`admin-shell-nav-item${pathname === dashboardPath ? " is-active" : ""}`} href={dashboardPath}>
            <AdminIcon name="chart" />
            <span><strong>Giám sát hệ thống</strong><small>Observability Dashboard</small></span>
          </Link>
          <Link className={`admin-shell-nav-item${isVbpl ? " is-active" : ""}`} href={vbplPath}>
            <AdminIcon name="archive" />
            <span><strong>Kho văn bản nguồn</strong><small>VBPL Discovery</small></span>
          </Link>
        </nav>

        <div className="admin-shell-sidebar-footer">
          <div className="admin-shell-security"><AdminIcon name="shield" /><span><strong>Phiên quản trị nội bộ</strong><small>Firebase authorization</small></span></div>
          <button type="button" onClick={handleLogout}><AdminIcon name="logout" /><span>Đăng xuất</span></button>
        </div>
      </aside>

      <div className="admin-shell-main">
        <header className="admin-shell-header">
          <nav className="admin-shell-breadcrumb" aria-label="Breadcrumb">
            <span>Admin</span><AdminIcon name="chevron" /><span>{isVbpl ? "Kho văn bản nguồn" : "Giám sát hệ thống"}</span><AdminIcon name="chevron" /><strong>{isVbpl ? "VBPL Discovery" : "Observability Dashboard"}</strong>
          </nav>
          <div className="admin-shell-header-actions">
            <div className="admin-profile"><span><AdminIcon name="user" /></span><div><strong>{user?.displayName || "Quản trị viên"}</strong><small>{user?.email || "Admin"}</small></div></div>
            <button className="admin-header-logout" type="button" onClick={handleLogout}><AdminIcon name="logout" /><span>Đăng xuất</span></button>
          </div>
        </header>
        <div className="admin-shell-content">{children}</div>
      </div>
    </div>
  );
}

function AdminIcon({ name }: { name: AdminIconName }) {
  const paths: Record<AdminIconName, ReactNode> = {
    archive: <><path d="M4 7h16v13H4z" /><path d="M3 4h18v3H3zM9 11h6" /></>,
    chart: <><path d="M5 19V9M12 19V5M19 19v-7" /><path d="M3 19h18" /></>,
    chevron: <path d="m9 18 6-6-6-6" />,
    logout: <><path d="M10 5H5v14h5M14 8l4 4-4 4M8 12h10" /></>,
    shield: <><path d="M12 2 20 5v6c0 5-3.4 9-8 11-4.6-2-8-6-8-11V5z" /><path d="m9 12 2 2 4-5" /></>,
    user: <><circle cx="12" cy="8" r="3" /><path d="M5 21a7 7 0 0 1 14 0" /></>,
  };
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>;
}
