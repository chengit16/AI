/**
 * @description P6B-04 企业分类、知识域与文档发布审批统一治理页
 *
 * 页面组合服务端统一快照、动态菜单体验权限和治理抽屉；不在浏览器计算授权、
 * 复制文档事实或扩展知识域范围，所有高风险操作仍由后端逐请求校验。
 */
import { Button, Skeleton } from "antd";
import { useState } from "react";

import { errorMessage, PlatformApiError } from "@/api/client";
import type { EnterpriseCategory, TeamKnowledgeDomain } from "@/api/services/enterpriseKnowledge";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";
import { useSessionStore } from "@/store/session";

import { CategoryDocumentBinder, CategoryEditor } from "./components/CategoryDialogs";
import { CategoryGovernancePanel } from "./components/CategoryGovernancePanel";
import { DomainEditor, DomainScopeEditor } from "./components/DomainDialogs";
import { DomainGovernancePanel } from "./components/DomainGovernancePanel";
import { EnterpriseKnowledgeSummary } from "./components/EnterpriseKnowledgeSummary";
import { DocumentPublishApprovalPanel } from "./components/DocumentPublishApprovalPanel";
import { ScopeExplanationDrawer } from "./components/ScopeExplanationDrawer";
import {
  categoryCreateBody,
  categoryUpdateBody,
  domainCreateBody,
  domainScopeBody,
  domainUpdateBody,
  type CategoryFormValues,
  type DomainFormValues,
  type DomainScopeFormValues,
  type EnterpriseKnowledgePermissions,
} from "./types";
import { useEnterpriseKnowledge } from "./useEnterpriseKnowledge";

