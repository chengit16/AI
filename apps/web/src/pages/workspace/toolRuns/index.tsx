/** @description 工具任务历史、进度恢复、确认审批与取消控制页面。 */
import { Alert, Button, Descriptions, Popconfirm, Progress, Skeleton, Tag, Typography } from "antd";
import { Ban, Check, RefreshCw, X } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router";

import type { ToolRunDetail, ToolRunSummary } from "@/api/services/tools";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import { useToolRunHistory } from "./useToolRunHistory";

const TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "timed_out"]);

/** 展示当前授权资源范围内的 Run，并使选择进入 URL。 */
export default function ToolRunHistoryPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedRunId = searchParams.get("run");
  const model = useToolRunHistory(selectedRunId);
  const { visiblePermissionCodes } = useWorkspaceMenuNavigation();
  const runs = useMemo(
    () => (model.runs.isError ? [] : (model.runs.data ?? [])),
    [model.runs.data, model.runs.isError],
  );

  useEffect(() => {
    if (!selectedRunId && runs[0]) {
      setSearchParams({ run: runs[0].run_id }, { replace: true });
    }
  }, [runs, selectedRunId, setSearchParams]);

  if (model.runs.isError) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="工具任务历史未能加载"
        description="服务端未返回可见 Run，页面不会保留上一次历史快照。"
        action={<Button onClick={() => void model.runs.refetch()}>重新加载</Button>}
      />
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="RUN OPERATIONS"
        title="工具任务"
        description="查看脱敏执行事实、恢复实时进度，并处理当前有权响应的确认与取消。"
        actions={
          <Button
            icon={<RefreshCw size={16} />}
            onClick={() => void Promise.all([model.runs.refetch(), model.detailQuery.refetch()])}
          >
            刷新
          </Button>
        }
      />
      <div className="ui-surface-panel grid min-h-[680px] grid-cols-[minmax(240px,320px)_minmax(0,1fr)] overflow-hidden tablet-down:grid-cols-1">
        <RunRail
          runs={runs}
          selectedId={selectedRunId}
          isLoading={model.runs.isLoading}
          onSelect={(runId) => setSearchParams({ run: runId })}
        />
        <main className="min-w-0 border-l border-solid border-[var(--ui-border)] p-5 tablet-down:border-l-0 tablet-down:border-t phone-down:p-3">
          {model.detailQuery.isLoading ? (
            <Skeleton active paragraph={{ rows: 12 }} />
          ) : model.detailQuery.isError ? (
            <StateView
              kind="error"
              title="任务详情未能加载"
              description="旧详情已清空，请重新加载当前任务。"
              action={<Button onClick={() => void model.detailQuery.refetch()}>重新加载</Button>}
            />
          ) : model.detail ? (
            <RunDetailPanel
              detail={model.detail}
              streamError={model.streamError}
              canRespond={visiblePermissionCodes.has("tool.confirmation.respond")}
              canCancel={visiblePermissionCodes.has("tool.run.cancel")}
              isResponding={model.respond.isPending}
              isCancelling={model.cancel.isPending}
              onRespond={(confirmationId, action) =>
                model.respond.mutate({ confirmationId, action })
              }
              onCancel={() => model.cancel.mutate()}
            />
          ) : (
            <StateView
              kind="empty"
              title="选择一个工具任务"
              description="任务详情只包含状态、摘要、用量和稳定错误码。"
            />
          )}
        </main>
      </div>
    </>
  );
}

