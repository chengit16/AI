import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router";
import { Skeleton } from "antd";

import { AppShell } from "@/app/AppShell";
import { pageRoutes } from "@/config/resources";
import { StateView } from "@/components/StateView/StateView";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";
import { useSessionStore } from "@/store/session";

const LoginPage = lazy(() => import("@/pages/auth/login"));
const StatusPage = lazy(() => import("@/pages/system/status"));
const WorkspaceMembersPage = lazy(() => import("@/pages/workspace/members"));
const WorkspaceOrganizationPage = lazy(() => import("@/pages/workspace/organization"));
const WorkspaceOverviewPage = lazy(() => import("@/pages/workspace/overview"));

function RequireSession() {
  const location = useLocation();
  const workspaceId = useSessionStore((state) => state.workspaceId);
  return workspaceId ? (
    <Outlet />
  ) : (
    <Navigate to="/login" replace state={{ from: location.pathname }} />
  );
}

function RequireMenuRoute({ children }: { children: ReactNode }) {
  const location = useLocation();
  const { navigation, isLoading, error } = useWorkspaceMenuNavigation();
  if (isLoading) return <Skeleton active paragraph={{ rows: 10 }} />;
  if (error) {
    return (
      <StateView
        kind="error"
        title="菜单暂时无法加载"
        description="当前空间导航未能同步，请稍后重试。"
      />
    );
  }
  if (!navigation.some((item) => item.to === location.pathname)) {
    return (
      <StateView
        kind="denied"
        title="没有访问此页面的权限"
        description="当前空间的菜单发布或角色配置未向当前账号开放此页面。"
      />
    );
  }
  return <>{children}</>;
}

export function AppRoutes() {
  return (
    <Suspense fallback={<Skeleton active paragraph={{ rows: 10 }} />}>
      <Routes>
        <Route path={pageRoutes.LoginPage} element={<LoginPage />} />
        <Route element={<RequireSession />}>
          <Route element={<AppShell />}>
            <Route
              path={pageRoutes.WorkspaceOverviewPage}
              element={
                <RequireMenuRoute>
                  <WorkspaceOverviewPage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.WorkspaceMembersPage}
              element={
                <RequireMenuRoute>
                  <WorkspaceMembersPage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.WorkspaceOrganizationPage}
              element={
                <RequireMenuRoute>
                  <WorkspaceOrganizationPage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.StatusPage}
              element={
                <RequireMenuRoute>
                  <StatusPage />
                </RequireMenuRoute>
              }
            />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to={pageRoutes.WorkspaceOverviewPage} replace />} />
      </Routes>
    </Suspense>
  );
}