/** 组合企业分类、知识域和范围解释全流程，按钮权限只用于体验裁剪。 */
export default function EnterpriseKnowledgePage() {
  // 1. 同时读取可信空间事实和菜单发布权限，个人空间与无读取权限均不发起门户请求。
  const { workspaceId, workspaces, currentWorkspace } = useCurrentWorkspace();
  const menu = useWorkspaceMenuNavigation();
  const accountId = useSessionStore((state) => state.accountId);
  const isEnterprise = currentWorkspace?.workspace_type === "enterprise";
  const canRead = menu.visiblePermissionCodes.has("enterprise.knowledge.read");
  const canReadPublishRequests = menu.visiblePermissionCodes.has(
    "enterprise.document.publish.read",
  );
  const management = useEnterpriseKnowledge({
    workspaceId,
    enabled: Boolean(isEnterprise && canRead),
    publishRequestsEnabled: canReadPublishRequests,
  });

  // 2. 抽屉只持有当前交互目标，提交成功后依靠统一 Query 刷新服务端事实。
  const [categoryEditorOpen, setCategoryEditorOpen] = useState(false);
  const [categoryParentId, setCategoryParentId] = useState<string>();
  const [editingCategory, setEditingCategory] = useState<EnterpriseCategory | null>(null);
  const [bindingCategory, setBindingCategory] = useState<EnterpriseCategory | null>(null);
  const [domainEditorOpen, setDomainEditorOpen] = useState(false);
  const [editingDomain, setEditingDomain] = useState<TeamKnowledgeDomain | null>(null);
  const [scopeDomain, setScopeDomain] = useState<TeamKnowledgeDomain | null>(null);
  const [explainingDomain, setExplainingDomain] = useState<TeamKnowledgeDomain | null>(null);

  if (
    workspaces.isLoading ||
    menu.isLoading ||
    (isEnterprise && canRead && management.portal.isLoading)
  ) {
    return <Skeleton active paragraph={{ rows: 12 }} />;
  }
  if (workspaces.isError || !currentWorkspace) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="空间信息未能加载"
        description={errorMessage(workspaces.error)}
      />
    );
  }
  if (!isEnterprise) {
    return (
      <StateView
        kind="empty"
        headingLevel={1}
        title="个人空间无需企业知识治理"
        description="企业分类、团队知识域和范围解释仅在企业空间中启用。"
      />
    );
  }
  if (
    !canRead ||
    menu.error ||
    (management.portal.error instanceof PlatformApiError &&
      management.portal.error.code === "POLICY_DENIED")
  ) {
    return (
      <StateView
        kind="denied"
        headingLevel={1}
        title="当前账号没有企业知识治理权限"
        description="分类、知识域和范围解释只向服务端已授权的企业成员开放。"
      />
    );
  }
  if (management.portal.isError || !management.portal.data) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="企业知识数据未能加载"
        description={errorMessage(management.portal.error)}
        action={<Button onClick={() => void management.portal.refetch()}>重新加载</Button>}
      />
    );
  }

  // 3. 权限集合只决定按钮是否出现，不改变接口参数或后端资源级授权。
  const permissionCodes = menu.visiblePermissionCodes;
  const permissions: EnterpriseKnowledgePermissions = {
    createCategory: permissionCodes.has("enterprise.category.create"),
    updateCategory: permissionCodes.has("enterprise.category.update"),
    archiveCategory: permissionCodes.has("enterprise.category.archive"),
    bindCategory: permissionCodes.has("enterprise.category.bind"),
    createDomain: permissionCodes.has("enterprise.domain.create"),
    updateDomain: permissionCodes.has("enterprise.domain.update"),
    archiveDomain: permissionCodes.has("enterprise.domain.archive"),
    scopeDomain: permissionCodes.has("enterprise.domain.scope"),
    resolveDomain: permissionCodes.has("enterprise.domain.resolve"),
    readPublishRequests: canReadPublishRequests,
    requestPublishRequest: permissionCodes.has("enterprise.document.publish.request"),
    approvePublishRequest: permissionCodes.has("approval.instance.approve"),
    rejectPublishRequest: permissionCodes.has("approval.instance.reject"),
    transferPublishRequest: permissionCodes.has("approval.instance.transfer"),
    withdrawPublishRequest: permissionCodes.has("approval.instance.withdraw"),
  };
  const snapshot = management.portal.data;
  const categoryPending =
    management.createCategory.isPending ||
    management.updateCategory.isPending ||
    management.archiveCategory.isPending ||
    management.bindCategoryDocuments.isPending;
  const domainPending =
    management.createDomain.isPending ||
    management.updateDomain.isPending ||
    management.archiveDomain.isPending ||
    management.replaceDomainScope.isPending;

  return (
    <>
      <PageHeader
        eyebrow="ENTERPRISE KNOWLEDGE"
        title="企业知识库"
        description="用企业分类组织治理关系，用团队知识域声明可检索范围，并解释运行时与当前授权的有效交集。"
      />
      <EnterpriseKnowledgeSummary snapshot={snapshot} />
      <div className="mt-5 grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] items-start gap-5 tablet-down:grid-cols-1">
        <CategoryGovernancePanel
          snapshot={snapshot}
          permissions={permissions}
          pending={categoryPending}
          onCreate={(parentCategoryId) => {
            setEditingCategory(null);
            setCategoryParentId(parentCategoryId);
            setCategoryEditorOpen(true);
          }}
          onEdit={(category) => {
            setEditingCategory(category);
            setCategoryParentId(undefined);
            setCategoryEditorOpen(true);
          }}
          onBind={setBindingCategory}
          onArchive={(category) => management.archiveCategory.mutate(category)}
        />
        <DomainGovernancePanel
          snapshot={snapshot}
          permissions={permissions}
          pending={domainPending}
          onCreate={() => {
            setEditingDomain(null);
            setDomainEditorOpen(true);
          }}
          onEdit={(domain) => {
            setEditingDomain(domain);
            setDomainEditorOpen(true);
          }}
          onScope={setScopeDomain}
          onResolve={(domain) => {
            setExplainingDomain(domain);
            management.resolveDomainScope.mutate(domain.domain_id);
          }}
          onArchive={(domain) => management.archiveDomain.mutate(domain)}
        />
      </div>
      {permissions.readPublishRequests && (
        <DocumentPublishApprovalPanel
          snapshot={snapshot}
          items={management.publishRequests.data ?? []}
          accountId={accountId}
          permissions={permissions}
          loading={management.publishRequests.isLoading}
          errorDescription={
            management.publishRequests.isError
              ? errorMessage(management.publishRequests.error)
              : null
          }
          pending={
            management.actOnPublishRequest.isPending ||
            management.transferPublishRequest.isPending ||
            management.requestPublish.isPending
          }
          onRetry={() => void management.publishRequests.refetch()}
          onAct={(instanceId, action, reasonCode) =>
            management.actOnPublishRequest.mutate({ instanceId, action, reasonCode })
          }
          onTransfer={(instanceId, targetAccountId) =>
            management.transferPublishRequest.mutate({ instanceId, targetAccountId })
          }
          onLoadVersions={(knowledgeBaseId, documentId) =>
            management.loadDocumentVersions.mutate({ knowledgeBaseId, documentId })
          }
          versionsLoading={management.loadDocumentVersions.isPending}
          versionsDocumentId={management.loadDocumentVersions.variables?.documentId ?? null}
          versionsDetail={management.loadDocumentVersions.data}
          requestPending={management.requestPublish.isPending}
          onRequestPublish={(documentId, documentVersionId, idempotencyKey, onSuccess) =>
            management.requestPublish.mutate(
              { documentId, documentVersionId, idempotencyKey },
              { onSuccess },
            )
          }
        />
      )}
      <CategoryEditor
        snapshot={snapshot}
        category={editingCategory}
        parentCategoryId={categoryParentId}
        open={categoryEditorOpen}
        pending={management.createCategory.isPending || management.updateCategory.isPending}
        onClose={() => setCategoryEditorOpen(false)}
        onSubmit={(values: CategoryFormValues) => {
          if (editingCategory) {
            management.updateCategory.mutate(
              { category: editingCategory, body: categoryUpdateBody(editingCategory, values) },
              { onSuccess: () => setCategoryEditorOpen(false) },
            );
          } else {
            management.createCategory.mutate(categoryCreateBody(values), {
              onSuccess: () => setCategoryEditorOpen(false),
            });
          }
        }}
      />
      <CategoryDocumentBinder
        snapshot={snapshot}
        category={bindingCategory}
        pending={management.bindCategoryDocuments.isPending}
        onClose={() => setBindingCategory(null)}
        onSubmit={(documentIds) => {
          if (!bindingCategory) return;
          management.bindCategoryDocuments.mutate(
            { category: bindingCategory, documentIds },
            { onSuccess: () => setBindingCategory(null) },
          );
        }}
      />
      <DomainEditor
        snapshot={snapshot}
        domain={editingDomain}
        open={domainEditorOpen}
        pending={management.createDomain.isPending || management.updateDomain.isPending}
        onClose={() => setDomainEditorOpen(false)}
        onSubmit={(values: DomainFormValues) => {
          if (editingDomain) {
            management.updateDomain.mutate(
              { domain: editingDomain, body: domainUpdateBody(editingDomain, values) },
              { onSuccess: () => setDomainEditorOpen(false) },
            );
          } else {
            management.createDomain.mutate(domainCreateBody(values), {
              onSuccess: () => setDomainEditorOpen(false),
            });
          }
        }}
      />
      <DomainScopeEditor
        snapshot={snapshot}
        domain={scopeDomain}
        pending={management.replaceDomainScope.isPending}
        onClose={() => setScopeDomain(null)}
        onSubmit={(values: DomainScopeFormValues) => {
          if (!scopeDomain) return;
          management.replaceDomainScope.mutate(
            { domain: scopeDomain, body: domainScopeBody(scopeDomain, values) },
            { onSuccess: () => setScopeDomain(null) },
          );
        }}
      />
      <ScopeExplanationDrawer
        snapshot={snapshot}
        domain={explainingDomain}
        result={management.resolveDomainScope.data}
        loading={management.resolveDomainScope.isPending}
        onClose={() => {
          setExplainingDomain(null);
          management.resolveDomainScope.reset();
        }}
      />
    </>
  );
}
