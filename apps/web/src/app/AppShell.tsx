import { useMutation } from "@tanstack/react-query";
import { App, Button, Drawer, Dropdown, Tooltip } from "antd";
import type { MenuProps } from "antd";
import {
  Activity,
  Building2,
  ChevronsLeft,
  ChevronsRight,
  CircleUserRound,
  LayoutDashboard,
  LogOut,
  Menu,
  Network,
  UsersRound,
} from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";

import { errorMessage } from "@/api/client";
import { logoutCurrentSession } from "@/api/services/auth";
import { PlatformMark } from "@/components/PlatformMark/PlatformMark";
import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher/WorkspaceSwitcher";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

import styles from "./AppShell.module.css";

const navigation = [
  { to: "/workspace/overview", label: "空间总览", icon: LayoutDashboard },
  { to: "/workspace/members", label: "成员管理", icon: UsersRound },
  { to: "/workspace/organization", label: "组织架构", icon: Network },
  { to: "/status", label: "运行状态", icon: Activity },
];

function Navigation({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className={styles.navigation} aria-label="平台主导航">
      <p className={styles.navigationLabel}>空间管理</p>
      {navigation.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
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
      navigate("/login", { replace: true });
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
              {navigation.find((item) => location.pathname.startsWith(item.to))?.label ??
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
