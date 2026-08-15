/** @description 审批策略列表、当前不可变定义和版本创建组件。 */
import {
  Alert,
  Button,
  Empty,
  Input,
  InputNumber,
  Modal,
  Select,
  Skeleton,
  Switch,
  Table,
  Tag,
} from "antd";
import { Plus, Save } from "lucide-react";
import { useState } from "react";

import type {
  ApprovalPolicy,
  ApprovalPolicyDefinition,
  ApprovalPolicyDetail,
  CreateApprovalPolicyRequest,
} from "@/api/services/workflows";

/** 审批策略面板的查询事实、权限和写操作。 */
export interface ApprovalPolicyPanelProps {
  /** 当前授权范围内的策略身份。 */
  policies: readonly ApprovalPolicy[];
  /** 当前选中策略 ID。 */
  selectedId: string | null;
  /** 当前策略及不可变版本详情。 */
  detail: ApprovalPolicyDetail | undefined;
  /** 列表或详情加载状态。 */
  isLoading: boolean;
  /** 是否允许创建策略。 */
  canCreate: boolean;
  /** 是否允许新增策略版本。 */
  canUpdate: boolean;
  /** 任一策略写操作进行中。 */
  isMutating: boolean;
  /** 切换当前策略。 */
  onSelect: (approvalPolicyId: string) => void;
  /** 创建策略和首版本。 */
  onCreate: (body: CreateApprovalPolicyRequest) => void;
  /** 以当前身份版本号新增策略定义版本。 */
  onRevise: (definition: ApprovalPolicyDefinition, version: number) => void;
}

interface PolicyFormValue {
  name: string;
  resourceType: string;
  operation: string;
  priority: number;
  securityLevels: Array<"PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED">;
  riskLevels: Array<"normal" | "high" | "critical">;
  approverIds: string;
  mode: "any" | "all";
  reminderMinutes: number;
  timeoutMinutes: number;
  timeoutAction: "wait" | "transfer" | "escalate" | "reject";
  allowSelfApproval: boolean;
}

/** 将首期结构化表单转换为完整审批定义，服务端继续校验账号和状态。 */
function definitionFromForm(value: PolicyFormValue): ApprovalPolicyDefinition {
  const referenceIds = value.approverIds
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return {
    resource_type: value.resourceType.trim(),
    operation: value.operation.trim(),
    priority: value.priority,
    department_ids: [],
    security_levels: value.securityLevels,
    risk_levels: value.riskLevels,
    field_conditions: [],
    levels: [
      {
        sequence_no: 1,
        mode: value.mode,
        sources: [{ source_type: "accounts", reference_ids: referenceIds, levels_up: null }],
        reminder_after_minutes: value.reminderMinutes,
        timeout_after_minutes: value.timeoutMinutes,
        timeout_action: value.timeoutAction,
        fallback_sources: [],
      },
    ],
    allow_self_approval: value.allowSelfApproval,
  };
}

/** 用当前定义填充可安全修订的首层结构化表单，复杂多层策略保持只读展示。 */
function formFromDetail(detail?: ApprovalPolicyDetail): PolicyFormValue {
  const definition = detail?.version.definition;
  const level = definition?.levels[0];
  const source = level?.sources[0];
  return {
    name: detail?.policy.name ?? "",
    resourceType: definition?.resource_type ?? "document",
    operation: definition?.operation ?? "publish",
    priority: definition?.priority ?? 100,
    securityLevels: [...(definition?.security_levels ?? ["INTERNAL"])],
    riskLevels: [...(definition?.risk_levels ?? ["normal"])],
    approverIds: source?.source_type === "accounts" ? source.reference_ids.join(",") : "",
    mode: level?.mode ?? "all",
    reminderMinutes: level?.reminder_after_minutes ?? 1_440,
    timeoutMinutes: level?.timeout_after_minutes ?? 4_320,
    timeoutAction: level?.timeout_action ?? "wait",
    allowSelfApproval: definition?.allow_self_approval ?? false,
  };
}

interface PolicyEditorModalProps {
  /** 策略编辑对话框是否打开。 */
  open: boolean;
  /** 已有策略详情；缺失时表示创建模式。 */
  detail?: ApprovalPolicyDetail;
  /** 策略写操作提交状态。 */
  isMutating: boolean;
  /** 关闭对话框但不提交。 */
  onClose: () => void;
  /** 提交经过基础校验的结构化策略值。 */
  onSubmit: (value: PolicyFormValue) => void;
}

