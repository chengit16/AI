import { lazy, Suspense } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router";
import { Skeleton } from "antd";

import { AppShell } from "@/app/AppShell";
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

export function AppRoutes() {
  return (
    <Suspense fallback={<Skeleton active paragraph={{ rows: 10 }} />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireSession />}>
          <Route element={<AppShell />}>
            <Route path="/workspace/overview" element={<WorkspaceOverviewPage />} />
            <Route path="/workspace/members" element={<WorkspaceMembersPage />} />
            <Route path="/workspace/organization" element={<WorkspaceOrganizationPage />} />
            <Route path="/status" element={<StatusPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/workspace/overview" replace />} />
      </Routes>
    </Suspense>
  );
}
