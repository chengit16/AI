/**
 * @description 动态菜单应用壳层
 * 负责工作空间导航、桌面侧栏和移动抽屉，菜单裁剪不替代服务端接口授权。
 */
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
import { useEffect, useRef } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";

import { errorMessage } from "@/api/client";
import { logoutCurrentSession } from "@/api/services/auth";
import { PlatformMark } from "@/components/PlatformMark/PlatformMark";
import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher/WorkspaceSwitcher";
import { pageRoutes, staticPlatformNavigation } from "@/config/resources";
import { usePlatformAdministration } from "@/hooks/usePlatformAdministration";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import { cn } from "@/utils/cn";

function Navigation({
  items,
  label,
  onNavigate,
  surface = "dark",
}: {
  items: ReturnType<typeof useWorkspaceMenuNavigation>["navigation"];
  label: string;
  onNavigate?: () => void;
  surface?: "dark" | "light";
}) {
  const isLightSurface = surface === "light";
  return (
    <nav className="flex-none px-3 py-5" aria-label={label}>
      <p
        className={cn(
          "navigation-copy mx-3 mb-2 mt-0 whitespace-nowrap text-[11px] font-700",
          isLightSurface ? "text-text-muted" : "text-nav-muted",
        )}
      >
        {label}
      </p>
      {items.length === 0 && (
        <span
          className={cn(
            "navigation-copy m-3 block text-xs leading-[1.5]",
            isLightSurface ? "text-text-muted" : "text-nav-muted",
          )}
        >
          暂无可用页面
        </span>
      )}
      {items.map(({ key, to, label, icon: Icon }) => (
        <NavLink
          key={key}
          to={to}
          className={({ isActive }) =>
            cn(
              "my-0.5 flex min-h-11 items-center gap-3 whitespace-nowrap rounded-ui px-[13px] no-underline",
              isLightSurface
                ? "text-text hover:bg-brand-soft hover:text-brand"
                : "text-nav-text hover:bg-nav-hover hover:text-nav-text-strong",
              isActive &&
                (isLightSurface
                  ? "bg-brand-soft text-brand shadow-[inset_3px_0_0_var(--color-brand)]"
                  : "bg-nav-active text-nav-text-strong shadow-[inset_3px_0_0_var(--color-accent)]"),
            )
          }
          onClick={onNavigate}
        >
          <Icon size={18} aria-hidden="true" />
          <span className="navigation-copy">{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}

/**
 * 平台登录态应用壳层，统一组织动态菜单、工作空间、账号入口和响应式导航。
 *
 * 空间菜单来自已发布快照，平台治理菜单来自静态注册表；两者的隐藏和路由守卫
 * 只改善前端体验，接口与字段权限仍由后端 PDP 独立执行。
 */
export function AppShell() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const location = useLocation();
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const mobileOpen = useUiStore((state) => state.mobileNavigationOpen);
  const setMobileOpen = useUiStore((state) => state.setMobileNavigationOpen);
  const { navigation } = useWorkspaceMenuNavigation();
  const { isPlatformAdministrator } = usePlatformAdministration();
  const allNavigation = isPlatformAdministrator
    ? [...navigation, ...staticPlatformNavigation]
    : navigation;
  const clearSession = useSessionStore((state) => state.clear);
  const accountId = useSessionStore((state) => state.accountId);
  const mainContentRef = useRef<HTMLElement>(null);
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

  useEffect(() => {
    // 路由切换后把阅读起点交给主内容，避免键盘和读屏用户仍停留在旧导航位置。
    mainContentRef.current?.focus({ preventScroll: true });
  }, [location.pathname]);

  return (
    <div
      className={cn(
        "grid min-h-[100dvh] bg-canvas [transition:grid-template-columns_var(--motion-fast)] nav-mobile:block landscape-mobile:block",
        collapsed ? "grid-cols-[72px_minmax(0,1fr)]" : "grid-cols-[240px_minmax(0,1fr)]",
      )}
    >
      <a className="ui-skip-link" href="#main-content">
        跳到主要内容
      </a>
      <aside className="sticky top-0 z-10 flex h-[100dvh] flex-col overflow-hidden border-r border-r-solid border-nav-divider bg-nav-bg text-nav-text nav-mobile:hidden landscape-mobile:hidden">
        <div className="flex h-16 items-center border-b border-b-solid border-nav-divider px-[18px]">
          <PlatformMark compact={collapsed} />
        </div>
        <div
          className={cn(
            "[&_.navigation-copy]:[transition:opacity_var(--motion-fast)]",
            collapsed && "[&_.navigation-copy]:pointer-events-none [&_.navigation-copy]:opacity-0",
          )}
        >
          <Navigation items={navigation} label="空间管理" />
          {isPlatformAdministrator && (
            <Navigation items={staticPlatformNavigation} label="平台治理" />
          )}
        </div>
        <Tooltip title={collapsed ? "展开侧栏" : "收起侧栏"} placement="right">
          <Button
            className="!mb-3 !ml-3 !mt-auto !h-11 !w-11 !text-nav-text"
            type="text"
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            icon={collapsed ? <ChevronsRight size={18} /> : <ChevronsLeft size={18} />}
            onClick={toggleSidebar}
          />
        </Tooltip>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-8 flex min-h-16 items-center justify-between gap-4 border-b border-b-solid border-border bg-topbar px-[clamp(18px,3vw,40px)] py-2.5 nav-mobile:min-h-15 nav-mobile:px-3 nav-mobile:py-2 landscape-mobile:min-h-15 landscape-mobile:px-3 landscape-mobile:py-2">
          <Button
            className="hidden flex-none nav-mobile:!inline-flex nav-mobile:basis-10 landscape-mobile:!inline-flex landscape-mobile:basis-11"
            aria-label="打开主导航"
            icon={<Menu size={19} />}
            onClick={() => setMobileOpen(true)}
          />
          <WorkspaceSwitcher />
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-3 text-[13px] text-text-muted nav-mobile:hidden landscape-mobile:hidden">
              <Building2 size={16} />
              {allNavigation.find((item) => location.pathname.startsWith(item.to))?.label ??
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
        <main
          ref={mainContentRef}
          className="mx-auto w-[min(1280px,100%)] px-[clamp(18px,3vw,40px)] pb-12 pt-8 outline-none nav-mobile:pt-6 landscape-mobile:pt-6"
          id="main-content"
          tabIndex={-1}
        >
          <Outlet />
        </main>
      </div>

      <Drawer
        title={<PlatformMark surface="light" />}
        placement="left"
        size="default"
        open={mobileOpen}
        focusable={{ focusTriggerAfterClose: false }}
        afterOpenChange={(open) => {
          // 移动抽屉关闭会把焦点还给触发按钮，此时需恢复路由变化后的主内容阅读起点。
          if (!open) mainContentRef.current?.focus({ preventScroll: true });
        }}
        onClose={() => setMobileOpen(false)}
      >
        <Navigation
          items={navigation}
          label="空间管理"
          surface="light"
          onNavigate={() => setMobileOpen(false)}
        />
        {isPlatformAdministrator && (
          <Navigation
            items={staticPlatformNavigation}
            label="平台治理"
            surface="light"
            onNavigate={() => setMobileOpen(false)}
          />
        )}
      </Drawer>
    </div>
  );
}
