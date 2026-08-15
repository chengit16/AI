/** @description 知识生产页面查询、上传、状态动作和错误恢复测试。 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
        canUploadVersion={false}
        canMarkReady
        canPublish
        isMutating={false}
        onUploadVersion={vi.fn()}
        onMarkReady={onMarkReady}
        onPublish={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "确认就绪" }));
    expect(onMarkReady).toHaveBeenCalledWith(document, parsedHash);
  });
});
