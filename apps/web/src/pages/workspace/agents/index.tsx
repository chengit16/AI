/**
 * @description Agent 控制台页面编排
 *
 * 负责 URL 选择、动态菜单权限和 Agent 发布流水视图组合；
 * 候选状态、测试结论、审批终态与 Release 均以服务端查询为事实来源。
 */
import { Button, Tabs } from "antd";
import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { errorMessage } from "@/api/client";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import { AgentDialogs } from "./components/AgentDialogs";
import { AgentDraftEditor } from "./components/AgentDraftEditor";
import { AgentRail } from "./components/AgentRail";
import { AgentReleaseList } from "./components/AgentReleaseList";
import { AgentReleasePipeline } from "./components/AgentReleasePipeline";
import { useAgentControl } from "./useAgentControl";

type AgentView = "draft" | "pipeline" | "releases";

function resolveView(value: string | null): AgentView {
  return value === "pipeline" || value === "releases" ? value : "draft";
}

/** 展示 Agent 草稿、测试审批流水和不可变 Release 历史。 */
export default function AgentControlPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("agent");
  const view = resolveView(searchParams.get("view"));
  const [createOpen, setCreateOpen] = useState(false);
  const model = useAgentControl(selectedId);
  const { visiblePermissionCodes } = useWorkspaceMenuNavigation();
  const has = (code: string) => visiblePermissionCodes.has(code);

  // 1. Agent 选择和 Tab 都进入 URL；首次加载只选择服务端当前返回的第一项。
  const updateParams = (values: { agent?: string; view?: AgentView }) => {
    const next = new URLSearchParams(searchParams);
    if (values.agent) next.set("agent", values.agent);
    if (values.view) next.set("view", values.view);
    setSearchParams(next);
  };
  const agents = useMemo(
    () => (model.agents.isError ? [] : (model.agents.data ?? [])),
    [model.agents.data, model.agents.isError],
  );
  const selected = agents.find((item) => item.agent.agent_id === selectedId) ?? null;
  useEffect(() => {
    if (!selectedId && agents[0]) {
      setSearchParams({ agent: agents[0].agent.agent_id, view }, { replace: true });
    }
  }, [agents, selectedId, setSearchParams, view]);

  if (model.agents.isError) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="Agent 控制台未能加载"
        description={errorMessage(model.agents.error)}
        action={<Button onClick={() => void model.agents.refetch()}>重新加载</Button>}
      />
    );
  }

  // 2. 候选或 Release 查询失败时只呈现错误状态，不继续传递旧缓存中的敏感事实。
  const workspace = selected ? (
    <Tabs
      activeKey={view}
      onChange={(next) => updateParams({ view: next as AgentView })}
      items={[
        {
          key: "draft",
          label: "草稿配置",
          children: (
            <AgentDraftEditor
              key={`${selected.agent.agent_id}-${selected.draft.revision}`}
              detail={selected}
              canUpdate={has("agent.definition.update")}
              canArchive={has("agent.definition.archive")}
              isMutating={model.saveDraft.isPending || model.archive.isPending}
              onSave={(configuration, expectedRevision) =>
                model.saveDraft.mutate({ configuration, expectedRevision })
              }
              onArchive={(expectedVersion) => model.archive.mutate(expectedVersion)}
            />
          ),
        },
        {
          key: "pipeline",
          label: `发布流水 ${model.candidates.isError ? 0 : (model.candidates.data?.length ?? 0)}`,
          children: model.candidates.isError ? (
            <StateView
              kind="error"
              title="发布流水未能加载"
              description="旧候选、测试和审批状态已清空，请恢复权限或服务后重新加载。"
              action={<Button onClick={() => void model.candidates.refetch()}>重新加载</Button>}
            />
          ) : (
            <AgentReleasePipeline
              items={model.candidates.data ?? []}
              draftRevision={selected.draft.revision}
              isAgentActive={selected.agent.status === "active"}
              isLoading={model.candidates.isLoading}
              canRequest={has("agent.release.request")}
              canEvaluate={has("agent.test.execute")}
              canRequestApproval={has("agent.release.approve")}
              canPublish={has("agent.release.publish")}
              isMutating={
                model.requestRelease.isPending ||
                model.evaluate.isPending ||
                model.requestApproval.isPending ||
                model.publish.isPending
              }
              onRequest={(revision) => model.requestRelease.mutate(revision)}
              onEvaluate={(candidateId) => model.evaluate.mutate(candidateId)}
              onRequestApproval={(candidateId) => model.requestApproval.mutate(candidateId)}
              onPublish={(candidateId) => model.publish.mutate(candidateId)}
            />
          ),
        },
        {
          key: "releases",
          label: `Release ${model.releases.isError ? 0 : (model.releases.data?.length ?? 0)}`,
          children: model.releases.isError ? (
            <StateView
              kind="error"
              title="Release 历史未能加载"
              description="旧发布摘要已清空，请恢复权限或服务后重新加载。"
              action={<Button onClick={() => void model.releases.refetch()}>重新加载</Button>}
            />
          ) : (
            <AgentReleaseList
              items={model.releases.data ?? []}
              isLoading={model.releases.isLoading}
            />
          ),
        },
      ]}
    />
  ) : (
    <StateView
      kind="empty"
      title="选择或创建一个 Agent"
      description="Agent 创建后先维护草稿，再依次完成测试、审批和发布。"
    />
  );

  // 3. 页面只组合授权后的主操作和主从视图，所有接口继续由后端 PDP 独立授权。
  return (
    <>
      <PageHeader
        eyebrow="AGENT CONTROL"
        title="Agent 控制台"
        description="管理完整草稿配置，并通过固定测试、审批和不可变发布流水交付 Agent。"
        actions={
          has("agent.definition.create") ? (
            <Button type="primary" icon={<Plus size={17} />} onClick={() => setCreateOpen(true)}>
              创建 Agent
            </Button>
          ) : undefined
        }
      />
      <div className="ui-surface-panel grid min-h-[680px] grid-cols-[minmax(220px,280px)_minmax(0,1fr)] overflow-hidden tablet-down:grid-cols-1">
        <AgentRail
          items={agents}
          selectedId={selectedId}
          isLoading={model.agents.isLoading}
          onSelect={(agent) => updateParams({ agent })}
        />
        <section className="min-w-0 p-5 phone-down:p-3" aria-label="Agent 当前视图">
          {workspace}
        </section>
      </div>
      <AgentDialogs
        open={createOpen}
        isCreating={model.create.isPending}
        onClose={() => setCreateOpen(false)}
        onCreate={async (body) => {
          const created = await model.create.mutateAsync(body);
          updateParams({ agent: created.agent.agent_id, view: "draft" });
        }}
      />
    </>
  );
}
