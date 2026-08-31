/** @description P6B-02 团队管理页面状态、筛选与治理动作测试。 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { App as AntdApp } from "antd";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const workspaceHarness = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
const teamHarness = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));

vi.mock("@/hooks/useCurrentWorkspace", () => ({
  useCurrentWorkspace: () => workspaceHarness.value,
}));
vi.mock("./useTeamManagement", () => ({
  useTeamManagement: () => teamHarness.value,
}));

import { PlatformApiError } from "@/api/client";
import type { TeamManagement } from "@/api/services/teamManagement";

import WorkspaceMembersPage from "./index";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000932";
const OWNER_ID = "10000000-0000-4000-8000-000000000932";
const ACTIVE_ID = "10000000-0000-4000-8000-000000000933";
const DISABLED_ID = "10000000-0000-4000-8000-000000000934";
const LEFT_ID = "10000000-0000-4000-8000-000000000935";
const DEPARTMENT_ID = "21000000-0000-4000-8000-000000000932";
const POSITION_ID = "22000000-0000-4000-8000-000000000932";
const ROLE_ID = "23000000-0000-4000-8000-000000000932";
const INVITATION_ID = "24000000-0000-4000-8000-000000000932";

const snapshot: TeamManagement = {
  workspace: {
    workspace_id: WORKSPACE_ID,
    workspace_type: "enterprise",
    name: "合成团队企业",
    status: "active",
  },
  statistics: {
    active_members: 2,
    disabled_members: 1,
    pending_invitations: 1,
    departments: 1,
    positions: 1,
  },
  members: [
    {
      account_id: OWNER_ID,
      display_name: "合成所有者",
      login_name: "owner.synthetic@example.test",
      membership_type: "owner",
      status: "active",
      department_ids: [],
      primary_department_id: null,
      position_ids: [],
      direct_role_ids: [],
      effective_roles: [],
      joined_at: "2026-08-31T01:00:00Z",
      last_active_at: "2026-08-31T02:00:00Z",
      updated_at: "2026-08-31T02:00:00Z",
      version: 1,
    },
    {
      account_id: ACTIVE_ID,
      display_name: "合成研发成员",
      login_name: "member.synthetic@example.test",
      membership_type: "member",
      status: "active",
      department_ids: [DEPARTMENT_ID],
      primary_department_id: DEPARTMENT_ID,
      position_ids: [POSITION_ID],
      direct_role_ids: [ROLE_ID],
      effective_roles: [
        {
          role_id: ROLE_ID,
          role_key: "synthetic_reviewer",
          name: "合成审核员",
          source_types: ["member"],
        },
      ],
      joined_at: "2026-08-31T01:00:00Z",
      last_active_at: null,
      updated_at: "2026-08-31T02:00:00Z",
      version: 7,
    },
    {
      account_id: DISABLED_ID,
      display_name: "合成停用成员",
      login_name: "disabled.synthetic@example.test",
      membership_type: "member",
      status: "disabled",
      department_ids: [DEPARTMENT_ID],
      primary_department_id: DEPARTMENT_ID,
      position_ids: [POSITION_ID],
      direct_role_ids: [ROLE_ID],
      effective_roles: [],
      joined_at: "2026-08-31T01:00:00Z",
      last_active_at: null,
      updated_at: "2026-08-31T02:00:00Z",
      version: 3,
    },
    {
      account_id: LEFT_ID,
      display_name: "合成离开成员",
      login_name: "left.synthetic@example.test",
      membership_type: "member",
      status: "left",
      department_ids: [],
      primary_department_id: null,
      position_ids: [],
      direct_role_ids: [],
      effective_roles: [],
      joined_at: "2026-08-31T01:00:00Z",
      last_active_at: null,
      updated_at: "2026-08-31T02:00:00Z",
      version: 2,
    },
  ],
  invitations: [
    {
      invitation_id: INVITATION_ID,
      invited_account_id: "10000000-0000-4000-8000-000000000936",
      invited_display_name: "合成待邀请成员",
      invited_login_name: "invited.synthetic@example.test",
      invited_by_display_name: "合成所有者",
      status: "pending",
      created_at: "2026-08-31T01:00:00Z",
      expires_at: "2026-09-07T01:00:00Z",
      accepted_at: null,
    },
    {
      invitation_id: "24000000-0000-4000-8000-000000000933",
      invited_account_id: "10000000-0000-4000-8000-000000000937",
      invited_display_name: "合成过期邀请",
      invited_login_name: "expired.synthetic@example.test",
      invited_by_display_name: "合成所有者",
      status: "expired",
      created_at: "2026-08-01T01:00:00Z",
      expires_at: "2026-08-08T01:00:00Z",
      accepted_at: null,
    },
  ],
  departments: [
    {
      department_id: DEPARTMENT_ID,
      parent_department_id: null,
      name: "合成研发部",
      depth: 0,
      status: "active",
      effective_active: true,
      version: 1,
    },
  ],
  positions: [
    {
      position_id: POSITION_ID,
      department_id: DEPARTMENT_ID,
      name: "合成工程师",
      status: "active",
      effective_active: true,
      version: 1,
    },
  ],
  roles: [{ role_id: ROLE_ID, role_key: "synthetic_reviewer", name: "合成审核员" }],
  recent_audits: [],
  generated_at: "2026-08-31T03:00:00Z",
};

function mutation() {
  return { isPending: false, mutate: vi.fn() };
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>;
}

function renderPage(initialEntry = "/workspace/members") {
  return render(
    <AntdApp>
      <MemoryRouter initialEntries={[initialEntry]}>
        <WorkspaceMembersPage />
        <LocationProbe />
      </MemoryRouter>
    </AntdApp>,
  );
}

describe("P6B-02 团队管理页面", () => {
  beforeEach(() => {
    workspaceHarness.value = {
      workspaceId: WORKSPACE_ID,
      currentWorkspace: {
        workspace_id: WORKSPACE_ID,
        name: "合成团队企业",
        workspace_type: "enterprise",
      },
      workspaces: { isLoading: false, isError: false, error: null },
    };
    teamHarness.value = {
      team: {
        isLoading: false,
        isError: false,
        error: null,
        data: snapshot,
        refetch: vi.fn(),
      },
      invite: mutation(),
      cancelInvitation: mutation(),
      updateMember: mutation(),
      disableMember: mutation(),
      activateMember: mutation(),
      removeMember: mutation(),
    };
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("区分加载、个人空间、权限拒绝和可重试错误", async () => {
    workspaceHarness.value = {
      ...workspaceHarness.value,
      workspaces: { isLoading: true, isError: false, error: null },
    };
    const loading = renderPage();
    expect(loading.container.querySelector(".ant-skeleton")).toBeInTheDocument();
    loading.unmount();

    workspaceHarness.value = {
      ...workspaceHarness.value,
      currentWorkspace: {
        workspace_id: WORKSPACE_ID,
        name: "合成个人空间",
        workspace_type: "personal",
      },
      workspaces: { isLoading: false, isError: false, error: null },
    };
    const personal = renderPage();
    expect(screen.getByText("个人空间无需团队治理")).toBeInTheDocument();
    personal.unmount();

    workspaceHarness.value = {
      ...workspaceHarness.value,
      currentWorkspace: {
        workspace_id: WORKSPACE_ID,
        name: "合成团队企业",
        workspace_type: "enterprise",
      },
    };
    teamHarness.value = {
      ...teamHarness.value,
      team: {
        isLoading: false,
        isError: true,
        data: undefined,
        error: new PlatformApiError(403, "POLICY_DENIED", "拒绝", false),
        refetch: vi.fn(),
      },
    };
    const denied = renderPage();
    expect(screen.getByText("当前账号没有团队治理权限")).toBeInTheDocument();
    denied.unmount();

    const refetch = vi.fn();
    teamHarness.value = {
      ...teamHarness.value,
      team: {
        isLoading: false,
        isError: true,
        data: undefined,
        error: new Error("synthetic team failure"),
        refetch,
      },
    };
    renderPage();
    expect(screen.getByText("团队数据未能加载")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("从 URL 恢复搜索、状态、部门、岗位和角色筛选并支持重置", async () => {
    renderPage(
      `/workspace/members?query=研发&status=active&department=${DEPARTMENT_ID}&position=${POSITION_ID}&role=${ROLE_ID}`,
    );

    expect(screen.getByDisplayValue("研发")).toBeInTheDocument();
    expect(
      screen.getByText("显示 1 / 4 名成员；最后活跃来自当前企业审计事实。"),
    ).toBeInTheDocument();
    expect(screen.getByText("合成研发成员")).toBeInTheDocument();
    expect(screen.queryByText("合成停用成员")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重置" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/workspace/members"),
    );
    expect(
      screen.getByText("显示 4 / 4 名成员；最后活跃来自当前企业审计事实。"),
    ).toBeInTheDocument();
  });

  it("保护 Owner 和移除终态，并分别触发停用、恢复与移除", async () => {
    renderPage();
    await screen.findByText("合成研发成员");

    const ownerRow = screen.getByText("合成所有者").closest("tr");
    const leftRow = screen.getByText("合成离开成员").closest("tr");
    const activeRow = screen.getByText("合成研发成员").closest("tr");
    const disabledRow = screen.getByText("合成停用成员").closest("tr");
    expect(ownerRow).not.toBeNull();
    expect(leftRow).not.toBeNull();
    expect(activeRow).not.toBeNull();
    expect(disabledRow).not.toBeNull();
    expect(within(ownerRow!).getByText("系统所有者不可变更")).toBeInTheDocument();
    expect(within(leftRow!).getByText("需通过新邀请重新加入")).toBeInTheDocument();

    fireEvent.click(within(activeRow!).getByRole("button", { name: "停用" }));
    const disableConfirm = (await screen.findByText("停用该成员？")).closest<HTMLElement>(
      ".ant-popconfirm",
    );
    expect(disableConfirm).not.toBeNull();
    fireEvent.click(
      within(disableConfirm!).getByRole("button", {
        name: /停\s*用/,
      }),
    );
    expect(
      (teamHarness.value.disableMember as ReturnType<typeof mutation>).mutate,
    ).toHaveBeenCalledWith(ACTIVE_ID);

    fireEvent.click(within(disabledRow!).getByRole("button", { name: "恢复" }));
    expect(
      (teamHarness.value.activateMember as ReturnType<typeof mutation>).mutate,
    ).toHaveBeenCalledWith(DISABLED_ID);

    fireEvent.click(within(activeRow!).getByRole("button", { name: "移除" }));
    const removeConfirm = (await screen.findByText("移除该成员？")).closest<HTMLElement>(
      ".ant-popconfirm",
    );
    expect(removeConfirm).not.toBeNull();
    fireEvent.click(
      within(removeConfirm!).getByRole("button", {
        name: /确\s*认\s*移\s*除/,
      }),
    );
    expect(
      (teamHarness.value.removeMember as ReturnType<typeof mutation>).mutate,
    ).toHaveBeenCalledWith(ACTIVE_ID);
  });

  it("成员编辑携带乐观版本并一次提交完整组织和直接角色配置", async () => {
    renderPage();
    const activeRow = (await screen.findByText("合成研发成员")).closest("tr");
    expect(activeRow).not.toBeNull();
    fireEvent.click(within(activeRow!).getByRole("button", { name: "编辑" }));
    expect(await screen.findByText("本次保存为原子更新")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));

    await waitFor(() =>
      expect(
        (teamHarness.value.updateMember as ReturnType<typeof mutation>).mutate,
      ).toHaveBeenCalledWith(
        {
          member: snapshot.members[1],
          body: {
            expected_version: 7,
            department_ids: [DEPARTMENT_ID],
            primary_department_id: DEPARTMENT_ID,
            position_ids: [POSITION_ID],
            direct_role_ids: [ROLE_ID],
          },
        },
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      ),
    );
  });

  it("创建和撤销邀请，过期邀请只读，并明确展示空审计与缺失活跃事实", async () => {
    renderPage();
    await screen.findByText("合成待邀请成员");
    expect(screen.getByText("已过期")).toBeInTheDocument();
    expect(screen.getByText("暂无团队治理审计记录")).toBeInTheDocument();
    expect(screen.getAllByText("暂无活动记录").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "邀请成员" }));
    fireEvent.change(screen.getByPlaceholderText("member@example.com"), {
      target: { value: "new.synthetic@example.test" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建邀请" }));
    await waitFor(() =>
      expect((teamHarness.value.invite as ReturnType<typeof mutation>).mutate).toHaveBeenCalledWith(
        "new.synthetic@example.test",
      ),
    );

    const invitationRow = screen.getByText("合成待邀请成员").closest("tr");
    const expiredRow = screen.getByText("合成过期邀请").closest("tr");
    expect(invitationRow).not.toBeNull();
    expect(expiredRow).not.toBeNull();
    expect(within(expiredRow!).queryByRole("button", { name: "撤销" })).not.toBeInTheDocument();
    fireEvent.click(within(invitationRow!).getByRole("button", { name: "撤销" }));
    const cancelConfirm = (await screen.findByText("撤销这条邀请？")).closest<HTMLElement>(
      ".ant-popconfirm",
    );
    expect(cancelConfirm).not.toBeNull();
    fireEvent.click(
      within(cancelConfirm!).getByRole("button", {
        name: /撤\s*销/,
      }),
    );
    expect(
      (teamHarness.value.cancelInvitation as ReturnType<typeof mutation>).mutate,
    ).toHaveBeenCalledWith(INVITATION_ID);
    expect(within(invitationRow!).getByRole("button", { name: "复制链接" })).toBeInTheDocument();
  });
});
