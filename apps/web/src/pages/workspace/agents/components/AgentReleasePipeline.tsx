/** @description Agent 候选测试、审批和发布流水展示与受控动作。 */
import { Button, Empty, Skeleton, Table, Tag } from "antd";
import { ClipboardCheck, FlaskConical, GitPullRequestCreate, Rocket } from "lucide-react";

import type { AgentCandidateControl } from "@/api/services/agents";

import {
  candidateNextAction,
  candidateStatus,
  evaluationCheckLabel,
  formatAgentTime,
} from "../config";

/** 候选流水数据、权限和状态机命令。 */
export interface AgentReleasePipelineProps {
  /** 当前 Agent 的候选、最近评估与审批聚合。 */
  items: readonly AgentCandidateControl[];
  /** 当前草稿 revision，用于申请新候选。 */
  draftRevision: number;
  /** 当前 Agent 是否仍可产生新候选。 */
  isAgentActive: boolean;
  /** 候选查询首次加载状态。 */
  isLoading: boolean;
  /** 只控制申请候选按钮，后端仍复核草稿和资源范围。 */
  canRequest: boolean;
  /** 只控制测试按钮展示。 */
  canEvaluate: boolean;
  /** 只控制发起审批按钮展示。 */
  canRequestApproval: boolean;
  /** 只控制发布按钮展示。 */
  canPublish: boolean;
  /** 任一候选命令正在提交。 */
  isMutating: boolean;
  /** 冻结当前草稿为候选。 */
  onRequest: (expectedRevision: number) => void;
  /** 执行固定五类确定性测试。 */
  onEvaluate: (candidateId: string) => void;
  /** 发起个人所有者确认或企业多级审批。 */
  onRequestApproval: (candidateId: string) => void;
  /** 固化已经通过测试与审批的不可变 Release。 */
  onPublish: (candidateId: string) => void;
}

function statusTag(status: string) {
  const display = candidateStatus[status as keyof typeof candidateStatus] ?? {
    label: status,
    color: "default",
  };
  return <Tag color={display.color}>{display.label}</Tag>;
}

/** 展示严格按“候选、测试、审批、发布”推进的流水，不能在页面跳过后端门禁。 */
export function AgentReleasePipeline(props: AgentReleasePipelineProps) {
  if (props.isLoading) return <Skeleton active paragraph={{ rows: 10 }} />;
  return (
    <section aria-labelledby="agent-pipeline-title">
      <div className="mb-4 flex items-start justify-between gap-4 nav-mobile:flex-col">
        <div>
          <h2 id="agent-pipeline-title" className="m-0 text-xl text-text-strong">
            发布流水
          </h2>
          <p className="mb-0 mt-2 text-sm text-text-muted">
            测试只证明 core_functional，不代表真实模型质量、供应商兼容性或容量结论。
          </p>
        </div>
        {props.canRequest && props.isAgentActive && (
          <Button
            type="primary"
            icon={<GitPullRequestCreate size={16} />}
            loading={props.isMutating}
            onClick={() => props.onRequest(props.draftRevision)}
          >
            申请发布
          </Button>
        )}
      </div>
      {props.items.length === 0 ? (
        <Empty description="还没有发布候选" />
      ) : (
        <Table<AgentCandidateControl>
          rowKey={(item) => item.candidate.candidate_id}
          dataSource={[...props.items]}
          pagination={{ pageSize: 8, hideOnSinglePage: true }}
          scroll={{ x: 960 }}
          expandable={{
            rowExpandable: (item) => Boolean(item.evaluation),
            expandedRowRender: (item) => (
              <div className="flex flex-wrap gap-2 py-2">
                {item.evaluation?.checks.map((check) => (
                  <Tag
                    key={check.check_code}
                    color={check.status === "passed" ? "success" : "error"}
                  >
                    {evaluationCheckLabel[check.check_code] ?? check.check_code}{" "}
                    {check.passed_count}/{check.case_count} · {(check.score_bps / 100).toFixed(0)}%
                  </Tag>
                ))}
              </div>
            ),
          }}
          columns={[
            {
              title: "状态",
              dataIndex: ["candidate", "status"],
              width: 130,
              render: statusTag,
            },
            {
              title: "草稿",
              key: "revision",
              width: 100,
              render: (_, item) => `r${item.candidate.draft_revision}`,
            },
            {
              title: "测试",
              key: "evaluation",
              width: 190,
              render: (_, item) =>
                item.evaluation ? (
                  <span className="flex items-center gap-2">
                    <Tag color={item.evaluation.status === "passed" ? "success" : "error"}>
                      {item.evaluation.status === "passed" ? "通过" : "未通过"}
                    </Tag>
                    <span className="text-xs text-text-muted">
                      {item.evaluation.passed_cases}/{item.evaluation.total_cases}
                    </span>
                  </span>
                ) : (
                  <span className="text-text-muted">未执行</span>
                ),
            },
            {
              title: "审批",
              key: "approval",
              width: 170,
              render: (_, item) =>
                item.approval ? (
                  <span className="flex flex-col gap-1">
                    <Tag color={item.approval.status === "approved" ? "success" : "processing"}>
                      {item.approval.status}
                    </Tag>
                    <span className="text-xs text-text-muted">
                      {item.approval.personal_owner_confirmation
                        ? "个人所有者确认"
                        : "企业多级审批"}
                    </span>
                  </span>
                ) : (
                  <span className="text-text-muted">未发起</span>
                ),
            },
            {
              title: "更新时间",
              dataIndex: ["candidate", "updated_at"],
              width: 150,
              render: formatAgentTime,
            },
            {
              title: "下一步",
              key: "actions",
              fixed: "right",
              width: 190,
              render: (_, item) => {
                const action = candidateNextAction(item);
                if (action === "evaluate" && props.canEvaluate) {
                  return (
                    <Button
                      type="link"
                      icon={<FlaskConical size={15} />}
                      loading={props.isMutating}
                      onClick={() => props.onEvaluate(item.candidate.candidate_id)}
                    >
                      执行测试
                    </Button>
                  );
                }
                if (action === "approve" && props.canRequestApproval) {
                  return (
                    <Button
                      type="link"
                      icon={<ClipboardCheck size={15} />}
                      loading={props.isMutating}
                      onClick={() => props.onRequestApproval(item.candidate.candidate_id)}
                    >
                      发起审批
                    </Button>
                  );
                }
                if (action === "publish" && props.canPublish) {
                  return (
                    <Button
                      type="link"
                      icon={<Rocket size={15} />}
                      loading={props.isMutating}
                      onClick={() => props.onPublish(item.candidate.candidate_id)}
                    >
                      发布 Release
                    </Button>
                  );
                }
                if (item.candidate.status === "approval_pending") {
                  return (
                    <Button type="link" href="/workspace/workflows?view=approvals">
                      前往审批待办
                    </Button>
                  );
                }
                return <span className="text-xs text-text-muted">无可执行动作</span>;
              },
            },
          ]}
        />
      )}
    </section>
  );
}
