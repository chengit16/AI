/** @description 团队知识域、RAG 策略与声明范围治理面板。 */
import { Button, Empty, Popconfirm, Tag, Tooltip } from "antd";
import { Archive, Braces, Pencil, Plus, ScanSearch, SlidersHorizontal } from "lucide-react";

import type {
  EnterpriseKnowledgePortal,
  TeamKnowledgeDomain,
} from "@/api/services/enterpriseKnowledge";

import type { EnterpriseKnowledgePermissions } from "../types";

interface DomainGovernancePanelProps {
  /** 企业知识统一快照。 */
  snapshot: EnterpriseKnowledgePortal;
  /** 页面动作体验权限，服务端仍逐请求执行授权。 */
  permissions: EnterpriseKnowledgePermissions;
  /** 任一知识域写操作是否正在提交。 */
  pending: boolean;
  /** 打开知识域创建表单。 */
  onCreate: () => void;
  /** 打开知识域编辑表单。 */
  onEdit: (domain: TeamKnowledgeDomain) => void;
  /** 打开成员、部门和知识库原子范围表单。 */
  onScope: (domain: TeamKnowledgeDomain) => void;
  /** 解释当前主体的有效知识库交集。 */
  onResolve: (domain: TeamKnowledgeDomain) => void;
  /** 归档知识域并立即关闭运行范围。 */
  onArchive: (domain: TeamKnowledgeDomain) => void;
}

const ragModeLabel = { balanced: "均衡", precision: "精准", recall: "高召回" } as const;

/** 展示知识域唯一关系、RAG 策略版本和可执行治理动作。 */
export function DomainGovernancePanel(props: DomainGovernancePanelProps) {
  const domains = [...props.snapshot.domains].sort((left, right) => {
    const status = Number(left.status === "archived") - Number(right.status === "archived");
    return status || left.name.localeCompare(right.name, "zh-CN");
  });

  return (
    <section className="ui-surface-panel min-w-0 overflow-hidden" aria-labelledby="domain-title">
      <div className="flex items-start justify-between gap-4 border-b border-b-solid border-border-soft px-6 py-5 phone-down:px-4">
        <div>
          <h2 id="domain-title" className="m-0 text-[17px] text-text-strong">
            团队知识域
          </h2>
          <p className="mb-0 mt-1 text-xs leading-5 text-text-muted">
            声明成员、部门和知识库范围，运行时始终与当前 PDP 授权取交集。
          </p>
        </div>
        {props.permissions.createDomain && (
          <Button type="primary" icon={<Plus size={16} />} onClick={props.onCreate}>
            新建知识域
          </Button>
        )}
      </div>
      {domains.length === 0 ? (
        <Empty
          className="py-12"
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="还没有团队知识域"
        >
          {props.permissions.createDomain && (
            <Button onClick={props.onCreate}>创建第一个知识域</Button>
          )}
        </Empty>
      ) : (
        <ul className="m-0 list-none p-0">
          {domains.map((domain) => (
            <li
              className="border-b border-b-solid border-border-soft px-5 py-4 last:border-b-0 phone-down:px-4"
              key={domain.domain_id}
            >
              <div className="flex min-w-0 items-start gap-3">
                <span className="ui-icon-badge mt-0.5 h-9 w-9 flex-none">
                  <Braces size={17} aria-hidden="true" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <strong className="truncate text-sm text-text-strong">{domain.name}</strong>
                    <Tag color={domain.status === "active" ? "success" : "default"}>
                      {domain.status === "active" ? "活动" : "已归档"}
                    </Tag>
                    <Tag color="blue">RAG {ragModeLabel[domain.rag_policy.mode]}</Tag>
                  </div>
                  <p className="mb-0 mt-2 line-clamp-2 text-xs leading-5 text-text-muted">
                    {domain.description ?? "暂无知识域说明"}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
                    <span>{domain.member_ids.length} 名直接成员</span>
                    <span>{domain.department_ids.length} 个部门</span>
                    <span>{domain.knowledge_base_ids.length} 个知识库</span>
                    <span>Top K {domain.rag_policy.top_k}</span>
                    <span>最低分 {domain.rag_policy.minimum_score.toFixed(2)}</span>
                    <span>策略版本 {domain.rag_policy.policy_version}</span>
                  </div>
                </div>
                {domain.status === "active" && (
                  <div className="flex flex-none flex-wrap justify-end gap-1 phone-down:max-w-22">
                    {props.permissions.resolveDomain && (
                      <Tooltip title="解释有效范围">
                        <Button
                          type="text"
                          aria-label={"解释知识域“" + domain.name + "”的有效范围"}
                          icon={<ScanSearch size={15} />}
                          onClick={() => props.onResolve(domain)}
                        />
                      </Tooltip>
                    )}
                    {props.permissions.scopeDomain && (
                      <Tooltip title="配置声明范围">
                        <Button
                          type="text"
                          aria-label={"配置知识域“" + domain.name + "”的声明范围"}
                          icon={<SlidersHorizontal size={15} />}
                          onClick={() => props.onScope(domain)}
                        />
                      </Tooltip>
                    )}
                    {props.permissions.updateDomain && (
                      <Tooltip title="编辑知识域">
                        <Button
                          type="text"
                          aria-label={"编辑知识域“" + domain.name + "”"}
                          icon={<Pencil size={15} />}
                          onClick={() => props.onEdit(domain)}
                        />
                      </Tooltip>
                    )}
                    {props.permissions.archiveDomain && (
                      <Popconfirm
                        title="归档该知识域？"
                        description="归档后运行范围立即为空，历史关系保留用于审计。"
                        okText="归档"
                        cancelText="取消"
                        onConfirm={() => props.onArchive(domain)}
                      >
                        <Button
                          type="text"
                          danger
                          disabled={props.pending}
                          aria-label={"归档知识域“" + domain.name + "”"}
                          icon={<Archive size={15} />}
                        />
                      </Popconfirm>
                    )}
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
