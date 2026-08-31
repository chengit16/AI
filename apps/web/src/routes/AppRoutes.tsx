/**
 * @description 应用路由注册与会话保护入口
 * 动态菜单控制导航可见性，路由直访和接口访问仍分别执行体验保护与后端授权。
 */
import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Outlet, Route, Routes, useLocation } from "react-router";
import { Skeleton } from "antd";

import { AppShell } from "@/app/AppShell";
import { pageRoutes } from "@/config/resources";
import { StateView } from "@/components/StateView/StateView";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";
import { usePlatformAdministration } from "@/hooks/usePlatformAdministration";
import { useSessionStore } from "@/store/session";

const LoginPage = lazy(() => import("@/pages/auth/login"));
const StatusPage = lazy(() => import("@/pages/system/status"));
const WorkspaceMembersPage = lazy(() => import("@/pages/workspace/members"));
const WorkspaceOrganizationPage = lazy(() => import("@/pages/workspace/organization"));
const WorkspaceOverviewPage = lazy(() => import("@/pages/workspace/overview"));
const EnterpriseKnowledgePage = lazy(() => import("@/pages/workspace/enterpriseKnowledge"));
const KnowledgeProductionPage = lazy(() => import("@/pages/workspace/knowledge"));
const AssistantConversationsPage = lazy(() => import("@/pages/workspace/assistant"));
const WorkflowDesignPage = lazy(() => import("@/pages/workspace/workflows"));
const AgentControlPage = lazy(() => import("@/pages/workspace/agents"));
const ServiceManagementPage = lazy(() => import("@/pages/workspace/services"));
const AgentOperationsPage = lazy(() => import("@/pages/workspace/agentOperations"));
const ToolExecutionConsolePage = lazy(() => import("@/pages/workspace/tools"));
const ToolRunHistoryPage = lazy(() => import("@/pages/workspace/toolRuns"));
const OperationsControlTowerPage = lazy(() => import("@/pages/workspace/controlTower"));
const PlatformModelsPage = lazy(() => import("@/pages/platform/models"));

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
        headingLevel={1}
        title="菜单暂时无法加载"
        description="当前空间导航未能同步，请稍后重试。"
      />
    );
  }
  if (!navigation.some((item) => item.to === location.pathname)) {
    return (
      <StateView
        kind="denied"
        headingLevel={1}
        title="没有访问此页面的权限"
        description="当前空间的菜单发布或角色配置未向当前账号开放此页面。"
      />
    );
  }
  return <>{children}</>;
}

function RequirePlatformAdministrator({ children }: { children: ReactNode }) {
  const { providers, isDenied } = usePlatformAdministration();
  if (providers.isLoading) return <Skeleton active paragraph={{ rows: 10 }} />;
  if (isDenied) {
    return (
      <StateView
        kind="denied"
        headingLevel={1}
        title="仅平台管理员可访问"
        description="模型供应商和运行配置属于平台级治理，不接受工作空间角色授权。"
      />
    );
  }
  if (providers.isError) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="平台权限暂时无法核验"
        description="未能从服务端确认平台管理员资格，请稍后重试。"
      />
    );
  }
  return <>{children}</>;
}

/** 注册登录、空间菜单保护和平台管理员保护后的完整路由树。 */
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
              path={pageRoutes.KnowledgeProductionPage}
              element={
                <RequireMenuRoute>
                  <KnowledgeProductionPage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.EnterpriseKnowledgePage}
              element={
                <RequireMenuRoute>
                  <EnterpriseKnowledgePage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.AssistantConversationsPage}
              element={
                <RequireMenuRoute>
                  <AssistantConversationsPage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.WorkflowDesignPage}
              element={
                <RequireMenuRoute>
                  <WorkflowDesignPage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.AgentControlPage}
              element={
                <RequireMenuRoute>
                  <AgentControlPage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.ServiceManagementPage}
              element={
                <RequireMenuRoute>
                  <ServiceManagementPage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.AgentOperationsPage}
              element={
                <RequireMenuRoute>
                  <AgentOperationsPage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.ToolExecutionConsolePage}
              element={
                <RequireMenuRoute>
                  <ToolExecutionConsolePage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.ToolRunHistoryPage}
              element={
                <RequireMenuRoute>
                  <ToolRunHistoryPage />
                </RequireMenuRoute>
              }
            />
            <Route
              path={pageRoutes.OperationsControlTowerPage}
              element={
                <RequireMenuRoute>
                  <OperationsControlTowerPage />
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
            <Route
              path={pageRoutes.PlatformModelsPage}
              element={
                <RequirePlatformAdministrator>
                  <PlatformModelsPage />
                </RequirePlatformAdministrator>
              }
            />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to={pageRoutes.WorkspaceOverviewPage} replace />} />
      </Routes>
    </Suspense>
  );
}
