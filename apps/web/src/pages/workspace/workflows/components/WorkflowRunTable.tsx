/** @description 工作流最近运行、预算用量、错误码和输入输出查看组件。 */
import { Button, Descriptions, Drawer, Empty, Skeleton, Table, Tag } from "antd";
import { Eye, Play } from "lucide-react";
import { useState } from "react";

import type { WorkflowRun } from "@/api/services/workflows";

import { formatWorkflowTime, runStatus } from "../config";

/** 运行列表的服务端事实和权限化启动入口。 */
export interface WorkflowRunTableProps {
  /** 当前工作流最近运行，按创建时间倒序。 */
  items: readonly WorkflowRun[];
  /** 运行列表首次加载状态。 */
  isLoading: boolean;
  /** 是否存在当前发布版本。 */
  hasPublication: boolean;
  /** 是否展示启动运行入口。 */
  canRun: boolean;
  /** 打开运行输入对话框。 */
  onRun: () => void;
}

/** 展示可刷新恢复的运行历史，并在抽屉中查看字段投影后的输入输出。 */
export function WorkflowRunTable(props: WorkflowRunTableProps) {
  const [selectedRun, setSelectedRun] = useState<WorkflowRun | null>(null);
  if (props.isLoading) return <Skeleton active paragraph={{ rows: 8 }} />;
  return (
    <>
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h2 className="m-0 text-lg text-text-strong">运行监控</h2>
          <p className="mb-0 mt-1 text-sm text-text-muted">排队、执行和审批等待状态会自动刷新。</p>
        </div>
        {props.canRun && (
          <Button
            type="primary"
            icon={<Play size={16} />}
            disabled={!props.hasPublication}
            onClick={props.onRun}
          >
            启动运行
          </Button>
        )}
      </div>
      {props.items.length === 0 ? (
        <Empty description={props.hasPublication ? "暂无运行记录" : "发布工作流后可启动运行"} />
      ) : (
        <Table<WorkflowRun>
          rowKey="workflow_run_id"
          dataSource={[...props.items]}
          pagination={false}
          scroll={{ x: 760 }}
          columns={[
            {
              title: "状态",
              dataIndex: "status",
              width: 120,
              render: (status: WorkflowRun["status"]) => (
                <Tag color={runStatus[status].color}>{runStatus[status].label}</Tag>
              ),
            },
            {
              title: "节点 / 模型 / 检索",
              key: "budget",
              width: 170,
              render: (_, run) =>
                `${run.steps_executed} / ${run.model_calls} / ${run.retrieval_calls}`,
            },
            {
              title: "开始时间",
              dataIndex: "created_at",
              width: 150,
              render: formatWorkflowTime,
            },
            {
              title: "错误码",
              dataIndex: "error_code",
              ellipsis: true,
              render: (value: string | null) => value ?? "-",
            },
            {
              title: "操作",
              key: "actions",
              fixed: "right",
              width: 88,
              render: (_, run) => (
                <Button type="link" icon={<Eye size={15} />} onClick={() => setSelectedRun(run)}>
                  查看
                </Button>
              ),
            },
          ]}
        />
      )}
      <Drawer
        title="运行事实"
        width={560}
        open={Boolean(selectedRun)}
        onClose={() => setSelectedRun(null)}
      >
        {selectedRun && (
          <div className="flex flex-col gap-5">
            <Descriptions
              size="small"
              column={1}
              items={[
                { key: "id", label: "运行 ID", children: selectedRun.workflow_run_id },
                { key: "executor", label: "执行器", children: selectedRun.executor_version ?? "-" },
                {
                  key: "updated",
                  label: "更新时间",
                  children: formatWorkflowTime(selectedRun.updated_at),
                },
                { key: "bytes", label: "输出字节", children: selectedRun.output_bytes },
              ]}
            />
            <section>
              <h3 className="text-sm text-text-strong">输入</h3>
              <pre className="max-h-[220px] overflow-auto rounded-ui bg-surface-subtle p-3 text-xs">
                {selectedRun.input_payload === null
                  ? "字段权限已隐藏"
                  : JSON.stringify(selectedRun.input_payload, null, 2)}
              </pre>
            </section>
            <section>
              <h3 className="text-sm text-text-strong">输出</h3>
              <pre className="max-h-[220px] overflow-auto rounded-ui bg-surface-subtle p-3 text-xs">
                {selectedRun.output_payload === null
                  ? "字段权限已隐藏或尚未产生输出"
                  : JSON.stringify(selectedRun.output_payload, null, 2)}
              </pre>
            </section>
          </div>
        )}
      </Drawer>
    </>
  );
}
