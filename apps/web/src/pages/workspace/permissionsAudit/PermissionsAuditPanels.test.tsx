/** @description P6B-05 角色矩阵与审计导出面板交互测试。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { App as AntdApp } from "antd";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AuditExport,
  AuditRecord,
  AuditRecordDetail,
  RoleGovernance,
} from "@/api/services/permissionsAudit";

import { AuditGovernancePanel } from "./AuditGovernancePanel";
import { RoleGovernancePanel } from "./RoleGovernancePanel";

const ROLE_ID = "70000000-0000-4000-8000-000000000905";
const AUDIT_ID = "61000000-0000-4000-8000-000000000905";

const governance: RoleGovernance = {
  role_version: 7,
  roles: [
    {
      role_id: ROLE_ID,
      role_key: "synthetic_reviewer",
      name: "合成审核员",
      status: "active",
      system_managed: false,
      editable: true,
      grants: [],
      bindings: [
        {
          scope_type: "workspace",
          scope_id: "20000000-0000-4000-8000-000000000905",
          scope_name: "当前工作空间",
        },
      ],
      affected_member_count: 1,
      affected_members: [
        {
          account_id: "10000000-0000-4000-8000-000000000905",
          display_name: "合成研发成员",
          membership_type: "member",
          sources: [
            {
              scope_type: "workspace",
              scope_id: "20000000-0000-4000-8000-000000000905",
              scope_name: "当前工作空间",
            },
          ],
        },
      ],
    },
  ],
  permission_groups: [
    {
      domain: "operations",
      items: [
        {
          permission_code: "operations.records.read",
          resource_type: "operations_record",
          action: "read",
          allowed_scope_types: ["workspace"],
          fields: [
            { field_name: "actor_id", security_level: "CONFIDENTIAL" },
            { field_name: "attributes", security_level: "RESTRICTED" },
          ],
        },
      ],
    },
  ],
};

const audit: AuditRecord = {
  audit_id: AUDIT_ID,
  workspace_id: "20000000-0000-4000-8000-000000000905",
  actor_id: "00000000-0000-0000-0000-000000000000",
  actor_id_masked: true,
  user_id: null,
  action: "role.permissions.replace",
  resource_type: "role",
  resource_id: ROLE_ID,
  outcome: "succeeded",
  occurred_at: "2026-09-03T08:30:00Z",
  request_id: "62000000-0000-4000-8000-000000000905",
  trace_id: "a".repeat(32),
  permission_code: "authorization.role_permission.manage",
  policy_decision_id: null,
  policy_version: 7,
};

const detail: AuditRecordDetail = {
  ...audit,
  attributes: { reason_code: "SYNTHETIC", token: "[已脱敏]" },
};

const completedExport: AuditExport = {
  audit_export_request_id: "63000000-0000-4000-8000-000000000905",
  workspace_id: audit.workspace_id,
  idempotency_key: "synthetic-export-0001",
  actor_id: null,
  action: audit.action,
  resource_type: audit.resource_type,
  outcome: "succeeded",
  occurred_from: null,
  occurred_to: "2026-09-03T08:30:00Z",
  status: "completed",
  attempt_count: 1,
  last_error_code: null,
  row_count: 1,
  result_sha256: "b".repeat(64),
  result_summary: "已生成 1 条脱敏审计记录的安全摘要",
  created_at: "2026-09-03T08:30:00Z",
  updated_at: "2026-09-03T08:31:00Z",
  completed_at: "2026-09-03T08:31:00Z",
};

function renderWithApp(node: React.ReactNode) {
  return render(<AntdApp>{node}</AntdApp>);
}

describe("P6B-05 权限与审计面板", () => {
  afterEach(cleanup);

  it("新增权限后使用可信角色版本整体保存", () => {
    const onSave = vi.fn();
    renderWithApp(
      <RoleGovernancePanel snapshot={governance} canManage saving={false} onSave={onSave} />,
    );

    expect(screen.getByText("合成研发成员")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: /operations.records.read/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存角色权限" }));

    expect(onSave).toHaveBeenCalledWith(
      ROLE_ID,
      7,
      expect.arrayContaining([
        expect.objectContaining({
          permission_code: "operations.records.read",
          scope_type: "workspace",
          maximum_security_level: "RESTRICTED",
        }),
      ]),
    );
  });

  it("没有管理权限时角色矩阵只读", () => {
    const onSave = vi.fn();
    renderWithApp(
      <RoleGovernancePanel
        snapshot={governance}
        canManage={false}
        saving={false}
        onSave={onSave}
      />,
    );

    expect(screen.getByText("当前角色只读")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存角色权限" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /operations.records.read/ })).toBeDisabled();
  });

  it("展示字段裁剪、脱敏详情和导出结果，并转发筛选与导出动作", () => {
    const onFilter = vi.fn();
    const onSelect = vi.fn();
    const onExport = vi.fn();
    renderWithApp(
      <AuditGovernancePanel
        filters={{ action: audit.action }}
        records={[audit]}
        exports={[completedExport]}
        detail={detail}
        detailLoading={false}
        selectedAuditId={AUDIT_ID}
        exporting={false}
        onFilter={onFilter}
        onReset={vi.fn()}
        onSelect={onSelect}
        onExport={onExport}
      />,
    );

    expect(screen.getByText("受字段权限保护")).toBeInTheDocument();
    expect(screen.getByText("[已脱敏]", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("已生成 1 条脱敏审计记录的安全摘要")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("资源类型"), { target: { value: "document" } });
    fireEvent.click(screen.getByRole("button", { name: /创建脱敏导出/ }));
    fireEvent.click(screen.getByText("role.permissions.replace", { selector: "td" }));

    expect(onFilter).toHaveBeenCalledWith("resource", "document");
    expect(onExport).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(AUDIT_ID);
  });
});
