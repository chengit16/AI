import { useMutation } from "@tanstack/react-query";
import { App, Button, Drawer, Dropdown, Tooltip } from "antd";
import type { MenuProps } from "antd";
import {
  Building2,
  ChevronsLeft,
  ChevronsRight,
  CircleUserRound,
  LogOut,
  Menu,
} from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";

import { errorMessage } from "@/api/client";
import { logoutCurrentSession } from "@/api/services/auth";
import { PlatformMark } from "@/components/PlatformMark/PlatformMark";
import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher/WorkspaceSwitcher";
import { pageRoutes, workspaceNavigation } from "@/config/resources";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

import styles from "./AppShell.module.css";

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className={styles.navigation} aria-label="平台主导航">
      <p className={styles.navigationLabel}>空间管理</p>
      {workspaceNavigation.map(({ key, to, label, icon: Icon }) => (
        <NavLink
          key={key}
          to={to}
          className={({ isActive }) => `${styles.navigationItem} ${isActive ? styles.active : ""}`}
          onClick={onNavigate}
        >
          <Icon size={18} aria-hidden="true" />
          <span>{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

export function AppShell() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const location = useLocation();
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const mobileOpen = useUiStore((state) => state.mobileNavigationOpen);
  const setMobileOpen = useUiStore((state) => state.setMobileNavigationOpen);
  const clearSession = useSessionStore((state) => state.clear);
  const accountId = useSessionStore((state) => state.accountId);
  const logout = useMutation({
    mutationFn: logoutCurrentSession,
    onSettled: () => {
      clearSession();
      navigate(pageRoutes.LoginPage, { replace: true });
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const userMenu: MenuProps["items"] = [
    {
      key: "account",
      label: accountId ? `账号 ${accountId.slice(0, 8)}` : "当前账号",
      disabled: true,
    },
    { type: "divider" },
    { key: "logout", label: "退出登录", icon: <LogOut size={16} /> },
  ];

  return (
    <div className={`${styles.shell} ${collapsed ? styles.collapsed : ""}`}>
      <aside className={styles.sidebar}>
        <div className={styles.brand}>
          <PlatformMark compact={collapsed} />
        </div>
        <Navigation />
        <Tooltip title={collapsed ? "展开侧栏" : "收起侧栏"} placement="right">
          <Button
            className={styles.collapseButton}
            type="text"
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            icon={collapsed ? <ChevronsRight size={18} /> : <ChevronsLeft size={18} />}
            onClick={toggleSidebar}
          />
        </Tooltip>
      </aside>

      <div className={styles.workspace}>
        <header className={styles.topbar}>
          <Button
            className={styles.mobileMenu}
            aria-label="打开主导航"
            icon={<Menu size={19} />}
            onClick={() => setMobileOpen(true)}
          />
          <WorkspaceSwitcher />
          <div className={styles.topbarEnd}>
            <span className={styles.routeLabel}>
              <Building2 size={16} />
              {workspaceNavigation.find((item) => location.pathname.startsWith(item.to))?.label ??
                "空间管理"}
            </span>
            <Dropdown
              menu={{ items: userMenu, onClick: ({ key }) => key === "logout" && logout.mutate() }}
              placement="bottomRight"
            >
              <Button aria-label="账号菜单" icon={<CircleUserRound size={19} />} />
            </Dropdown>
          </div>
        </header>
        <main className={styles.content} id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>

      <Drawer
        className={styles.mobileDrawer}
        title={<PlatformMark />}
        placement="left"
        size="default"
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
      >
        <Navigation onNavigate={() => setMobileOpen(false)} />
      </Drawer>
    </div>
  );
}
