/** @description P1F-05 图编辑器权限、配置校验和审批责任按钮测试。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApprovalInstance, WorkflowDetail } from "@/api/services/workflows";

import { ApprovalInbox } from "./components/ApprovalInbox";
import { WorkflowGraphEditor } from "./components/WorkflowGraphEditor";
import { createStarterGraph } from "./config";

const detail: WorkflowDetail = {
  workflow: {
    workflow_id: "f1000000-0000-4000-8000-000000000001",
    workspace_id: "f1000000-0000-4000-8000-000000000002",
    name: "合成发布工作流",
    description: "仅用于前端交互测试",
    status: "active",
    current_version_id: null,
    created_by_account_id: "f1000000-0000-4000-8000-000000000003",
    created_at: "2026-08-15T08:00:00Z",
    updated_at: "2026-08-15T08:00:00Z",
    version: 1,
  },
  draft: {
    workflow_id: "f1000000-0000-4000-8000-000000000001",
    workspace_id: "f1000000-0000-4000-8000-000000000002",
    revision: 1,
    graph: createStarterGraph(),
    graph_digest: "a".repeat(64),
    validation_errors: [],
    updated_by_account_id: "f1000000-0000-4000-8000-000000000003",
    updated_at: "2026-08-15T08:00:00Z",
  },
  publication: null,
};

const approval: ApprovalInstance = {
  approval_instance_id: "f2000000-0000-4000-8000-000000000001",
  workspace_id: "f1000000-0000-4000-8000-000000000002",
  approval_policy_id: null,
  approval_policy_version_id: null,
  requester_account_id: "f2000000-0000-4000-8000-000000000002",
  resource_type: "document",
  operation: "publish",
  resource_id: null,
  subject_digest: "b".repeat(64),
  chain_digest: "c".repeat(64),
  personal_owner_confirmation: false,
  status: "pending",
  current_sequence_no: 1,
  workflow_run_id: null,
  workflow_step_id: null,
  created_at: "2026-08-15T08:00:00Z",
  updated_at: "2026-08-15T08:00:00Z",
  completed_at: null,
  version: 1,
  levels: [
    {
      approval_level_id: "f2000000-0000-4000-8000-000000000003",
      sequence_no: 1,
      mode: "all",
      status: "active",
      reminder_after_minutes: 60,
      timeout_after_minutes: 120,
      timeout_action: "wait",
      fallback_approver_account_ids: [],
      fallback_activated: false,
      reminder_at: null,
      reminded_at: null,
      timeout_at: null,
      activated_at: "2026-08-15T08:00:00Z",
      completed_at: null,
      version: 1,
    },
  ],
  assignments: [
    {
      approval_assignment_id: "f2000000-0000-4000-8000-000000000004",
      approval_level_id: "f2000000-0000-4000-8000-000000000003",
      approver_account_id: "f2000000-0000-4000-8000-000000000005",
      status: "pending",
      transferred_to_account_id: null,
      created_at: "2026-08-15T08:00:00Z",
      decided_at: null,
      version: 1,
    },
  ],
};

describe("P1F-05 工作流审批交互", () => {
  afterEach(cleanup);

  it("只读用户看不到草稿和发布动作", () => {
    render(
      <WorkflowGraphEditor
        detail={detail}
        canUpdate={false}
        canPublish={false}
        isMutating={false}
        onSave={vi.fn()}
        onValidate={vi.fn()}
        onPublish={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "保存草稿" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "发布" })).not.toBeInTheDocument();
  });

  it("节点配置不是 JSON 对象时阻止保存", () => {
    render(
      <WorkflowGraphEditor
        detail={detail}
        canUpdate
        canPublish
        isMutating={false}
        onSave={vi.fn()}
        onValidate={vi.fn()}
        onPublish={vi.fn()}
      />,
    );

    const configs = screen.getAllByLabelText("声明式配置 JSON");
    fireEvent.change(configs[0], { target: { value: "[]" } });
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeDisabled();
    expect(screen.getByText("节点配置必须是有效 JSON 对象后才能保存")).toBeInTheDocument();
  });

  it("审批动作只向当前活动责任人显示", () => {
    const permissions = new Set([
      "approval.instance.approve",
      "approval.instance.reject",
      "approval.instance.transfer",
    ]);
    const { rerender } = render(
      <ApprovalInbox
        items={[approval]}
        accountId="f2000000-0000-4000-8000-000000000006"
        isLoading={false}
        permissions={permissions}
        isMutating={false}
        onAct={vi.fn()}
        onTransfer={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "通过" })).not.toBeInTheDocument();

    rerender(
      <ApprovalInbox
        items={[approval]}
        accountId="f2000000-0000-4000-8000-000000000005"
        isLoading={false}
        permissions={permissions}
        isMutating={false}
        onAct={vi.fn()}
        onTransfer={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "通过" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "驳回" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "转交" })).toBeInTheDocument();
  });
});
