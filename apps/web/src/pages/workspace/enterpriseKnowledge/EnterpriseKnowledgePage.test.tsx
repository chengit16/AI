/** @description P6B-03 企业知识页的状态、权限裁剪、治理动作与响应式骨架测试。 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App as AntdApp } from "antd";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PlatformApiError } from "@/api/client";
import type { EnterpriseKnowledgePortal } from "@/api/services/enterpriseKnowledge";

const workspaceHarness = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
const menuHarness = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
const managementHarness = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock("@/hooks/useCurrentWorkspace", () => ({
  useCurrentWorkspace: () => workspaceHarness.value,
}));
vi.mock("@/hooks/useWorkspaceMenuNavigation", () => ({
  useWorkspaceMenuNavigation: () => menuHarness.value,
}));
vi.mock("./useEnterpriseKnowledge", () => ({
  useEnterpriseKnowledge: () => managementHarness.value,
}));

import EnterpriseKnowledgePage from "./index";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000905";
const CATEGORY_ID = "30000000-0000-4000-8000-000000000905";
const DOMAIN_ID = "40000000-0000-4000-8000-000000000905";
const KNOWLEDGE_BASE_ID = "50000000-0000-4000-8000-000000000905";
const MEMBERSHIP_ID = "80000000-0000-4000-8000-000000000905";
const ACCOUNT_ID = "70000000-0000-4000-8000-000000000905";
const ALL_PERMISSIONS = new Set([
  "enterprise.knowledge.read",
  "enterprise.category.create",
  "enterprise.category.update",
  "enterprise.category.archive",
  "enterprise.category.bind",
  "enterprise.domain.create",
  "enterprise.domain.update",
  "enterprise.domain.archive",
  "enterprise.domain.scope",
  "enterprise.domain.resolve",
]);

const snapshot: EnterpriseKnowledgePortal = {
  workspace_id: WORKSPACE_ID,
  workspace_name: "合成治理企业",
  statistics: {
    active_categories: 1,
    active_domains: 1,
    classified_documents: 1,
    governed_knowledge_bases: 1,
  },
  categories: [
    {
      category_id: CATEGORY_ID,
      parent_category_id: null,
      name: "合成制度分类",
      description: "仅用于页面测试",
      visibility: "public",
      department_ids: [],
      document_ids: ["60000000-0000-4000-8000-000000000905"],
      status: "active",
      created_at: "2026-08-31T08:00:00Z",
      updated_at: "2026-08-31T08:00:00Z",
      version: 2,
    },
  ],
  domains: [
    {
      domain_id: DOMAIN_ID,
      name: "合成研发知识域",
      description: "仅用于页面测试",
      member_ids: [],
      department_ids: [],
      knowledge_base_ids: [KNOWLEDGE_BASE_ID],
      rag_policy: { policy_version: 3, mode: "precision", top_k: 6, minimum_score: 0.4 },
      status: "active",
      created_at: "2026-08-31T08:00:00Z",
      updated_at: "2026-08-31T08:00:00Z",
      version: 4,
    },
  ],
  documents: [
    {
      document_id: "60000000-0000-4000-8000-000000000905",
      knowledge_base_id: KNOWLEDGE_BASE_ID,
      title: "合成制度文档",
      security_level: "INTERNAL",
    },
  ],
  knowledge_bases: [
    {
      knowledge_base_id: KNOWLEDGE_BASE_ID,
      name: "合成研发库",
      default_security_level: "INTERNAL",
    },
  ],
  departments: [],
  members: [
    {
      membership_id: MEMBERSHIP_ID,
      account_id: ACCOUNT_ID,
      display_name: "合成研发成员",
    },
  ],
  generated_at: "2026-08-31T08:00:00Z",
};

function mutation(data?: unknown) {
  return { isPending: false, data, mutate: vi.fn(), reset: vi.fn() };
}

function renderPage() {
  return render(
    <AntdApp>
      <EnterpriseKnowledgePage />
    </AntdApp>,
  );
}

describe("P6B-03 企业知识治理页面", () => {
  beforeEach(() => {
    workspaceHarness.value = {
      workspaceId: WORKSPACE_ID,
      currentWorkspace: { workspace_id: WORKSPACE_ID, workspace_type: "enterprise" },
      workspaces: { isLoading: false, isError: false, error: null },
    };
    menuHarness.value = {
      isLoading: false,
      error: null,
      visiblePermissionCodes: new Set(ALL_PERMISSIONS),
    };
    managementHarness.value = {
      portal: { isLoading: false, isError: false, error: null, data: snapshot, refetch: vi.fn() },
      createCategory: mutation(),
      updateCategory: mutation(),
      archiveCategory: mutation(),
      bindCategoryDocuments: mutation(),
      createDomain: mutation(),
      updateDomain: mutation(),
      archiveDomain: mutation(),
      replaceDomainScope: mutation(),
      resolveDomainScope: mutation({
        domain_id: DOMAIN_ID,
        policy_version: 8,
        actor_in_declared_scope: true,
        declared_knowledge_base_ids: [KNOWLEDGE_BASE_ID],
        authorized_knowledge_base_ids: [KNOWLEDGE_BASE_ID],
        effective_knowledge_base_ids: [KNOWLEDGE_BASE_ID],
        empty_reason: "none",
      }),
    };
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("区分加载、个人空间、菜单拒绝、服务端拒绝和普通错误", () => {
    workspaceHarness.value = {
      ...workspaceHarness.value,
      workspaces: { isLoading: true, isError: false, error: null },
    };
    const loading = renderPage();
    expect(loading.container.querySelector(".ant-skeleton")).toBeInTheDocument();
    loading.unmount();

    workspaceHarness.value = {
      ...workspaceHarness.value,
      currentWorkspace: { workspace_id: WORKSPACE_ID, workspace_type: "personal" },
      workspaces: { isLoading: false, isError: false, error: null },
    };
    const personal = renderPage();
    expect(screen.getByText("个人空间无需企业知识治理")).toBeInTheDocument();
    personal.unmount();

    workspaceHarness.value = {
      ...workspaceHarness.value,
      currentWorkspace: { workspace_id: WORKSPACE_ID, workspace_type: "enterprise" },
    };
    menuHarness.value = { ...menuHarness.value, visiblePermissionCodes: new Set<string>() };
    const menuDenied = renderPage();
    expect(screen.getByText("当前账号没有企业知识治理权限")).toBeInTheDocument();
    menuDenied.unmount();

    menuHarness.value = { ...menuHarness.value, visiblePermissionCodes: new Set(ALL_PERMISSIONS) };
    managementHarness.value = {
      ...managementHarness.value,
      portal: {
        isLoading: false,
        isError: true,
        data: undefined,
        error: new PlatformApiError(403, "POLICY_DENIED", "拒绝", false),
        refetch: vi.fn(),
      },
    };
    const apiDenied = renderPage();
    expect(screen.getByText("当前账号没有企业知识治理权限")).toBeInTheDocument();
    apiDenied.unmount();

    managementHarness.value = {
      ...managementHarness.value,
      portal: {
        isLoading: false,
        isError: true,
        data: undefined,
        error: new Error("synthetic portal failure"),
        refetch: vi.fn(),
      },
    };
    renderPage();
    expect(screen.getByText("企业知识数据未能加载")).toBeInTheDocument();
  });

  it("展示双栏治理事实并触发归档与范围解释动作", async () => {
    const view = renderPage();
    expect(screen.getByText("合成治理企业")).toBeInTheDocument();
    expect(screen.getByText("合成制度分类")).toBeInTheDocument();
    expect(screen.getByText("合成研发知识域")).toBeInTheDocument();
    expect(view.container.querySelector('[class*="tablet-down:grid-cols-1"]')).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "解释知识域“合成研发知识域”的有效范围" }));
    expect(
      (managementHarness.value.resolveDomainScope as ReturnType<typeof mutation>).mutate,
    ).toHaveBeenCalledWith(DOMAIN_ID);
    expect(await screen.findByText("有效范围非空")).toBeInTheDocument();
    expect(screen.getByText("运行时有效交集")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "归档分类“合成制度分类”" }));
    const confirm = (await screen.findByText("归档该分类？")).closest<HTMLElement>(
      ".ant-popconfirm",
    );
    expect(confirm).not.toBeNull();
    fireEvent.click(within(confirm!).getByRole("button", { name: "归 档" }));
    await waitFor(() =>
      expect(
        (managementHarness.value.archiveCategory as ReturnType<typeof mutation>).mutate,
      ).toHaveBeenCalledWith(snapshot.categories[0]),
    );
  });

  it("按菜单权限隐藏所有高风险按钮但保留只读事实", () => {
    menuHarness.value = {
      ...menuHarness.value,
      visiblePermissionCodes: new Set(["enterprise.knowledge.read"]),
    };
    renderPage();
    expect(screen.getByText("合成制度分类")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新建分类" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新建知识域" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "归档分类“合成制度分类”" }),
    ).not.toBeInTheDocument();
  });

  it("声明范围提交企业成员关系标识而不是账号标识", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "配置知识域“合成研发知识域”的声明范围" }));

    const memberSelect = screen.getByRole("combobox", { name: "直接成员" });
    fireEvent.mouseDown(memberSelect);
    fireEvent.click(await screen.findByText("合成研发成员"));
    fireEvent.click(screen.getByRole("button", { name: "原子保存范围" }));

    await waitFor(() =>
      expect(
        (managementHarness.value.replaceDomainScope as ReturnType<typeof mutation>).mutate,
      ).toHaveBeenCalledWith(
        {
          domain: snapshot.domains[0],
          body: {
            expected_version: 4,
            member_ids: [MEMBERSHIP_ID],
            department_ids: [],
            knowledge_base_ids: [KNOWLEDGE_BASE_ID],
          },
        },
        expect.any(Object),
      ),
    );
    expect(MEMBERSHIP_ID).not.toBe(ACCOUNT_ID);
  });
});
