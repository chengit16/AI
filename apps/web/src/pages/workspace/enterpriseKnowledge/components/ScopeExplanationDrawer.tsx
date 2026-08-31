/** @description 团队知识域声明范围、PDP 授权与有效交集解释抽屉。 */
import { Alert, Drawer, Skeleton, Tag } from "antd";

import type {
  EnterpriseKnowledgePortal,
  ResolvedKnowledgeDomainScope,
  TeamKnowledgeDomain,
} from "@/api/services/enterpriseKnowledge";

interface ScopeExplanationDrawerProps {
  /** 门户快照用于把知识库 ID 映射为授权后的稳定名称。 */
  snapshot: EnterpriseKnowledgePortal;
  /** 当前解释目标；空值时关闭抽屉。 */
  domain: TeamKnowledgeDomain | null;
  /** 服务端按当前主体即时计算的范围交集。 */
  result: ResolvedKnowledgeDomainScope | undefined;
  /** 范围解释请求是否正在执行。 */
  loading: boolean;
  /** 关闭抽屉并清理本次解释状态。 */
  onClose: () => void;
}

const emptyReasonLabel = {
  none: "有效范围非空",
  domain_inactive: "知识域已归档",
  member_inactive: "当前成员未处于活动状态",
  actor_outside_declared_scope: "当前成员不在声明的成员或部门范围内",
  no_declared_knowledge_bases: "知识域没有声明任何知识库",
  pdp_scope_empty: "当前 PDP 授权与声明知识库没有交集",
} as const;

/** 解释有效范围为何可用或为空，不把前端计算结果当作运行时授权事实。 */
export function ScopeExplanationDrawer(props: ScopeExplanationDrawerProps) {
  const knowledgeBaseById = new Map(
    props.snapshot.knowledge_bases.map((item) => [item.knowledge_base_id, item.name]),
  );
  const renderScope = (ids: readonly string[]) =>
    ids.length === 0 ? (
      <span className="text-xs text-text-muted">空集合</span>
    ) : (
      <div className="flex flex-wrap gap-2">
        {ids.map((id) => (
          <Tag key={id}>{knowledgeBaseById.get(id) ?? "受限知识库"}</Tag>
        ))}
      </div>
    );

  return (
    <Drawer
      title={props.domain ? "有效范围解释 · " + props.domain.name : "有效范围解释"}
      open={Boolean(props.domain)}
      size={560}
      destroyOnHidden
      onClose={props.onClose}
    >
      {props.loading || !props.result ? (
        <Skeleton active paragraph={{ rows: 8 }} />
      ) : (
        <>
          <Alert
            type={props.result.empty_reason === "none" ? "success" : "warning"}
            showIcon
            title={emptyReasonLabel[props.result.empty_reason]}
            description={
              props.result.actor_in_declared_scope
                ? "当前成员已进入声明人员范围，最终知识库仍以交集为准。"
                : "当前成员未进入声明人员范围，运行时不会检索任何知识库。"
            }
          />
          <dl className="mt-6 grid gap-5">
            <div>
              <dt className="mb-2 text-xs font-700 text-text-muted">声明知识库</dt>
              <dd className="m-0">{renderScope(props.result.declared_knowledge_base_ids)}</dd>
            </div>
            <div>
              <dt className="mb-2 text-xs font-700 text-text-muted">当前 PDP 授权</dt>
              <dd className="m-0">{renderScope(props.result.authorized_knowledge_base_ids)}</dd>
            </div>
            <div className="rounded-panel border border-solid border-brand-border bg-brand-soft p-4">
              <dt className="mb-2 text-xs font-700 text-brand">运行时有效交集</dt>
              <dd className="m-0">{renderScope(props.result.effective_knowledge_base_ids)}</dd>
            </div>
          </dl>
          <p className="mb-0 mt-6 text-xs text-text-muted">
            本次解释使用授权策略版本 {props.result.policy_version}
            ；运行调用时仍会重新计算，不复用浏览器结果。
          </p>
        </>
      )}
    </Drawer>
  );
}
