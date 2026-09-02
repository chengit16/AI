/** @description 企业文档发布审批台账、详情和通用审批动作入口。 */
import { Button, Drawer, Empty, Input, Modal, Select, Skeleton, Table, Tag, Timeline } from "antd";
import type { TableColumnsType } from "antd";
import { ArrowRightLeft, Check, Eye, RotateCcw, X } from "lucide-react";
import { useState } from "react";

import type {
  DocumentPublishRequest,
  EnterpriseDocumentDetail,
  EnterpriseKnowledgePortal,
} from "@/api/services/enterpriseKnowledge";

import type { EnterpriseKnowledgePermissions } from "../types";

type PublishApprovalAction = "approve" | "reject" | "transfer" | "withdraw";

interface DocumentPublishApprovalPanelProps {
  /** 企业知识快照用于还原文档、分类和成员名称。 */
  snapshot: EnterpriseKnowledgePortal;
  /** 当前参与者可见的发布请求。 */
  items: readonly DocumentPublishRequest[];
  /** 当前浏览器会话账号，只用于裁剪责任相关入口。 */
  accountId: string | null;
  /** 菜单权限只裁剪体验，服务端仍重新校验审批责任。 */
  permissions: EnterpriseKnowledgePermissions;
  /** 台账首次加载状态。 */
  loading: boolean;
  /** 台账失败时的稳定提示。 */
  errorDescription: string | null;
  /** 任一审批动作是否正在提交。 */
  pending: boolean;
  /** 重新读取参与者可见台账。 */
  onRetry: () => void;
  /** 复用通用审批运行时提交通过、驳回或撤回。 */
  onAct: (instanceId: string, action: "approve" | "reject" | "withdraw", reason?: string) => void;
  /** 复用通用审批运行时转交当前责任。 */
  onTransfer: (instanceId: string, targetAccountId: string) => void;
  /** 读取选中文档的版本与索引状态，候选 ID 由服务端详情提供。 */
  onLoadVersions: (knowledgeBaseId: string, documentId: string) => void;
  /** 当前文档详情请求是否进行中。 */
  versionsLoading: boolean;
  /** 最近一次服务端返回的文档详情及其对应文档 ID。 */
  versionsDocumentId: string | null;
  /** 服务端按当前账号资源权限裁剪后的文档版本详情。 */
  versionsDetail: EnterpriseDocumentDetail | undefined;
  /** 申请成功后由页面 Mutation 回调关闭弹窗，失败时保留原幂等键供安全重试。 */
  onRequestPublish: (
    documentId: string,
    documentVersionId: string,
    idempotencyKey: string,
    onSuccess: () => void,
  ) => void;
  /** 发布申请提交状态。 */
  requestPending: boolean;
}

interface PendingCommand {
  request: DocumentPublishRequest;
  action: PublishApprovalAction;
}

const requestStatus = {
  pending: { label: "审批中", color: "processing" },
  published: { label: "已发布", color: "success" },
  rejected: { label: "已驳回", color: "error" },
  withdrawn: { label: "已撤回", color: "default" },
  expired: { label: "已过期", color: "warning" },
  publish_failed: { label: "发布失败", color: "error" },
} as const;

const levelStatus = {
  waiting: "等待前序审批",
  active: "当前审批层级",
  approved: "已通过",
  rejected: "已驳回",
  withdrawn: "已撤回",
} as const;

const failureReason: Record<string, string> = {
  requester_inactive: "申请人已停用",
  permission_revoked: "申请人发布权限已撤销",
  policy_unavailable: "授权策略暂时不可用",
  document_inactive: "文档已归档或不可用",
  version_changed: "版本事实已变化",
  governance_changed: "分类治理规则已变化",
  index_not_ready: "索引不再处于可发布状态",
};

/** 以本地时区显示服务端时间，空值保持可解释占位。 */
function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "-";
}

