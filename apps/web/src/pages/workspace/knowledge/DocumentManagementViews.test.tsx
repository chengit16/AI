/** @description P6A-03 文档详情、版本处理链和卡片视图行为测试。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  IngestionJob,
  KnowledgeDocumentDetail,
  KnowledgeDocumentSummary,
} from "@/api/services/knowledge";

import { DocumentCardGrid } from "./components/DocumentCardGrid";
import { DocumentDetailDrawer } from "./components/DocumentDetailDrawer";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000703";
const DOCUMENT_ID = "40000000-0000-4000-8000-000000000703";
const VERSION_ID = "41000000-0000-4000-8000-000000000703";
const SOURCE_ID = "42000000-0000-4000-8000-000000000703";
const FOLDER_ID = "60000000-0000-4000-8000-000000000703";

const job: IngestionJob = {
  ingestion_job_id: "50000000-0000-4000-8000-000000000703",
  knowledge_base_id: "30000000-0000-4000-8000-000000000703",
  document_id: DOCUMENT_ID,
  document_version_id: VERSION_ID,
  source_id: SOURCE_ID,
  source_name: "合成产品手册.pdf",
  source_media_type: "application/pdf",
  status: "succeeded",
  attempt_count: 1,
  max_attempts: 3,
  available_at: "2026-08-25T00:00:00Z",
  started_at: "2026-08-25T00:00:01Z",
  completed_at: "2026-08-25T00:00:02Z",
  failure_stage: null,
  error_code: null,
  error_message: null,
  parsed_content_hash: "a".repeat(64),
  parser_name: "tika-v3",
  ocr_used: true,
  page_count: 12,
  block_count: 48,
  can_retry_manually: false,
  can_cancel: false,
  manual_retry_count: 0,
  last_retried_at: null,
  cancelled_at: null,
  created_at: "2026-08-25T00:00:00Z",
  updated_at: "2026-08-25T00:00:02Z",
};

const summary: KnowledgeDocumentSummary = {
  document_id: DOCUMENT_ID,
  title: "合成产品手册",
  visibility: "workspace",
  security_level: "INTERNAL",
  updated_at: "2026-08-25T00:00:02Z",
  latest_version: {
    document_version_id: VERSION_ID,
    workspace_id: WORKSPACE_ID,
    document_id: DOCUMENT_ID,
    version_number: 2,
    status: "published",
    content_hash: "a".repeat(64),
    created_by_account_id: "10000000-0000-4000-8000-000000000703",
    created_at: "2026-08-25T00:00:00Z",
    published_at: "2026-08-25T00:00:03Z",
    record_version: 3,
  },
  source_id: SOURCE_ID,
  source_kind: "upload",
  source_name: "合成产品手册.pdf",
  current_document_version_id: VERSION_ID,
  folder_id: FOLDER_ID,
  tag_ids: [],
  is_favorite: false,
};

const detail: KnowledgeDocumentDetail = {
  document: {
    document_id: DOCUMENT_ID,
    workspace_id: WORKSPACE_ID,
    knowledge_base_id: job.knowledge_base_id,
    title: summary.title,
    visibility: "workspace",
    department_ids: [],
    security_level: "INTERNAL",
    permission_labels: ["product"],
    status: "active",
    created_by_account_id: summary.latest_version.created_by_account_id,
    created_at: "2026-08-25T00:00:00Z",
    updated_at: "2026-08-25T00:00:03Z",
    deleted_at: null,
    version: 1,
  },
  current_document_version_id: VERSION_ID,
  folder_id: FOLDER_ID,
  tag_ids: [],
  is_favorite: false,
  versions: [
    {
      version: summary.latest_version,
      source: {
        source_id: SOURCE_ID,
        source_kind: "upload",
        source_name: summary.source_name,
        media_type: "application/pdf",
        size_bytes: 2048,
        scan_status: "clean",
        scanned_at: "2026-08-25T00:00:00Z",
        captured_at: null,
        download_available: true,
      },
      ingestion: job,
      index: {
        index_version_id: "51000000-0000-4000-8000-000000000703",
        build_no: 2,
        status: "active",
        chunk_count: 36,
        staged_chunk_count: 36,
        failure_stage: null,
        error_code: null,
        error_message: null,
        completed_at: "2026-08-25T00:00:03Z",
        activated_at: "2026-08-25T00:00:03Z",
        updated_at: "2026-08-25T00:00:03Z",
      },
    },
  ],
};

describe("P6A-03 文档管理视图", () => {
  afterEach(cleanup);

  it("详情抽屉展示解析、OCR、索引与授权下载", () => {
    const onDownload = vi.fn();
    render(
      <DocumentDetailDrawer
        open
        detail={detail}
        isLoading={false}
        errorDescription={null}
        canDownload
        isDownloading={false}
        onClose={vi.fn()}
        onRetry={vi.fn()}
        onDownload={onDownload}
      />,
    );

    expect(screen.getByText("解析器：tika-v3")).toBeInTheDocument();
    expect(screen.getByText("OCR：已使用")).toBeInTheDocument();
    expect(screen.getByText("页数 / 内容块：12 / 48")).toBeInTheDocument();
    expect(screen.getByText("Chunk：36")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "下载原文件" }));
    expect(onDownload).toHaveBeenCalledWith(DOCUMENT_ID, VERSION_ID);
  });

  it("卡片视图保留详情、选择和收藏操作", async () => {
    const onOpenDetails = vi.fn();
    const onSelectionChange = vi.fn();
    const onFavorite = vi.fn();
    // 1. 使用完整权限渲染卡片，确保布局切换不会裁掉列表已有命令。
    render(
      <DocumentCardGrid
        items={[summary]}
        jobs={[job]}
        folders={[]}
        tags={[]}
        selectedDocumentIds={[]}
        isLoading={false}
        emptyTitle="没有文档"
        emptyDescription="上传后显示"
        permissions={{
          readDetails: true,
          uploadVersion: true,
          markReady: true,
          publish: true,
          organize: true,
          favorite: true,
          delete: true,
        }}
        isMutating={false}
        onSelectionChange={onSelectionChange}
        onOpenDetails={onOpenDetails}
        onUploadVersion={vi.fn()}
        onMarkReady={vi.fn()}
        onPublish={vi.fn()}
        onMove={vi.fn()}
        onSetTags={vi.fn()}
        onFavorite={onFavorite}
        onDelete={vi.fn()}
      />,
    );

    // 2. 依次验证详情、批量选择和菜单动作仍传递同一文档事实。
    fireEvent.click(screen.getByRole("button", { name: summary.title }));
    expect(onOpenDetails).toHaveBeenCalledWith(summary);
    fireEvent.click(screen.getByRole("checkbox", { name: `选择文档 ${summary.title}` }));
    expect(onSelectionChange).toHaveBeenCalledWith([DOCUMENT_ID]);
    fireEvent.click(screen.getByRole("button", { name: `打开 ${summary.title} 操作菜单` }));
    fireEvent.click(await screen.findByText("收藏"));
    await waitFor(() => expect(onFavorite).toHaveBeenCalledWith(summary));
  });
});
