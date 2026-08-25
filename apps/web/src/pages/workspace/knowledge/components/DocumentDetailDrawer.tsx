/** @description 文档元数据、版本历史、解析与索引状态详情抽屉。 */
import { Alert, Button, Descriptions, Drawer, Skeleton, Tag, Tooltip } from "antd";
import { Download, FileText, ScanText } from "lucide-react";

import type { KnowledgeDocumentDetail } from "@/api/services/knowledge";
import { StateView } from "@/components/StateView/StateView";

import {
  documentStatus,
  formatTimestamp,
  indexStatus,
  ingestionStatus,
  securityLevelLabels,
  visibilityLabels,
} from "../config";

interface DocumentDetailDrawerProps {
  /** 详情抽屉是否打开。 */
  open: boolean;
  /** 服务端按资源范围返回的文档详情。 */
  detail: KnowledgeDocumentDetail | undefined;
  /** 首次读取详情是否进行中。 */
  isLoading: boolean;
  /** 详情读取失败后的稳定展示文案。 */
  errorDescription: string | null;
  /** 只控制下载入口，服务端仍重新授权具体版本。 */
  canDownload: boolean;
  /** 当前是否有版本正在下载。 */
  isDownloading: boolean;
  /** 关闭详情抽屉。 */
  onClose: () => void;
  /** 重新读取详情。 */
  onRetry: () => void;
  /** 下载指定不可变版本的原文件。 */
  onDownload: (documentId: string, documentVersionId: string) => void;
}

