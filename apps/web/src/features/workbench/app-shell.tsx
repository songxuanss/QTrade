"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Icon } from "../../components/ui/icon";
import { ROLE_NAMES, type PublicWorkspace } from "../auth/public";
import { navigation } from "./navigation";
import { CommandPalette } from "./command-palette";
import type { WorkspaceChoice } from "./workspaces-server";
import { selectWorkspace } from "../../app/workspace/workspace-actions";

export function AppShell({
  children,
  authenticated = false,
  workspace = null,
  workspaces = [],
  selectedWorkspaceId = "",
}: {
  children: ReactNode;
  authenticated?: boolean;
  workspace?: PublicWorkspace | null;
  workspaces?: WorkspaceChoice[];
  selectedWorkspaceId?: string;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [logoutError, setLogoutError] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  async function logout() {
    setLoggingOut(true);
    setLogoutError(false);
    try {
      const session = await fetch("/auth/session", { cache: "no-store" });
      if (!session.ok) throw Error("Session unavailable");
      const context = await session.json();
      const response = await fetch("/auth/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": context.csrf_token },
      });
      if (response.status !== 204) throw Error("Logout failed");
      router.replace("/");
      router.refresh();
      setLoggingOut(false);
    } catch {
      setLogoutError(true);
      setLoggingOut(false);
    }
  }
  const current =
    navigation.find((item) => item.href === pathname)?.label ?? "工作台";
  useEffect(() => {
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuOpen(false);
    }
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        跳转到主内容
      </a>
      <aside
        className={`sidebar ${menuOpen ? "sidebar-open" : ""}`}
        id="workspace-navigation"
      >
        <Link
          href="/"
          className="brand"
          aria-label="QTrade 工作台"
          onClick={() => setMenuOpen(false)}
        >
          <span className="brand-symbol">
            Q<span />
          </span>
          <span>
            QTrade<span className="brand-subtitle">RESEARCH & EXECUTION</span>
          </span>
        </Link>
        <div className="workspace-switch">
          <span className="workspace-avatar">W</span>
          <div>
            <strong title={workspace?.name}>
              {workspace?.name ?? "交易工作台"}
            </strong>
            <span>{workspace ? ROLE_NAMES[workspace.role] : "Workspace"}</span>
          </div>
          <Icon name="chevron" size={14} />
        </div>
        {authenticated && workspaces.length > 1 && (
          <form action={selectWorkspace} className="workspace-select-form">
            <label className="sr-only" htmlFor="workspace-choice">
              切换工作区
            </label>
            <select
              id="workspace-choice"
              name="workspace_id"
              defaultValue={selectedWorkspaceId}
              key={selectedWorkspaceId}
            >
              {workspaces.map((item) => (
                <option key={item.workspace_id} value={item.workspace_id}>
                  {item.name} · {ROLE_NAMES[item.role]}
                </option>
              ))}
            </select>
            <button className="button button-secondary" type="submit">
              切换
            </button>
          </form>
        )}
        <nav aria-label="主导航">
          {(["研究与交易", "治理与管理"] as const).map((group) => (
            <div className="nav-group" key={group}>
              <p className="nav-label">{group}</p>
              {navigation
                .filter((item) => item.group === group)
                .map((item) => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`nav-item ${pathname === item.href ? "active" : ""}`}
                    aria-current={pathname === item.href ? "page" : undefined}
                    onClick={() => setMenuOpen(false)}
                  >
                    <Icon name={item.icon} />
                    <span>{item.label}</span>
                    {pathname === item.href && <span className="active-dot" />}
                  </Link>
                ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="environment">
            <span className="status-dot" />
            <span>研究环境</span>
            <span className="mono">PAPER</span>
          </div>
          <div className="identity">
            <span className="user-avatar">{authenticated ? "用" : "访"}</span>
            <div>
              <strong>{authenticated ? "已登录" : "访客"}</strong>
              <span>{authenticated ? "身份已验证" : "尚未登录"}</span>
            </div>
            {authenticated ? (
              <button
                className="icon-button"
                onClick={logout}
                disabled={loggingOut}
                aria-label="退出登录"
                title="退出登录"
              >
                <Icon name="lock" size={15} />
              </button>
            ) : (
              <Icon name="lock" size={15} />
            )}
          </div>
          {logoutError && (
            <p className="logout-error" role="alert">
              退出失败，请重试。
            </p>
          )}
        </div>
      </aside>
      {menuOpen && (
        <button
          className="menu-backdrop"
          aria-label="关闭导航"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <div className="app-body">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label={menuOpen ? "关闭主导航" : "打开主导航"}
              aria-expanded={menuOpen}
              aria-controls="workspace-navigation"
              onClick={() => setMenuOpen(!menuOpen)}
            >
              <Icon name={menuOpen ? "close" : "menu"} />
            </button>
            <span className="breadcrumb-root">工作区</span>
            <Icon name="chevron" size={13} />
            <strong>{current}</strong>
          </div>
          <div className="topbar-actions">
            <span className="badge badge-safe">
              <Icon name="shield" size={12} />
              实盘已关闭
            </span>
            <span className="topbar-divider" />
            <span className="timezone mono">UTC</span>
            {authenticated && (
              <CommandPalette
                authenticated={authenticated}
                role={workspace?.role ?? null}
              />
            )}
          </div>
        </header>
        <main id="main-content" tabIndex={-1}>
          {children}
        </main>
        <footer className="statusbar">
          <span>
            <span
              className={
                workspace ? "status-dot" : "status-dot status-dot-muted"
              }
            />
            {workspace
              ? "工作区已连接"
              : authenticated
                ? "工作区暂不可用"
                : "工作区未连接"}
          </span>
          <span>
            数据与交易操作需要授权<span className="footer-divider">·</span>
            <span className="mono">QTRADE</span>
          </span>
        </footer>
      </div>
    </div>
  );
}
