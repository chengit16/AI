/** @description P6B-02 企业团队管理统一产品页。 */
import { Button, Skeleton } from "antd";
import { Network } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { errorMessage, PlatformApiError } from "@/api/client";
import type { TeamMember } from "@/api/services/teamManagement";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { pageRoutes } from "@/config/resources";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

import { AuditTimeline } from "./components/AuditTimeline";
import { InvitationPanel } from "./components/InvitationPanel";
import { MemberEditor } from "./components/MemberEditor";
import { MemberFilters } from "./components/MemberFilters";
import { MemberTable } from "./components/MemberTable";
import { TeamSummary } from "./components/TeamSummary";
import { filterMembers } from "./teamUtils";
import { useTeamFilters } from "./useTeamFilters";
import { useTeamManagement } from "./useTeamManagement";

/** 组合团队聚合、筛选和高风险治理动作，后端仍是唯一安全边界。 */
export default function WorkspaceMembersPage() {
  const { workspaceId, workspaces, currentWorkspace } = useCurrentWorkspace();
  const isEnterprise = currentWorkspace?.workspace_type === "enterprise";
  const management = useTeamManagement({ workspaceId, enabled: Boolean(isEnterprise) });
  const { filters, setFilter, resetFilters } = useTeamFilters();
  const [editingMember, setEditingMember] = useState<TeamMember | null>(null);

  if (workspaces.isLoading || (isEnterprise && management.team.isLoading)) {
    return <Skeleton active paragraph={{ rows: 12 }} />;
  }
  if (workspaces.isError || !currentWorkspace) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="空间信息未能加载"
        description={errorMessage(workspaces.error)}
      />
    );
  }
  if (!isEnterprise) {
    return (
      <StateView
        kind="empty"
        headingLevel={1}
        title="个人空间无需团队治理"
        description="邀请、部门、岗位和角色管理仅在企业空间中启用。"
      />
    );
  }
  if (
    management.team.error instanceof PlatformApiError &&
    management.team.error.code === "POLICY_DENIED"
  ) {
    return (
      <StateView
        kind="denied"
        headingLevel={1}
        title="当前账号没有团队治理权限"
        description="团队成员、邀请和治理审计仅向获授权的企业所有者开放。"
      />
    );
  }
  if (management.team.isError || !management.team.data) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="团队数据未能加载"
        description={errorMessage(management.team.error)}
        action={<Button onClick={() => void management.team.refetch()}>重新加载</Button>}
      />
    );
  }

  const snapshot = management.team.data;
  const members = filterMembers(snapshot.members, filters);
  const lifecyclePending =
    management.disableMember.isPending ||
    management.activateMember.isPending ||
    management.removeMember.isPending;
  return (
    <>
      <PageHeader
        eyebrow="TEAM MANAGEMENT"
        title="团队管理"
        description="在一处治理成员生命周期、组织归属、岗位、直接角色和邀请状态，并解释每个成员的有效角色来源。"
        actions={
          <Link to={pageRoutes.WorkspaceOrganizationPage}>
            <Button icon={<Network size={16} />}>维护组织结构</Button>
          </Link>
        }
      />
      <TeamSummary snapshot={snapshot} />
      <section
        className="ui-surface-panel mt-5 overflow-hidden"
        aria-labelledby="member-list-title"
      >
        <div className="px-6 pt-5">
          <h2 id="member-list-title" className="m-0 text-[17px] text-text-strong">
            成员治理
          </h2>
          <p className="mb-4 mt-1 text-xs text-text-muted">
            显示 {members.length} / {snapshot.members.length} 名成员；最后活跃来自当前企业审计事实。
          </p>
        </div>
        <MemberFilters
          snapshot={snapshot}
          filters={filters}
          onChange={setFilter}
          onReset={resetFilters}
        />
        <MemberTable
          snapshot={snapshot}
          members={members}
          pending={lifecyclePending}
          onEdit={setEditingMember}
          onDisable={(accountId) => management.disableMember.mutate(accountId)}
          onActivate={(accountId) => management.activateMember.mutate(accountId)}
          onRemove={(accountId) => management.removeMember.mutate(accountId)}
        />
      </section>
      <div className="mt-5 grid grid-cols-[minmax(0,1.25fr)_minmax(300px,0.75fr)] gap-5 tablet-down:grid-cols-1">
        <InvitationPanel
          invitations={snapshot.invitations}
          pending={management.invite.isPending || management.cancelInvitation.isPending}
          onInvite={(loginName) => management.invite.mutate(loginName)}
          onCancel={(invitationId) => management.cancelInvitation.mutate(invitationId)}
        />
        <AuditTimeline audits={snapshot.recent_audits} />
      </div>
      <MemberEditor
        member={editingMember}
        snapshot={snapshot}
        pending={management.updateMember.isPending}
        onClose={() => setEditingMember(null)}
        onSubmit={(values) => {
          if (!editingMember) return;
          management.updateMember.mutate(
            {
              member: editingMember,
              body: {
                expected_version: editingMember.version,
                department_ids: values.departmentIds ?? [],
                primary_department_id: values.primaryDepartmentId ?? null,
                position_ids: values.positionIds ?? [],
                direct_role_ids: values.directRoleIds ?? [],
              },
            },
            { onSuccess: () => setEditingMember(null) },
          );
        }}
      />
    </>
  );
}