function RunRail(props: {
  runs: readonly ToolRunSummary[];
  selectedId: string | null;
  isLoading: boolean;
  onSelect: (runId: string) => void;
}) {
  if (props.isLoading)
    return (
      <aside className="p-4">
        <Skeleton active paragraph={{ rows: 8 }} />
      </aside>
    );
  return (
    <aside className="min-w-0 p-3" aria-label="工具任务历史">
      {props.runs.map((run) => (
        <button
          key={run.run_id}
          type="button"
          className={`mb-1 w-full border-0 bg-transparent p-3 text-left transition-colors ${props.selectedId === run.run_id ? "bg-[var(--ui-fill-secondary)]" : "hover:bg-[var(--ui-fill-tertiary)]"}`}
          onClick={() => props.onSelect(run.run_id)}
        >
          <span className="flex items-center justify-between gap-2">
            <span className="truncate font-medium">{run.service_name}</span>
            <RunStateTag state={run.state} />
          </span>
          <span className="mt-1 block text-xs text-[var(--ui-text-secondary)]">
            Release V{run.agent_release_version} · {formatTime(run.created_at)}
          </span>
          <Progress
            className="!mb-0 !mt-2"
            percent={
              run.step_count ? Math.round((run.completed_step_count / run.step_count) * 100) : 0
            }
            showInfo={false}
            size="small"
          />
        </button>
      ))}
      {!props.runs.length && (
        <StateView kind="empty" title="暂无工具任务" description="从工具执行页面冻结第一份计划。" />
      )}
    </aside>
  );
}