/** 编辑首期常用单层账号审批策略；已有复杂多层策略不会被降级覆盖。 */
function PolicyEditorModal(props: PolicyEditorModalProps) {
  const [value, setValue] = useState<PolicyFormValue>(() => formFromDetail(props.detail));
  const invalid =
    !value.name.trim() ||
    !value.resourceType.trim() ||
    !value.operation.trim() ||
    !value.approverIds.trim() ||
    value.reminderMinutes >= value.timeoutMinutes;
  return (
    <Modal
      title={props.detail ? "修订审批策略" : "创建审批策略"}
      width={720}
      open={props.open}
      okText="提交新版本"
      cancelText="取消"
      confirmLoading={props.isMutating}
      okButtonProps={{ disabled: invalid }}
      onOk={() => props.onSubmit(value)}
      onCancel={props.onClose}
    >
      <div className="grid grid-cols-2 gap-4 phone-down:grid-cols-1">
        <label className="text-xs font-650 text-text-muted">
          策略名称
          <Input
            className="mt-1"
            value={value.name}
            disabled={Boolean(props.detail)}
            onChange={(event) => setValue({ ...value, name: event.target.value })}
          />
        </label>
        <label className="text-xs font-650 text-text-muted">
          优先级
          <InputNumber
            className="mt-1 w-full"
            min={0}
            max={10_000}
            value={value.priority}
            onChange={(next) => setValue({ ...value, priority: next ?? 0 })}
          />
        </label>
        <label className="text-xs font-650 text-text-muted">
          资源类型
          <Input
            className="mt-1"
            value={value.resourceType}
            onChange={(event) => setValue({ ...value, resourceType: event.target.value })}
          />
        </label>
        <label className="text-xs font-650 text-text-muted">
          业务动作
          <Input
            className="mt-1"
            value={value.operation}
            onChange={(event) => setValue({ ...value, operation: event.target.value })}
          />
        </label>
        <label className="col-span-2 text-xs font-650 text-text-muted phone-down:col-span-1">
          审批人账号 ID（逗号分隔）
          <Input
            className="mt-1"
            value={value.approverIds}
            onChange={(event) => setValue({ ...value, approverIds: event.target.value })}
          />
        </label>
        <label className="text-xs font-650 text-text-muted">
          同级通过模式
          <Select
            className="mt-1 w-full"
            value={value.mode}
            options={[
              { value: "any", label: "任一通过" },
              { value: "all", label: "全部通过" },
            ]}
            onChange={(mode) => setValue({ ...value, mode })}
          />
        </label>
        <label className="text-xs font-650 text-text-muted">
          超时动作
          <Select
            className="mt-1 w-full"
            value={value.timeoutAction}
            options={[
              { value: "wait", label: "继续等待" },
              { value: "transfer", label: "转交候补" },
              { value: "escalate", label: "升级候补" },
              { value: "reject", label: "自动驳回" },
            ]}
            onChange={(timeoutAction) => setValue({ ...value, timeoutAction })}
          />
        </label>
        <label className="text-xs font-650 text-text-muted">
          提醒分钟
          <InputNumber
            className="mt-1 w-full"
            min={1}
            max={43_199}
            value={value.reminderMinutes}
            onChange={(next) => setValue({ ...value, reminderMinutes: next ?? 1 })}
          />
        </label>
        <label className="text-xs font-650 text-text-muted">
          超时分钟
          <InputNumber
            className="mt-1 w-full"
            min={2}
            max={43_200}
            value={value.timeoutMinutes}
            onChange={(next) => setValue({ ...value, timeoutMinutes: next ?? 2 })}
          />
        </label>
        <label className="text-xs font-650 text-text-muted">
          密级范围
          <Select
            className="mt-1 w-full"
            mode="multiple"
            value={value.securityLevels}
            options={["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"].map((item) => ({
              value: item,
              label: item,
            }))}
            onChange={(securityLevels) => setValue({ ...value, securityLevels })}
          />
        </label>
        <label className="text-xs font-650 text-text-muted">
          风险范围
          <Select
            className="mt-1 w-full"
            mode="multiple"
            value={value.riskLevels}
            options={[
              { value: "normal", label: "常规" },
              { value: "high", label: "高" },
              { value: "critical", label: "关键" },
            ]}
            onChange={(riskLevels) => setValue({ ...value, riskLevels })}
          />
        </label>
        <label className="flex items-center gap-3 text-xs font-650 text-text-muted">
          <Switch
            checked={value.allowSelfApproval}
            onChange={(allowSelfApproval) => setValue({ ...value, allowSelfApproval })}
          />
          允许申请人自审
        </label>
      </div>
      {value.reminderMinutes >= value.timeoutMinutes && (
        <Alert className="mt-4" type="error" showIcon message="提醒时间必须早于超时时间" />
      )}
    </Modal>
  );
}

