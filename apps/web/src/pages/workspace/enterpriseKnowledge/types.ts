/** @description 企业分类与团队知识域页面的表单和体验权限类型。 */
import type {
  CreateEnterpriseCategory,
  CreateTeamKnowledgeDomain,
  EnterpriseCategory,
  ReplaceKnowledgeDomainScope,
  TeamKnowledgeDomain,
  UpdateEnterpriseCategory,
  UpdateTeamKnowledgeDomain,
} from "@/api/services/enterpriseKnowledge";

/** 企业分类创建或编辑表单值。 */
export interface CategoryFormValues {
  /** 分类名称。 */
  name: string;
  /** 可选治理说明。 */
  description?: string;
  /** 父分类；空值表示根分类。 */
  parentCategoryId?: string;
  /** 分类独立可见策略。 */
  visibility: CreateEnterpriseCategory["visibility"];
  /** 部门策略的显式部门集合。 */
  departmentIds?: string[];
  /** 创建时的初始文档绑定。 */
  documentIds?: string[];
  /** 命中文档发布时是否强制进入审批链。 */
  approvalRequired: boolean;
}

/** 团队知识域创建或编辑表单值。 */
export interface DomainFormValues {
  /** 知识域名称。 */
  name: string;
  /** 可选维护说明。 */
  description?: string;
  /** 初始直接成员范围。 */
  memberIds?: string[];
  /** 初始部门范围。 */
  departmentIds?: string[];
  /** 初始知识库范围。 */
  knowledgeBaseIds?: string[];
  /** RAG 召回模式。 */
  ragMode: CreateTeamKnowledgeDomain["rag_mode"];
  /** 候选召回数量。 */
  topK: number;
  /** 最低相关性分数。 */
  minimumScore: number;
}

/** 团队知识域原子范围表单值。 */
export interface DomainScopeFormValues {
  /** 直接成员范围。 */
  memberIds?: string[];
  /** 部门范围。 */
  departmentIds?: string[];
  /** 可检索知识库范围。 */
  knowledgeBaseIds?: string[];
}

/** 页面按钮权限只做体验裁剪，服务端仍独立执行 PDP。 */
export interface EnterpriseKnowledgePermissions {
  /** 是否允许创建分类。 */
  createCategory: boolean;
  /** 是否允许编辑分类。 */
  updateCategory: boolean;
  /** 是否允许归档分类。 */
  archiveCategory: boolean;
  /** 是否允许维护分类文档绑定。 */
  bindCategory: boolean;
  /** 是否允许创建知识域。 */
  createDomain: boolean;
  /** 是否允许编辑知识域。 */
  updateDomain: boolean;
  /** 是否允许归档知识域。 */
  archiveDomain: boolean;
  /** 是否允许原子维护知识域范围。 */
  scopeDomain: boolean;
  /** 是否允许解释运行时有效范围。 */
  resolveDomain: boolean;
  /** 是否允许读取参与者可见的文档发布审批。 */
  readPublishRequests: boolean;
  /** 是否允许发起文档发布审批申请。 */
  requestPublishRequest: boolean;
  /** 是否允许通过当前指派。 */
  approvePublishRequest: boolean;
  /** 是否允许驳回当前指派。 */
  rejectPublishRequest: boolean;
  /** 是否允许转交当前指派。 */
  transferPublishRequest: boolean;
  /** 是否允许申请人撤回待审批请求。 */
  withdrawPublishRequest: boolean;
}

/** 把表单值转换为创建分类契约，空字符串不会进入服务端事实。 */
export function categoryCreateBody(values: CategoryFormValues): CreateEnterpriseCategory {
  return {
    name: values.name.trim(),
    description: values.description?.trim() || null,
    parent_category_id: values.parentCategoryId ?? null,
    visibility: values.visibility,
    department_ids: values.visibility === "departments" ? (values.departmentIds ?? []) : [],
    document_ids: values.documentIds ?? [],
    approval_required: values.approvalRequired,
  };
}

/** 把表单值转换为带乐观版本的分类更新契约。 */
export function categoryUpdateBody(
  category: EnterpriseCategory,
  values: CategoryFormValues,
): UpdateEnterpriseCategory {
  return {
    expected_version: category.version,
    name: values.name.trim(),
    description: values.description?.trim() || null,
    parent_category_id: values.parentCategoryId ?? null,
    visibility: values.visibility,
    department_ids: values.visibility === "departments" ? (values.departmentIds ?? []) : [],
    approval_required: values.approvalRequired,
  };
}

/** 把表单值转换为创建知识域契约，显式空集合保持为空。 */
export function domainCreateBody(values: DomainFormValues): CreateTeamKnowledgeDomain {
  return {
    name: values.name.trim(),
    description: values.description?.trim() || null,
    member_ids: values.memberIds ?? [],
    department_ids: values.departmentIds ?? [],
    knowledge_base_ids: values.knowledgeBaseIds ?? [],
    rag_mode: values.ragMode,
    top_k: values.topK,
    minimum_score: values.minimumScore,
  };
}

/** 把表单值转换为带乐观版本的知识域和 RAG 策略更新契约。 */
export function domainUpdateBody(
  domain: TeamKnowledgeDomain,
  values: DomainFormValues,
): UpdateTeamKnowledgeDomain {
  return {
    expected_version: domain.version,
    name: values.name.trim(),
    description: values.description?.trim() || null,
    rag_mode: values.ragMode,
    top_k: values.topK,
    minimum_score: values.minimumScore,
  };
}

/** 把范围表单转换为一次原子替换命令，显式空集合不会回退全企业。 */
export function domainScopeBody(
  domain: TeamKnowledgeDomain,
  values: DomainScopeFormValues,
): ReplaceKnowledgeDomainScope {
  return {
    expected_version: domain.version,
    member_ids: values.memberIds ?? [],
    department_ids: values.departmentIds ?? [],
    knowledge_base_ids: values.knowledgeBaseIds ?? [],
  };
}
