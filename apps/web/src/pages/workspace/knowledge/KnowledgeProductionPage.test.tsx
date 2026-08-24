/** @description 知识生产页面查询、上传、状态动作和错误恢复测试。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { IngestionJob, KnowledgeDocumentSummary } from "@/api/services/knowledge";

import { DocumentTable } from "./components/DocumentTable";
import { IngestionTable } from "./components/IngestionTable";

const job: IngestionJob = {
  ingestion_job_id: "50000000-0000-4000-8000-000000000701",
  knowledge_base_id: "30000000-0000-4000-8000-000000000701",
  document_id: "40000000-0000-4000-8000-000000000701",
  document_version_id: "41000000-0000-4000-8000-000000000701",
  source_id: "42000000-0000-4000-8000-000000000701",
  source_name: "synthetic.md",
  source_media_type: "text/markdown",
  status: "failed",
  attempt_count: 3,
  max_attempts: 3,
  available_at: "2026-08-14T10:00:00Z",
  started_at: "2026-08-14T10:00:01Z",
  completed_at: "2026-08-14T10:00:02Z",
  failure_stage: "parse",
  error_code: "INGESTION_PARSER_UNAVAILABLE",
  error_message: "合成解析服务暂时不可用",
  parsed_content_hash: null,
  parser_name: null,
  ocr_used: null,
  page_count: null,
  block_count: null,
  can_retry_manually: true,
  can_cancel: false,
  manual_retry_count: 0,
  last_retried_at: null,
  cancelled_at: null,
  created_at: "2026-08-14T10:00:00Z",
  updated_at: "2026-08-14T10:00:02Z",
};

const document: KnowledgeDocumentSummary = {
  document_id: job.document_id,
  title: "合成产品手册",
  visibility: "workspace",
  security_level: "INTERNAL",
  updated_at: "2026-08-14T10:00:02Z",
  latest_version: {
    document_version_id: job.document_version_id,
    workspace_id: "20000000-0000-4000-8000-000000000701",
    document_id: job.document_id,
    version_number: 1,
    status: "draft",
    content_hash: null,
    created_by_account_id: "10000000-0000-4000-8000-000000000701",
    created_at: "2026-08-14T10:00:00Z",
    published_at: null,
    record_version: 1,
  },
  source_id: job.source_id,
  source_kind: "upload",
  source_name: job.source_name,
  current_document_version_id: null,
  folder_id: "60000000-0000-4000-8000-000000000701",
  tag_ids: [],
  is_favorite: false,
};

describe("P1D-07 知识生产表格", () => {
  afterEach(cleanup);

  it("只有暂时性失败且具备菜单权限时提供人工重试", () => {
    const onRetry = vi.fn();
    render(
      <IngestionTable
        items={[job, { ...job, ingestion_job_id: "permanent", can_retry_manually: false }]}
        isLoading={false}
        canRetry
        isRetrying={false}
        onRetry={onRetry}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(onRetry).toHaveBeenCalledWith(job.ingestion_job_id);
    expect(screen.getByText("不可恢复")).toBeInTheDocument();
  });

  it("区分超时、取消和普通等待状态，避免把稳定终态显示为处理中", () => {
    render(
      <IngestionTable
        items={[
          {
            ...job,
            ingestion_job_id: "timed-out",
            status: "timed_out",
            error_code: "INGESTION_WORKER_LEASE_EXPIRED",
            error_message: "合成租约已过期",
          },
          {
            ...job,
            ingestion_job_id: "cancelled",
            status: "cancelled",
            failure_stage: null,
            error_code: null,
            error_message: null,
            cancelled_at: "2026-08-15T00:00:00Z",
          },
        ]}
        isLoading={false}
        canRetry={false}
        isRetrying={false}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText("已超时")).toBeInTheDocument();
    expect(screen.getByText("INGESTION_WORKER_LEASE_EXPIRED")).toBeInTheDocument();
    expect(screen.getByText("已取消")).toBeInTheDocument();
    expect(screen.getByText("任务已由用户取消")).toBeInTheDocument();
    expect(screen.queryByText("等待处理")).not.toBeInTheDocument();
  });

  it("解析成功后以服务端摘要确认当前文档版本就绪", () => {
    const onMarkReady = vi.fn();
    const parsedHash = "b".repeat(64);
    render(
      <DocumentTable
        items={[document]}
        jobs={[
          {
            ...job,
            status: "succeeded",
            can_retry_manually: false,
            parsed_content_hash: parsedHash,
          },
        ]}
        isLoading={false}
        folders={[]}
        tags={[]}
        selectedDocumentIds={[]}
        canUploadVersion={false}
        canMarkReady
        canPublish
        canOrganize={false}
        canFavorite={false}
        canDelete={false}
        isMutating={false}
        onSelectionChange={vi.fn()}
        onUploadVersion={vi.fn()}
        onMarkReady={onMarkReady}
        onPublish={vi.fn()}
        onMove={vi.fn()}
        onSetTags={vi.fn()}
        onFavorite={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "确认就绪" }));
    expect(onMarkReady).toHaveBeenCalledWith(document, parsedHash);
  });

  it("展示目录与标签事实，并从操作菜单切换收藏", async () => {
    const onFavorite = vi.fn();
    // 1. 以完整目录和标签读模型渲染用户可见表格，避免只验证内部字段。
    render(
      <DocumentTable
        items={[{ ...document, tag_ids: ["62000000-0000-4000-8000-000000000701"] }]}
        jobs={[]}
        folders={[
          {
            folder_id: document.folder_id,
            workspace_id: "20000000-0000-4000-8000-000000000701",
            name: "合成产品资料",
            parent_folder_id: null,
            created_by_account_id: "10000000-0000-4000-8000-000000000701",
            created_at: "2026-08-14T10:00:00Z",
            updated_at: "2026-08-14T10:00:00Z",
            status: "active",
            deleted_at: null,
            version: 1,
            is_default: false,
          },
        ]}
        tags={[
          {
            tag_id: "62000000-0000-4000-8000-000000000701",
            workspace_id: "20000000-0000-4000-8000-000000000701",
            name: "产品",
            color: "#176b52",
            created_by_account_id: "10000000-0000-4000-8000-000000000701",
            created_at: "2026-08-14T10:00:00Z",
            updated_at: "2026-08-14T10:00:00Z",
            status: "active",
            deleted_at: null,
            version: 1,
          },
        ]}
        selectedDocumentIds={[]}
        isLoading={false}
        canUploadVersion={false}
        canMarkReady={false}
        canPublish={false}
        canOrganize
        canFavorite
        canDelete={false}
        isMutating={false}
        onSelectionChange={vi.fn()}
        onUploadVersion={vi.fn()}
        onMarkReady={vi.fn()}
        onPublish={vi.fn()}
        onMove={vi.fn()}
        onSetTags={vi.fn()}
        onFavorite={onFavorite}
        onDelete={vi.fn()}
      />,
    );

    // 2. 从真实操作菜单触发收藏回调，同时确认组织名称已经还原。
    expect(screen.getByText("合成产品资料")).toBeInTheDocument();
    expect(screen.getByText("产品")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开 合成产品手册 操作菜单" }));
    fireEvent.click(await screen.findByText("收藏"));
    await waitFor(() =>
      expect(onFavorite).toHaveBeenCalledWith({
        ...document,
        tag_ids: ["62000000-0000-4000-8000-000000000701"],
      }),
    );
  });

  it("允许页面为筛选结果提供上下文化空状态", () => {
    render(
      <DocumentTable
        items={[]}
        jobs={[]}
        folders={[]}
        tags={[]}
        selectedDocumentIds={[]}
        isLoading={false}
        emptyTitle="没有匹配的文档"
        emptyDescription="调整搜索、标签或文件夹筛选后重试。"
        canUploadVersion={false}
        canMarkReady={false}
        canPublish={false}
        canOrganize={false}
        canFavorite={false}
        canDelete={false}
        isMutating={false}
        onSelectionChange={vi.fn()}
        onUploadVersion={vi.fn()}
        onMarkReady={vi.fn()}
        onPublish={vi.fn()}
        onMove={vi.fn()}
        onSetTags={vi.fn()}
        onFavorite={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    expect(screen.getByText("没有匹配的文档")).toBeInTheDocument();
    expect(screen.getByText("调整搜索、标签或文件夹筛选后重试。")).toBeInTheDocument();
    expect(
      screen.queryByText("上传首份文档后，可以在这里跟踪解析和发布状态。"),
    ).not.toBeInTheDocument();
  });
});
