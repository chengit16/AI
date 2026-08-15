/**
 * @description 工作流与审批工作台页面编排
 *
 * 组合设计、运行、待办和策略四个视图；菜单只裁剪体验入口，后端 PDP 仍独立授权。
 */
import { Button, Skeleton, Tabs } from "antd";
import { Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { errorMessage } from "@/api/client";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";
import { useSessionStore } from "@/store/session";

import { ApprovalInbox } from "./components/ApprovalInbox";
import { ApprovalPolicyPanel } from "./components/ApprovalPolicyPanel";
import { WorkflowDialogs } from "./components/WorkflowDialogs";
import { WorkflowGraphEditor } from "./components/WorkflowGraphEditor";
import { WorkflowListRail } from "./components/WorkflowListRail";
import { WorkflowRunTable } from "./components/WorkflowRunTable";
import { useApprovalOperations } from "./useApprovalOperations";
import { useWorkflowOperations } from "./useWorkflowOperations";

type WorkbenchView = "design" | "runs" | "approvals" | "policies";

/** 读取受控视图参数，未知值稳定回退到工作流设计。 */
function currentView(value: string | null): WorkbenchView {
  return value === "runs" || value === "approvals" || value === "policies" ? value : "design";
}

/** 展示当前空间的工作流设计、运行监控、参与者待办和审批策略。 */
export default function WorkflowDesignPage() {
  // 长函数保留原因：四个页签共享 URL 选择、菜单权限和对话框状态，视图实现已拆到独立组件。
  // 1. 先从 URL、会话和菜单发布快照恢复整个工作台的稳定上下文。
  const [searchParams, setSearchParams] = useSearchParams();
  const view = currentView(searchParams.get("view"));
  const selectedWorkflowId = searchParams.get("workflow");
  const selectedPolicyId = searchParams.get("policy");
  const [createOpen, setCreateOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const accountId = useSessionStore((state) => state.accountId);
  const { visiblePermissionCodes } = useWorkspaceMenuNavigation();
  const has = (permissionCode: string) => visiblePermissionCodes.has(permissionCode);
  const updateParams = useCallback(
    (updates: Record<string, string | null>) => {
      const next = new URLSearchParams(searchParams);
      Object.entries(updates).forEach(([key, value]) => {
        if (value === null) next.delete(key);
        else next.set(key, value);
      });
      setSearchParams(next);
    },
    [searchParams, setSearchParams],
  );
  const workflows = useWorkflowOperations({
    selectedWorkflowId,
    onWorkflowCreated: (workflowId) => updateParams({ workflow: workflowId, view: "design" }),
  });
  const approvals = useApprovalOperations({
    selectedPolicyId,
    onPolicyCreated: (policyId) => updateParams({ policy: policyId, view: "policies" }),
  });

  // 2. 列表首次到达时选择第一项，URL 仍是刷新和深链恢复的唯一页面选择事实。
  useEffect(() => {
    if (!selectedWorkflowId && workflows.workflows.data?.[0]) {
      updateParams({ workflow: workflows.workflows.data[0].workflow_id });
    }
  }, [selectedWorkflowId, updateParams, workflows.workflows.data]);
  useEffect(() => {
    if (!selectedPolicyId && approvals.policies.data?.[0]) {
      updateParams({ policy: approvals.policies.data[0].approval_policy_id });
    }
  }, [approvals.policies.data, selectedPolicyId, updateParams]);

  if (workflows.workflows.isError) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="工作流工作台未能加载"
        description={errorMessage(workflows.workflows.error)}
        action={<Button onClick={() => void workflows.workflows.refetch()}>重新加载</Button>}
      />
    );
  }

  // 3. 最后组合设计/运行与审批/策略两组视图，具体业务交互由子组件承担。
  const workflowArea = (
    <div className="ui-surface-panel grid min-h-[640px] grid-cols-[minmax(220px,280px)_minmax(0,1fr)] overflow-hidden tablet-down:grid-cols-1">
      <WorkflowListRail
        items={workflows.workflows.data ?? []}
        selectedId={selectedWorkflowId}
        isLoading={workflows.workflows.isLoading}
        canCreate={has("workflow.definition.create")}
        onCreate={() => setCreateOpen(true)}
        onSelect={(workflowId) => updateParams({ workflow: workflowId })}
      />
      <section className="min-w-0 p-5 phone-down:p-3" aria-label="工作流当前视图">
        {!selectedWorkflowId ? (
          <StateView
            kind="empty"
            title="选择一个工作流"
            description="当前授权范围内还没有可查看的工作流。"
          />
        ) : workflows.detail.isLoading ? (
          <Skeleton active paragraph={{ rows: 12 }} />
        ) : workflows.detail.isError ? (
          <StateView
            kind="error"
            title="工作流详情未能加载"
            description={errorMessage(workflows.detail.error)}
          />
        ) : workflows.detail.data && view === "design" ? (
          <WorkflowGraphEditor
            key={`${workflows.detail.data.workflow.workflow_id}-${workflows.detail.data.draft.revision}`}
            detail={workflows.detail.data}
            canUpdate={has("workflow.definition.update")}
            canPublish={has("workflow.definition.publish")}
            isMutating={
              workflows.saveDraft.isPending ||
              workflows.validateDraft.isPending ||
              workflows.publish.isPending
            }
            onSave={(graph, revision) => workflows.saveDraft.mutate({ graph, revision })}
            onValidate={() => workflows.validateDraft.mutate()}
            onPublish={(revision) => workflows.publish.mutate(revision)}
          />
        ) : (
          <WorkflowRunTable
            items={workflows.runs.data ?? []}
            isLoading={workflows.runs.isLoading}
            hasPublication={Boolean(workflows.detail.data?.publication)}
            canRun={has("workflow.run.create")}
            onRun={() => setRunOpen(true)}
          />
        )}
      </section>
    </div>
  );

  const approvalArea =
    view === "approvals" ? (
      approvals.instances.isError ? (
        <StateView
          kind="error"
          title="审批待办未能加载"
          description={errorMessage(approvals.instances.error)}
        />
      ) : (
        <section className="ui-surface-panel min-h-[540px] p-5 phone-down:p-3">
          <ApprovalInbox
            items={approvals.instances.data ?? []}
            accountId={accountId}
            isLoading={approvals.instances.isLoading}
            permissions={visiblePermissionCodes}
            isMutating={approvals.act.isPending || approvals.transfer.isPending}
            onAct={(instanceId, action, reasonCode) =>
              approvals.act.mutate({ instanceId, action, reasonCode })
            }
            onTransfer={(instanceId, targetAccountId) =>
              approvals.transfer.mutate({ instanceId, targetAccountId })
            }
          />
        </section>
      )
    ) : (
      <section className="ui-surface-panel min-h-[540px] p-5 phone-down:p-3">
        <ApprovalPolicyPanel
          policies={approvals.policies.data ?? []}
          selectedId={selectedPolicyId}
          detail={approvals.policyDetail.data}
          isLoading={approvals.policies.isLoading || approvals.policyDetail.isLoading}
          canCreate={has("approval.policy.create")}
          canUpdate={has("approval.policy.update")}
          isMutating={approvals.createPolicy.isPending || approvals.revisePolicy.isPending}
          onSelect={(policyId) => updateParams({ policy: policyId })}
          onCreate={(body) => approvals.createPolicy.mutate(body)}
          onRevise={(definition, version) => approvals.revisePolicy.mutate({ definition, version })}
        />
      </section>
    );

  return (
    <>
      <PageHeader
        eyebrow="AUTOMATION"
        title="工作流与审批"
        description="设计受限工作流、发布不可变版本，并处理运行中的多级审批。"
        actions={
          has("workflow.definition.create") ? (
            <Button type="primary" icon={<Plus size={17} />} onClick={() => setCreateOpen(true)}>
              创建工作流
            </Button>
          ) : undefined
        }
      />
      <Tabs
        activeKey={view}
        onChange={(next) => updateParams({ view: next })}
        items={[
          { key: "design", label: "工作流设计", children: workflowArea },
          { key: "runs", label: "运行监控", children: workflowArea },
          {
            key: "approvals",
            label: `审批待办 ${approvals.instances.data?.filter((item) => item.status === "pending").length ?? 0}`,
            children: approvalArea,
          },
          { key: "policies", label: "审批策略", children: approvalArea },
        ]}
      />
      <WorkflowDialogs
        createOpen={createOpen}
        runOpen={runOpen}
        isCreating={workflows.create.isPending}
        isRunning={workflows.run.isPending}
        onCloseCreate={() => setCreateOpen(false)}
        onCloseRun={() => setRunOpen(false)}
        onCreate={workflows.create.mutateAsync}
        onRun={workflows.run.mutateAsync}
      />
    </>
  );
}