function RunDetailPanel(props: {
  detail: ToolRunDetail;
  streamError: string | null;
  canRespond: boolean;
  canCancel: boolean;
  isResponding: boolean;
  isCancelling: boolean;
  onRespond: (confirmationId: string, action: "confirm" | "reject") => void;
  onCancel: () => void;
}) {
  const { run } = props.detail;
  const canCancelRun =
    props.canCancel && !TERMINAL_STATES.has(run.state) && !run.cancel_requested_at;
  return (
    <section aria-label="工具任务详情">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Typography.Title level={2} className="!mb-1 !text-xl">
            {run.service_name}
          </Typography.Title>
          <Typography.Text type="secondary" copyable>
            {run.run_id}
          </Typography.Text>
        </div>
        <div className="flex items-center gap-2">
          <RunStateTag state={run.state} />
          {canCancelRun && (
            <Popconfirm
              title="取消此工具任务？"
              description="取消会阻止新的尝试；已在执行的 Adapter 只做尽力中止，迟到结果不会覆盖终态。"
              okText="确认取消"
              cancelText="继续执行"
              okButtonProps={{ danger: true }}
              onConfirm={props.onCancel}
            >
              <Button danger loading={props.isCancelling} icon={<Ban size={16} />}>
                取消任务
              </Button>
            </Popconfirm>
          )}
        </div>
      </div>
      {props.streamError && (
        <Alert
          className="my-4"
          type="warning"
          showIcon
          message="实时进度暂时中断"
          description={props.streamError}
        />
      )}
      <Descriptions
        className="mt-5"
        column={{ xs: 1, sm: 2, lg: 3 }}
        size="small"
        items={[
          { key: "release", label: "Release", children: `V${run.agent_release_version}` },
          {
            key: "progress",
            label: "步骤",
            children: `${run.completed_step_count} / ${run.step_count}`,
          },
          { key: "cursor", label: "进度游标", children: props.detail.latest_cursor },
          { key: "deadline", label: "截止时间", children: formatTime(run.deadline_at) },
          { key: "cost", label: "成本", children: `${run.total_cost_microunits} μ` },
          { key: "attempts", label: "单步尝试上限", children: run.max_attempts_per_step },
        ]}
      />
      <div className="mt-6 border-t border-solid border-[var(--ui-border)] pt-4">
        <Typography.Title level={3} className="!text-base">
          冻结步骤
        </Typography.Title>
        <div className="grid gap-3">
          {props.detail.steps.map((step) => (
            <article
              key={step.step_id}
              className="border border-solid border-[var(--ui-border)] p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <Typography.Text strong>
                    {step.sequence_no}. {step.display_name}
                  </Typography.Text>
                  <span className="mt-1 block break-all text-xs text-[var(--ui-text-secondary)]">
                    {step.tool_key} · V{step.tool_version} · 参数摘要{" "}
                    {step.canonical_arguments_hash.slice(0, 12)}
                  </span>
                </div>
                <Tag>{stateLabel(step.state)}</Tag>
              </div>
              <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[var(--ui-text-secondary)]">
                <span>风险 {step.risk_level}</span>
                <span>超时 {step.timeout_seconds}s</span>
                <span>
                  尝试 {step.attempts.length}/{step.max_attempts}
                </span>
              </div>
              {step.confirmation && (
                <ConfirmationActions
                  confirmation={step.confirmation}
                  canRespond={props.canRespond}
                  isPending={props.isResponding}
                  onRespond={props.onRespond}
                />
              )}
              {step.attempts.length > 0 && (
                <div className="mt-3 border-t border-solid border-[var(--ui-border)] pt-2 text-xs text-[var(--ui-text-secondary)]">
                  最近尝试：#{step.attempts.at(-1)?.attempt_no} ·{" "}
                  {stateLabel(step.attempts.at(-1)?.state ?? "unknown")} ·{" "}
                  {step.attempts.at(-1)?.error_code ?? "无稳定错误码"}
                </div>
              )}
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function ConfirmationActions(props: {
  confirmation: ToolRunDetail["steps"][number]["confirmation"] & {};
  canRespond: boolean;
  isPending: boolean;
  onRespond: (confirmationId: string, action: "confirm" | "reject") => void;
}) {
  const confirmation = props.confirmation;
  const actionable =
    props.canRespond && confirmation.can_respond && confirmation.state === "pending";
  return (
    <div className="mt-3 border-l-2 border-solid border-amber-500 bg-amber-50 p-3 text-sm dark:bg-amber-950/20">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Typography.Text strong>
            {confirmation.mode === "personal_owner" ? "个人确认" : "企业审批"}
          </Typography.Text>
          <span className="mt-1 block text-xs text-[var(--ui-text-secondary)]">
            风险 {confirmation.risk_level} · {formatTime(confirmation.expires_at)} 到期
          </span>
        </div>
        {actionable && (
          <div className="flex gap-2">
            <Popconfirm
              title="批准此工具调用？"
              description="批准后平台会重新核验当前权限；通过后可能执行已冻结参数对应的操作。"
              okText="确认批准"
              cancelText="暂不处理"
              okButtonProps={{
                danger:
                  confirmation.risk_level === "high" || confirmation.risk_level === "critical",
              }}
              onConfirm={() => props.onRespond(confirmation.confirmation_id, "confirm")}
            >
              <Button loading={props.isPending} icon={<Check size={15} />}>
                批准
              </Button>
            </Popconfirm>
            <Popconfirm
              title="驳回此工具调用？"
              description="最终驳回会关闭尚未执行的任务，且不会产生工具副作用。"
              okText="确认驳回"
              cancelText="暂不处理"
              okButtonProps={{ danger: true }}
              onConfirm={() => props.onRespond(confirmation.confirmation_id, "reject")}
            >
              <Button danger loading={props.isPending} icon={<X size={15} />}>
                驳回
              </Button>
            </Popconfirm>
          </div>
        )}
      </div>
    </div>
  );
}

function RunStateTag({ state }: { state: string }) {
  const color =
    state === "completed"
      ? "green"
      : state === "failed" || state === "timed_out"
        ? "red"
        : state === "cancelled"
          ? "default"
          : "processing";
  return <Tag color={color}>{stateLabel(state)}</Tag>;
}

function stateLabel(state: string) {
  const labels: Record<string, string> = {
    pending: "待处理",
    planning: "规划中",
    running: "执行中",
    waiting_confirmation: "等待确认",
    waiting_approval: "等待审批",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    timed_out: "已超时",
    ready: "就绪",
    executing: "执行中",
    succeeded: "成功",
    rejected: "已驳回",
  };
  return labels[state] ?? state;
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}