/** 展示发布请求业务终态与冻结审批链，按钮只对当前责任人或申请人出现。 */
export function DocumentPublishApprovalPanel(props: DocumentPublishApprovalPanelProps) {
  const [detail, setDetail] = useState<DocumentPublishRequest | null>(null);
  const [command, setCommand] = useState<PendingCommand | null>(null);
  const [value, setValue] = useState("");
  const [requestOpen, setRequestOpen] = useState(false);
  const [requestDocumentId, setRequestDocumentId] = useState<string>();
  const [requestVersionId, setRequestVersionId] = useState<string>();
  const [requestIdempotencyKey, setRequestIdempotencyKey] = useState("");
  const documentById = new Map(props.snapshot.documents.map((item) => [item.document_id, item]));
  const categoryById = new Map(props.snapshot.categories.map((item) => [item.category_id, item]));
  const memberOptions = props.snapshot.members
    .filter((item) => item.account_id !== props.accountId)
    .map((item) => ({
      value: item.account_id,
      label: item.display_name ?? `成员 ${item.account_id.slice(0, 8)}`,
    }));
  const approvalDocumentIds = new Set(
    props.snapshot.categories
      .filter((category) => category.status === "active" && category.approval_required)
      .flatMap((category) => category.document_ids),
  );
  const requestableDocuments = props.snapshot.documents.filter((document) =>
    approvalDocumentIds.has(document.document_id),
  );
  const pendingVersionIds = new Set(
    props.items.filter((item) => item.status === "pending").map((item) => item.document_version_id),
  );
  const readyVersions =
    props.versionsDocumentId === requestDocumentId && props.versionsDetail
      ? props.versionsDetail.versions
          .filter(
            (item) =>
              item.version.status === "ready" &&
              (item.index?.status === "ready" || item.index?.status === "active") &&
              !pendingVersionIds.has(item.version.document_version_id),
          )
          .map((item) => ({
            value: item.version.document_version_id,
            label: `V${item.version.version_number} · 索引已就绪`,
          }))
      : [];
  const openCommand = (request: DocumentPublishRequest, action: PublishApprovalAction) => {
    setValue("");
    setCommand({ request, action });
  };
  const submitCommand = () => {
    if (!command) return;
    const instanceId = command.request.approval.approval_instance_id;
    if (command.action === "transfer") props.onTransfer(instanceId, value);
    else props.onAct(instanceId, command.action, command.action === "reject" ? value : undefined);
    setCommand(null);
  };
  const openRequest = () => {
    setRequestDocumentId(undefined);
    setRequestVersionId(undefined);
    setRequestIdempotencyKey(crypto.randomUUID());
    setRequestOpen(true);
  };
  const closeRequest = () => {
    if (props.requestPending) return;
    setRequestOpen(false);
    setRequestDocumentId(undefined);
    setRequestVersionId(undefined);
  };
  const submitRequest = () => {
    if (!requestDocumentId || !requestVersionId || !requestIdempotencyKey) return;
    props.onRequestPublish(
      requestDocumentId,
      requestVersionId,
      requestIdempotencyKey,
      closeRequest,
    );
  };

  const columns: TableColumnsType<DocumentPublishRequest> = [
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (status: DocumentPublishRequest["status"]) => (
        <Tag color={requestStatus[status].color}>{requestStatus[status].label}</Tag>
      ),
    },
    {
      title: "文档版本",
      key: "document",
      width: 230,
      render: (_, item) => (
        <div className="min-w-0">
          <p className="m-0 truncate text-sm font-650 text-text-strong">
            {documentById.get(item.document_id)?.title ??
              `受限文档 ${item.document_id.slice(0, 8)}`}
          </p>
          <p className="mb-0 mt-1 text-xs text-text-muted">不可变版本 V{item.version_number}</p>
        </div>
      ),
    },
    {
      title: "审批进度",
      key: "approval",
      width: 150,
      render: (_, item) => (
        <span className="text-sm text-text-muted">
          第 {item.approval.current_sequence_no}/{item.approval.levels.length} 级
        </span>
      ),
    },
    { title: "申请时间", dataIndex: "created_at", width: 180, render: formatTime },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      width: 330,
      render: (_, item) => {
        const activeLevel = item.approval.levels.find(
          (level) =>
            level.sequence_no === item.approval.current_sequence_no && level.status === "active",
        );
        const assigned = Boolean(
          props.accountId && activeLevel?.approver_account_ids.includes(props.accountId),
        );
        const requester = item.requester_account_id === props.accountId;
        return (
          <div className="flex flex-wrap gap-1">
            <Button type="link" icon={<Eye size={15} />} onClick={() => setDetail(item)}>
              详情
            </Button>
            {item.status === "pending" && assigned && props.permissions.approvePublishRequest && (
              <Button
                type="link"
                icon={<Check size={15} />}
                onClick={() => openCommand(item, "approve")}
              >
                通过
              </Button>
            )}
            {item.status === "pending" && assigned && props.permissions.rejectPublishRequest && (
              <Button
                danger
                type="link"
                icon={<X size={15} />}
                onClick={() => openCommand(item, "reject")}
              >
                驳回
              </Button>
            )}
            {item.status === "pending" && assigned && props.permissions.transferPublishRequest && (
              <Button
                type="link"
                icon={<ArrowRightLeft size={15} />}
                onClick={() => openCommand(item, "transfer")}
              >
                转交
              </Button>
            )}
            {item.status === "pending" && requester && props.permissions.withdrawPublishRequest && (
              <Button
                type="link"
                icon={<RotateCcw size={15} />}
                onClick={() => openCommand(item, "withdraw")}
              >
                撤回
              </Button>
            )}
          </div>
        );
      },
    },
  ];

  return (
    <section
      className="ui-surface-panel mt-5 min-w-0 overflow-hidden"
      aria-labelledby="publish-approval-title"
    >
      <div className="flex items-start justify-between gap-4 border-b border-b-solid border-border-soft px-6 py-5 phone-down:px-4">
        <div>
          <h2 id="publish-approval-title" className="m-0 text-[17px] text-text-strong">
            文档发布审批
          </h2>
          <p className="mb-0 mt-1 text-xs leading-5 text-text-muted">
            只展示当前账号发起或参与的请求；批准后仍会复核成员、权限、版本、索引和治理快照。
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {props.permissions.requestPublishRequest && (
            <Button type="primary" onClick={openRequest}>
              提交发布申请
            </Button>
          )}
          <Button onClick={props.onRetry}>刷新台账</Button>
        </div>
      </div>
      {props.loading ? (
        <Skeleton className="p-6" active paragraph={{ rows: 6 }} />
      ) : props.errorDescription ? (
        <div className="p-8 text-center">
          <p className="mt-0 text-sm text-status-danger">{props.errorDescription}</p>
          <Button onClick={props.onRetry}>重新加载</Button>
        </div>
      ) : props.items.length === 0 ? (
        <Empty
          className="py-12"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无参与的发布审批"
        />
      ) : (
        <Table<DocumentPublishRequest>
          rowKey="publish_request_id"
          columns={columns}
          dataSource={[...props.items]}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          scroll={{ x: 1040 }}
        />
      )}
      <Drawer
        title="发布审批详情"
        open={Boolean(detail)}
        size={560}
        onClose={() => setDetail(null)}
      >
        {detail && (
          <div className="grid gap-5">
            <div className="rounded-panel bg-surface-subtle p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Tag color={requestStatus[detail.status].color}>
                  {requestStatus[detail.status].label}
                </Tag>
                <strong>{documentById.get(detail.document_id)?.title ?? "受限文档"}</strong>
                <span className="text-sm text-text-muted">V{detail.version_number}</span>
              </div>
              <p className="mb-0 mt-3 text-sm text-text-muted">
                分类快照：
                {detail.category_ids
                  .map((id) => categoryById.get(id)?.name ?? id.slice(0, 8))
                  .join("、")}
              </p>
              <p className="mb-0 mt-2 text-sm text-text-muted">
                完成时间：{formatTime(detail.completed_at)}
              </p>
              {detail.failure_reason_code && (
                <p className="mb-0 mt-2 text-sm text-status-danger">
                  失败原因：
                  {failureReason[detail.failure_reason_code] ?? detail.failure_reason_code}
                </p>
              )}
            </div>
            <Timeline
              items={detail.approval.levels.map((level) => ({
                color:
                  level.status === "approved"
                    ? "green"
                    : level.status === "active"
                      ? "blue"
                      : "gray",
                children: (
                  <div>
                    <strong>
                      第 {level.sequence_no} 级 · {level.mode === "all" ? "全部通过" : "任一通过"}
                    </strong>
                    <p className="mb-0 mt-1 text-xs text-text-muted">
                      {levelStatus[level.status]} · {level.approver_account_ids.length} 名审批人 ·
                      完成于 {formatTime(level.completed_at)}
                    </p>
                  </div>
                ),
              }))}
            />
          </div>
        )}
      </Drawer>
      <Modal
        title="提交文档发布申请"
        open={requestOpen}
        okText="提交申请"
        cancelText="取消"
        confirmLoading={props.requestPending}
        okButtonProps={{ disabled: !requestDocumentId || !requestVersionId }}
        onOk={submitRequest}
        onCancel={closeRequest}
      >
        <div className="grid gap-4">
          <p className="m-0 text-sm leading-6 text-text-muted">
            仅显示命中“需要发布审批”分类的文档；版本与索引状态由服务端实时校验。
          </p>
          {requestableDocuments.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可申请的审批文档" />
          ) : (
            <>
              <Select
                aria-label="发布申请文档"
                className="w-full"
                value={requestDocumentId}
                placeholder="选择需要发布的文档"
                options={requestableDocuments.map((document) => ({
                  value: document.document_id,
                  label: document.title,
                }))}
                onChange={(documentId) => {
                  const document = requestableDocuments.find(
                    (item) => item.document_id === documentId,
                  );
                  setRequestDocumentId(documentId);
                  setRequestVersionId(undefined);
                  if (document)
                    props.onLoadVersions(document.knowledge_base_id, document.document_id);
                }}
              />
              <Select
                aria-label="发布申请版本"
                className="w-full"
                value={requestVersionId}
                loading={props.versionsLoading}
                disabled={!requestDocumentId || props.versionsDocumentId !== requestDocumentId}
                placeholder={props.versionsLoading ? "正在读取版本" : "选择可发布版本"}
                options={readyVersions}
                onChange={setRequestVersionId}
                notFoundContent="暂无索引就绪版本"
              />
            </>
          )}
        </div>
      </Modal>
      <Modal
        title={
          command
            ? {
                approve: "通过发布审批",
                reject: "驳回发布审批",
                transfer: "转交发布审批",
                withdraw: "撤回发布申请",
              }[command.action]
            : "发布审批动作"
        }
        open={Boolean(command)}
        okText="确认"
        cancelText="取消"
        confirmLoading={props.pending}
        okButtonProps={{
          danger: command?.action === "reject",
          disabled: (command?.action === "reject" || command?.action === "transfer") && !value,
        }}
        onOk={submitCommand}
        onCancel={() => setCommand(null)}
      >
        {command?.action === "reject" && (
          <Input
            value={value}
            maxLength={128}
            placeholder="输入小写原因码，例如 governance_mismatch"
            onChange={(event) => setValue(event.target.value.trim())}
          />
        )}
        {command?.action === "transfer" && (
          <Select
            className="w-full"
            value={value || undefined}
            options={memberOptions}
            placeholder="选择新的活动审批人"
            onChange={setValue}
          />
        )}
        {(command?.action === "approve" || command?.action === "withdraw") && (
          <p className="m-0 text-sm text-text-muted">服务端将重新校验当前责任、版本和审批状态。</p>
        )}
      </Modal>
    </section>
  );
}