/** 展示策略身份、当前版本和多级定义，并提供受限创建或修订入口。 */
export function ApprovalPolicyPanel(props: ApprovalPolicyPanelProps) {
  const [editorOpen, setEditorOpen] = useState(false);
  const isComplex = Boolean(
    props.detail &&
    (props.detail.version.definition.levels.length > 1 ||
      props.detail.version.definition.levels.some((level) => level.sources.length > 1)),
  );
  const submit = (value: PolicyFormValue) => {
    const definition = definitionFromForm(value);
    if (props.detail) props.onRevise(definition, props.detail.policy.version);
    else props.onCreate({ name: value.name.trim(), definition });
    setEditorOpen(false);
  };
  if (props.isLoading && !props.policies.length) return <Skeleton active paragraph={{ rows: 8 }} />;
  return (
    <>
      <div className="grid grid-cols-[minmax(220px,280px)_minmax(0,1fr)] gap-5 tablet-down:grid-cols-1">
        <aside className="rounded-ui border border-solid border-border-soft bg-surface-subtle p-3">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="m-0 text-sm text-text-strong">审批策略</h2>
            {props.canCreate && (
              <Button
                type="text"
                icon={<Plus size={16} />}
                aria-label="创建审批策略"
                onClick={() => setEditorOpen(true)}
              />
            )}
          </div>
          <div className="flex flex-col gap-2">
            {props.policies.map((policy) => (
              <Button
                key={policy.approval_policy_id}
                type={props.selectedId === policy.approval_policy_id ? "primary" : "text"}
                className="justify-start"
                onClick={() => props.onSelect(policy.approval_policy_id)}
              >
                <span className="truncate">{policy.name}</span>
              </Button>
            ))}
            {!props.policies.length && (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无策略" />
            )}
          </div>
        </aside>
        <section className="min-w-0" aria-label="审批策略当前版本">
          {!props.detail ? (
            <Empty description="选择一个审批策略" />
          ) : (
            <>
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="m-0 text-lg text-text-strong">{props.detail.policy.name}</h2>
                    <Tag>版本 {props.detail.version.version_number}</Tag>
                    <Tag color="green">
                      {props.detail.policy.status === "active" ? "生效中" : "已停用"}
                    </Tag>
                  </div>
                  <p className="mb-0 mt-2 text-sm text-text-muted">
                    {props.detail.version.definition.resource_type} ·{" "}
                    {props.detail.version.definition.operation} · 优先级{" "}
                    {props.detail.version.definition.priority}
                  </p>
                </div>
                {props.canUpdate && (
                  <Button
                    icon={<Save size={16} />}
                    disabled={isComplex}
                    onClick={() => setEditorOpen(true)}
                  >
                    修订策略
                  </Button>
                )}
              </div>
              {isComplex && (
                <Alert
                  className="mb-4"
                  type="info"
                  showIcon
                  message="复杂多层策略保持只读，避免首期结构化表单丢失定义"
                />
              )}
              <Table
                rowKey="sequence_no"
                pagination={false}
                dataSource={[...props.detail.version.definition.levels]}
                scroll={{ x: 680 }}
                columns={[
                  {
                    title: "层级",
                    dataIndex: "sequence_no",
                    width: 80,
                    render: (value: number) => `第 ${value} 级`,
                  },
                  {
                    title: "通过模式",
                    dataIndex: "mode",
                    width: 110,
                    render: (value: string) => (value === "any" ? "任一通过" : "全部通过"),
                  },
                  {
                    title: "审批来源",
                    dataIndex: "sources",
                    render: (sources) =>
                      sources
                        .map(
                          (source: { source_type: string; reference_ids: readonly string[] }) =>
                            `${source.source_type} (${source.reference_ids.length})`,
                        )
                        .join("、"),
                  },
                  {
                    title: "提醒",
                    dataIndex: "reminder_after_minutes",
                    width: 100,
                    render: (value: number) => `${value} 分钟`,
                  },
                  {
                    title: "超时",
                    dataIndex: "timeout_after_minutes",
                    width: 100,
                    render: (value: number) => `${value} 分钟`,
                  },
                  { title: "超时动作", dataIndex: "timeout_action", width: 110 },
                ]}
              />
            </>
          )}
        </section>
      </div>
      <PolicyEditorModal
        key={`${props.detail?.policy.approval_policy_id ?? "new"}-${props.detail?.policy.version ?? 0}-${editorOpen}`}
        open={editorOpen}
        detail={props.detail}
        isMutating={props.isMutating}
        onClose={() => setEditorOpen(false)}
        onSubmit={submit}
      />
    </>
  );
}