/** 展示不可变版本处理链，错误详情只来自服务端可公开的稳定状态字段。 */
export function DocumentDetailDrawer(props: DocumentDetailDrawerProps) {
  const detail = props.detail;
  const title = detail?.document.title ?? "文档详情";
  return (
    <Drawer
      open={props.open}
      size={720}
      title={title}
      destroyOnHidden
      onClose={props.onClose}
      styles={{ body: { padding: 0 } }}
    >
      {props.isLoading ? (
        <div className="p-6">
          <Skeleton active paragraph={{ rows: 8 }} />
        </div>
      ) : props.errorDescription ? (
        <StateView
          kind="error"
          title="文档详情未能加载"
          description={props.errorDescription}
          action={<Button onClick={props.onRetry}>重新加载</Button>}
        />
      ) : detail ? (
        <div>
          <section className="border-b border-b-solid border-border px-6 py-5 phone-down:px-4">
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <Tag>{visibilityLabels[detail.document.visibility]}</Tag>
              <Tag>{securityLevelLabels[detail.document.security_level]}</Tag>
              <span className="text-sm text-text-muted">共 {detail.versions.length} 个版本</span>
            </div>
            <Descriptions
              size="small"
              column={{ xs: 1, sm: 2 }}
              items={[
                {
                  key: "created",
                  label: "创建时间",
                  children: formatTimestamp(detail.document.created_at),
                },
                {
                  key: "updated",
                  label: "更新时间",
                  children: formatTimestamp(detail.document.updated_at),
                },
                {
                  key: "creator",
                  label: "创建账号",
                  children: detail.document.created_by_account_id,
                },
                {
                  key: "labels",
                  label: "权限标签",
                  children: detail.document.permission_labels.length
                    ? detail.document.permission_labels.join("、")
                    : "-",
                },
              ]}
            />
          </section>
          <div aria-label="版本历史">
            {detail.versions.map((item) => {
              const version = item.version;
              const current = detail.current_document_version_id === version.document_version_id;
              const versionStatus = documentStatus[version.status];
              const parsingStatus = item.ingestion ? ingestionStatus[item.ingestion.status] : null;
              const buildStatus = item.index ? indexStatus[item.index.status] : null;
              return (
                <section
                  key={version.document_version_id}
                  className="border-b border-b-solid border-border px-6 py-5 last:border-b-0 phone-down:px-4"
                >
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <span className="grid h-8 w-8 place-items-center rounded-md bg-surface-subtle text-text-muted">
                        <FileText size={17} />
                      </span>
                      <strong>V{version.version_number}</strong>
                      <Tag color={versionStatus.color}>{versionStatus.label}</Tag>
                      {current && <Tag color="green">当前发布版</Tag>}
                    </div>
                    {props.canDownload && item.source.download_available && (
                      <Tooltip title={`下载 ${item.source.source_name}`}>
                        <Button
                          icon={<Download size={16} />}
                          loading={props.isDownloading}
                          onClick={() =>
                            props.onDownload(
                              detail.document.document_id,
                              version.document_version_id,
                            )
                          }
                        >
                          下载原文件
                        </Button>
                      </Tooltip>
                    )}
                  </div>
                  <Descriptions
                    size="small"
                    column={{ xs: 1, sm: 2 }}
                    items={[
                      { key: "source", label: "来源文件", children: item.source.source_name },
                      { key: "type", label: "文件类型", children: item.source.media_type ?? "-" },
                      {
                        key: "size",
                        label: "文件大小",
                        children: formatFileSize(item.source.size_bytes),
                      },
                      {
                        key: "created",
                        label: "版本时间",
                        children: formatTimestamp(version.created_at),
                      },
                      {
                        key: "scan",
                        label: "安全扫描",
                        children: item.source.scan_status === "clean" ? "已通过" : "无扫描记录",
                      },
                      {
                        key: "published",
                        label: "发布时间",
                        children: formatTimestamp(version.published_at),
                      },
                    ]}
                  />
                  <div className="mt-4 grid grid-cols-2 gap-4 tablet-down:grid-cols-1">
                    <div className="border-l-2 border-l-solid border-l-border px-3">
                      <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
                        <ScanText size={16} />
                        解析与 OCR
                        {parsingStatus && (
                          <Tag color={parsingStatus.color}>{parsingStatus.label}</Tag>
                        )}
                      </div>
                      {item.ingestion ? (
                        <div className="grid gap-1 text-sm text-text-muted">
                          <span>解析器：{item.ingestion.parser_name ?? "等待分配"}</span>
                          <span>
                            OCR：
                            {item.ingestion.ocr_used === null
                              ? "待确认"
                              : item.ingestion.ocr_used
                                ? "已使用"
                                : "未使用"}
                          </span>
                          <span>
                            页数 / 内容块：{item.ingestion.page_count ?? "-"} /{" "}
                            {item.ingestion.block_count ?? "-"}
                          </span>
                        </div>
                      ) : (
                        <span className="text-sm text-text-muted">此来源不需要文件解析任务</span>
                      )}
                    </div>
                    <div className="border-l-2 border-l-solid border-l-border px-3">
                      <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
                        索引构建
                        {buildStatus && <Tag color={buildStatus.color}>{buildStatus.label}</Tag>}
                      </div>
                      {item.index ? (
                        <div className="grid gap-1 text-sm text-text-muted">
                          <span>构建批次：#{item.index.build_no}</span>
                          <span>
                            Chunk：{item.index.chunk_count ?? item.index.staged_chunk_count ?? "-"}
                          </span>
                          <span>完成时间：{formatTimestamp(item.index.completed_at)}</span>
                        </div>
                      ) : (
                        <span className="text-sm text-text-muted">尚未生成索引构建</span>
                      )}
                    </div>
                  </div>
                  {(item.ingestion?.error_message || item.index?.error_message) && (
                    <Alert
                      className="mt-4"
                      type="error"
                      showIcon
                      message={item.ingestion?.error_code ?? item.index?.error_code ?? "处理失败"}
                      description={item.ingestion?.error_message ?? item.index?.error_message}
                    />
                  )}
                </section>
              );
            })}
          </div>
        </div>
      ) : null}
    </Drawer>
  );
}

function formatFileSize(value: number | null): string {
  if (value === null) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
