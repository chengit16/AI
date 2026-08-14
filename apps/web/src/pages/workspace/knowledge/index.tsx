import { Button, Tabs } from "antd";
import { Plus, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { errorMessage } from "@/api/client";
import type { KnowledgeDocumentSummary } from "@/api/services/knowledge";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import { DocumentTable } from "./components/DocumentTable";
import { IngestionTable } from "./components/IngestionTable";
import { KnowledgeBaseRail } from "./components/KnowledgeBaseRail";
import { KnowledgeDialogs } from "./components/KnowledgeDialogs";
import styles from "./KnowledgeProductionPage.module.css";
import { useKnowledgeProduction } from "./useKnowledgeProduction";

export default function KnowledgeProductionPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("base");
  const activeTab = searchParams.get("view") === "jobs" ? "jobs" : "documents";
  const [createOpen, setCreateOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [versionDocument, setVersionDocument] = useState<KnowledgeDocumentSummary | null>(null);
  const model = useKnowledgeProduction(selectedId);
  const { visiblePermissionCodes } = useWorkspaceMenuNavigation();
  const has = (permissionCode: string) => visiblePermissionCodes.has(permissionCode);

  useEffect(() => {
    if (!selectedId && model.bases.data?.[0]) {
      setSearchParams(
        { base: model.bases.data[0].knowledge_base_id, view: activeTab },
        { replace: true },
      );
    }
  }, [activeTab, model.bases.data, selectedId, setSearchParams]);

  if (model.bases.isError) {
    return (
      <StateView
        kind="error"
        title="知识生产台未能加载"
        description={errorMessage(model.bases.error)}
        action={<Button onClick={() => void model.bases.refetch()}>重新加载</Button>}
      />
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="KNOWLEDGE"
        title="知识生产"
        description="管理知识库、上传文档，并跟踪从安全扫描、解析到发布的完整状态。"
        actions={
          selectedId && has("knowledge.document.create") ? (
            <Button type="primary" icon={<Upload size={17} />} onClick={() => setUploadOpen(true)}>
              上传文档
            </Button>
          ) : undefined
        }
      />
      <div className={styles.workspace}>
        <KnowledgeBaseRail
          items={model.bases.data ?? []}
          selectedId={selectedId}
          isLoading={model.bases.isLoading}
          canCreate={has("knowledge.base.create")}
          onCreate={() => setCreateOpen(true)}
          onSelect={(base) => setSearchParams({ base, view: activeTab })}
        />
        <section className={styles.main} aria-label="知识库生产状态">
          {!selectedId ? (
            <StateView
              kind="empty"
              title="选择一个知识库"
              description="从左侧选择知识库，或先创建一个新的知识库。"
              action={
                has("knowledge.base.create") ? (
                  <Button icon={<Plus size={16} />} onClick={() => setCreateOpen(true)}>
                    创建知识库
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <Tabs
              activeKey={activeTab}
              onChange={(view) => setSearchParams({ base: selectedId, view })}
              items={[
                {
                  key: "documents",
                  label: `文档 ${model.documents.data?.length ?? 0}`,
                  children: (
                    <DocumentTable
                      items={model.documents.data ?? []}
                      jobs={model.jobs.data ?? []}
                      isLoading={model.documents.isLoading}
                      canUploadVersion={has("knowledge.document.version.create")}
                      canMarkReady={has("knowledge.document.version.ready")}
                      canPublish={has("knowledge.document.version.publish")}
                      isMutating={model.markReady.isPending || model.publish.isPending}
                      onUploadVersion={setVersionDocument}
                      onMarkReady={(document, contentHash) =>
                        model.markReady.mutate({ document, contentHash })
                      }
                      onPublish={(document) => model.publish.mutate(document)}
                    />
                  ),
                },
                {
                  key: "jobs",
                  label: `入库任务 ${model.jobs.data?.length ?? 0}`,
                  children: (
                    <IngestionTable
                      items={model.jobs.data ?? []}
                      isLoading={model.jobs.isLoading}
                      canRetry={has("knowledge.ingestion.retry")}
                      isRetrying={model.retryJob.isPending}
                      onRetry={(jobId) => model.retryJob.mutate(jobId)}
                    />
                  ),
                },
              ]}
            />
          )}
        </section>
      </div>
      <KnowledgeDialogs
        createOpen={createOpen}
        uploadOpen={uploadOpen}
        versionDocument={versionDocument}
        isCreating={model.createBase.isPending}
        isUploading={model.uploadDocument.isPending || model.uploadVersion.isPending}
        onCloseCreate={() => setCreateOpen(false)}
        onCloseUpload={() => setUploadOpen(false)}
        onCloseVersion={() => setVersionDocument(null)}
        onCreate={model.createBase.mutateAsync}
        onUpload={model.uploadDocument.mutateAsync}
        onUploadVersion={(documentId, file) =>
          model.uploadVersion.mutateAsync({ documentId, file })
        }
      />
    </>
  );
}
