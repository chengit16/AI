/** @description 审计筛选、详情脱敏展示与异步导出状态面板。 */
import { Button, Descriptions, Drawer, Input, Select, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { Download, RotateCcw } from "lucide-react";

import type {
  AuditExport,
  AuditFilters,
  AuditRecord,
  AuditRecordDetail,
} from "@/api/services/permissionsAudit";

interface Props {
  /** 当前由 URL 恢复的审计筛选条件。 */
  filters: AuditFilters;
  /** 服务端返回且不含自由属性的审计列表。 */
  records: readonly AuditRecord[];
  /** 当前空间最近的异步导出状态。 */
  exports: readonly AuditExport[];
  /** 当前选中记录的白名单化详情。 */
  detail?: AuditRecordDetail;
  /** 审计详情是否仍在加载。 */
  detailLoading: boolean;
  /** 当前打开详情抽屉的审计标识。 */
  selectedAuditId: string | null;
  /** 创建异步导出请求是否正在提交。 */
  exporting: boolean;
  /** 更新一个 URL 筛选条件。 */
  onFilter: (key: string, value?: string) => void;
  /** 清空当前页签的全部审计筛选条件。 */
  onReset: () => void;
  /** 打开或关闭指定审计详情。 */
  onSelect: (auditId: string | null) => void;
  /** 按当前冻结条件创建脱敏导出。 */
  onExport: () => void;
}

const outcomeColor = { succeeded: "success", denied: "warning", failed: "error" } as const;

/** 展示服务端投影，内部对象键、Payload 和自由内容不会由浏览器拼接。 */
export function AuditGovernancePanel(props: Props) {
  const columns: TableColumnsType<AuditRecord> = [
    {
      title: "时间",
      dataIndex: "occurred_at",
      width: 180,
      render: (value) => new Date(value).toLocaleString("zh-CN"),
    },
    { title: "操作", dataIndex: "action", width: 240 },
    { title: "资源", dataIndex: "resource_type", width: 160 },
    {
      title: "结果",
      dataIndex: "outcome",
      width: 100,
      render: (value) => <Tag color={outcomeColor[value as AuditRecord["outcome"]]}>{value}</Tag>,
    },
    {
      title: "操作者",
      dataIndex: "actor_id",
      width: 220,
      render: (value, record) => (record.actor_id_masked ? "受字段权限保护" : value),
    },
  ];
  return (
    <div className="space-y-5">
      <section className="ui-surface-panel p-5">
        <div className="grid grid-cols-4 gap-3 nav-mobile:grid-cols-1">
          <Input
            allowClear
            placeholder="操作，例如 role.permissions.replace"
            value={props.filters.action}
            onChange={(event) => props.onFilter("action", event.target.value || undefined)}
          />
          <Input
            allowClear
            placeholder="资源类型"
            value={props.filters.resourceType}
            onChange={(event) => props.onFilter("resource", event.target.value || undefined)}
          />
          <Input
            allowClear
            placeholder="操作者 UUID"
            value={props.filters.actorId}
            onChange={(event) => props.onFilter("actor", event.target.value || undefined)}
          />
          <Select
            allowClear
            placeholder="结果"
            value={props.filters.outcome}
            options={[
              { value: "succeeded", label: "成功" },
              { value: "denied", label: "拒绝" },
              { value: "failed", label: "失败" },
            ]}
            onChange={(value) => props.onFilter("outcome", value)}
          />
        </div>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button icon={<RotateCcw size={16} />} onClick={props.onReset}>
            重置筛选
          </Button>
          <Button
            type="primary"
            icon={<Download size={16} />}
            loading={props.exporting}
            onClick={props.onExport}
          >
            创建脱敏导出
          </Button>
        </div>
      </section>
      <section className="ui-surface-panel overflow-hidden">
        <div className="border-b border-b-solid border-border px-5 py-4">
          <h2 className="m-0 text-base">审计记录</h2>
          <p className="mb-0 mt-1 text-xs text-text-muted">
            列表不返回自由属性，点击一行查看白名单化详情。
          </p>
        </div>
        <div className="overflow-x-auto">
          <Table
            rowKey="audit_id"
            columns={columns}
            dataSource={[...props.records]}
            pagination={{ pageSize: 20 }}
            scroll={{ x: 900 }}
            onRow={(record) => ({ onClick: () => props.onSelect(record.audit_id) })}
          />
        </div>
      </section>
      <section className="ui-surface-panel overflow-hidden">
        <div className="border-b border-b-solid border-border px-5 py-4">
          <h2 className="m-0 text-base">导出任务</h2>
          <p className="mb-0 mt-1 text-xs text-text-muted">
            筛选与字段权限在创建时冻结，页面仅轮询安全摘要。
          </p>
        </div>
        <Table
          rowKey="audit_export_request_id"
          dataSource={[...props.exports]}
          pagination={false}
          columns={[
            {
              title: "创建时间",
              dataIndex: "created_at",
              render: (value) => new Date(value).toLocaleString("zh-CN"),
            },
            { title: "状态", dataIndex: "status", render: (value) => <Tag>{value}</Tag> },
            { title: "行数", dataIndex: "row_count", render: (value) => value ?? "—" },
            {
              title: "结果摘要",
              dataIndex: "result_summary",
              render: (value) => value ?? "处理中",
            },
          ]}
        />
      </section>
      <Drawer
        title="审计详情"
        width={560}
        open={Boolean(props.selectedAuditId)}
        loading={props.detailLoading}
        onClose={() => props.onSelect(null)}
      >
        {props.detail && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="操作">{props.detail.action}</Descriptions.Item>
            <Descriptions.Item label="资源">
              {props.detail.resource_type} / {props.detail.resource_id}
            </Descriptions.Item>
            <Descriptions.Item label="权限码">
              {props.detail.permission_code ?? "—"}
            </Descriptions.Item>
            <Descriptions.Item label="Trace ID">{props.detail.trace_id}</Descriptions.Item>
            <Descriptions.Item label="扩展属性">
              <pre className="m-0 whitespace-pre-wrap break-all text-xs">
                {JSON.stringify(props.detail.attributes, null, 2)}
              </pre>
            </Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </div>
  );
}
