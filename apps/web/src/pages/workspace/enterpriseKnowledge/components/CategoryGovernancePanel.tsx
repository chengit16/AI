/** @description 企业分类层级、可见策略和文档绑定治理面板。 */
import { Button, Empty, Popconfirm, Tag, Tooltip } from "antd";
import { Archive, FileStack, Pencil, Plus, Shield, UsersRound } from "lucide-react";

import type {
  EnterpriseCategory,
  EnterpriseKnowledgePortal,
} from "@/api/services/enterpriseKnowledge";

import type { EnterpriseKnowledgePermissions } from "../types";

interface CategoryGovernancePanelProps {
  /** 企业知识统一快照。 */
  snapshot: EnterpriseKnowledgePortal;
  /** 页面动作体验权限。 */
  permissions: EnterpriseKnowledgePermissions;
  /** 任一分类写操作是否正在提交。 */
  pending: boolean;
  /** 打开分类创建表单，可传入预选父分类。 */
  onCreate: (parentCategoryId?: string) => void;
  /** 打开分类编辑表单。 */
  onEdit: (category: EnterpriseCategory) => void;
  /** 打开分类文档绑定表单。 */
  onBind: (category: EnterpriseCategory) => void;
  /** 归档分类。 */
  onArchive: (category: EnterpriseCategory) => void;
}

const visibilityLabel = {
  public: "全企业公开",
  departments: "指定部门",
  private: "仅治理人员",
} as const;

/** 把服务端父子关系投影为稳定深度，不修复或隐藏异常层级。 */
function categoryDepth(category: EnterpriseCategory, byId: Map<string, EnterpriseCategory>) {
  let depth = 0;
  let current = category.parent_category_id;
  const visited = new Set<string>([category.category_id]);
  while (current && !visited.has(current) && byId.has(current)) {
    visited.add(current);
    depth += 1;
    current = byId.get(current)?.parent_category_id ?? null;
  }
  return depth;
}

/** 呈现分类层级和显式关系；按钮隐藏只做体验裁剪，接口仍独立授权。 */
export function CategoryGovernancePanel(props: CategoryGovernancePanelProps) {
  const byId = new Map(props.snapshot.categories.map((item) => [item.category_id, item]));
  const ordered = [...props.snapshot.categories].sort((left, right) => {
    const status = Number(left.status === "archived") - Number(right.status === "archived");
    if (status) return status;
    const depth = categoryDepth(left, byId) - categoryDepth(right, byId);
    return depth || left.name.localeCompare(right.name, "zh-CN");
  });
  const departmentById = new Map(
    props.snapshot.departments.map((item) => [item.department_id, item.name]),
  );

  return (
    <section className="ui-surface-panel min-w-0 overflow-hidden" aria-labelledby="category-title">
      <div className="flex items-start justify-between gap-4 border-b border-b-solid border-border-soft px-6 py-5 phone-down:px-4">
        <div>
          <h2 id="category-title" className="m-0 text-[17px] text-text-strong">
            企业分类
          </h2>
          <p className="mb-0 mt-1 text-xs leading-5 text-text-muted">
            独立治理层级与访问标签，不复制文档内容或改变文档权限。
          </p>
        </div>
        {props.permissions.createCategory && (
          <Button type="primary" icon={<Plus size={16} />} onClick={() => props.onCreate()}>
            新建分类
          </Button>
        )}
      </div>
      {ordered.length === 0 ? (
        <Empty className="py-12" image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有企业分类">
          {props.permissions.createCategory && (
            <Button onClick={() => props.onCreate()}>创建第一个分类</Button>
          )}
        </Empty>
      ) : (
        <ul className="m-0 list-none p-0">
          {ordered.map((category) => {
            const depth = categoryDepth(category, byId);
            const departmentNames = category.department_ids
              .map((id) => departmentById.get(id) ?? "受限部门")
              .join("、");
            return (
              <li
                className="border-b border-b-solid border-border-soft px-5 py-4 last:border-b-0 phone-down:px-4"
                key={category.category_id}
              >
                <div className="flex min-w-0 items-start gap-3">
                  <span
                    className="mt-1 h-8 flex-none border-l border-l-solid border-brand-border"
                    style={{ marginLeft: Math.min(depth, 4) * 18 }}
                    aria-hidden="true"
                  />
                  <span className="ui-icon-badge mt-0.5 h-9 w-9 flex-none">
                    <Shield size={17} aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <strong className="truncate text-sm text-text-strong">{category.name}</strong>
                      <Tag color={category.status === "active" ? "success" : "default"}>
                        {category.status === "active" ? "活动" : "已归档"}
                      </Tag>
                      <Tag>{visibilityLabel[category.visibility]}</Tag>
                      {category.approval_required && <Tag color="warning">发布需审批</Tag>}
                    </div>
                    <p className="mb-0 mt-2 line-clamp-2 text-xs leading-5 text-text-muted">
                      {category.description ?? "暂无分类说明"}
                    </p>
                    <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-text-muted">
                      <span className="inline-flex items-center gap-1">
                        <FileStack size={13} aria-hidden="true" />
                        {category.document_ids.length} 篇文档
                      </span>
                      {category.visibility === "departments" && (
                        <Tooltip title={departmentNames}>
                          <span className="inline-flex max-w-56 items-center gap-1 truncate">
                            <UsersRound size={13} aria-hidden="true" />
                            {departmentNames}
                          </span>
                        </Tooltip>
                      )}
                      <span>版本 {category.version}</span>
                    </div>
                  </div>
                  {category.status === "active" && (
                    <div className="flex flex-none flex-wrap justify-end gap-1 phone-down:max-w-22">
                      {props.permissions.createCategory && (
                        <Tooltip title="新建子分类">
                          <Button
                            type="text"
                            aria-label={"在“" + category.name + "”下新建子分类"}
                            icon={<Plus size={15} />}
                            onClick={() => props.onCreate(category.category_id)}
                          />
                        </Tooltip>
                      )}
                      {props.permissions.bindCategory && (
                        <Tooltip title="绑定文档">
                          <Button
                            type="text"
                            aria-label={"绑定“" + category.name + "”的文档"}
                            icon={<FileStack size={15} />}
                            onClick={() => props.onBind(category)}
                          />
                        </Tooltip>
                      )}
                      {props.permissions.updateCategory && (
                        <Tooltip title="编辑分类">
                          <Button
                            type="text"
                            aria-label={"编辑分类“" + category.name + "”"}
                            icon={<Pencil size={15} />}
                            onClick={() => props.onEdit(category)}
                          />
                        </Tooltip>
                      )}
                      {props.permissions.archiveCategory && (
                        <Popconfirm
                          title="归档该分类？"
                          description="存在活动子分类时服务端会拒绝归档。"
                          okText="归档"
                          cancelText="取消"
                          onConfirm={() => props.onArchive(category)}
                        >
                          <Button
                            type="text"
                            danger
                            disabled={props.pending}
                            aria-label={"归档分类“" + category.name + "”"}
                            icon={<Archive size={15} />}
                          />
                        </Popconfirm>
                      )}
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
