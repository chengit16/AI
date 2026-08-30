/** @description 会话级知识范围和临时附件控制条。 */
import {
  Button,
  Segmented,
  Select,
  Tag,
  Tooltip,
  Typography,
  Upload,
  type UploadProps,
} from "antd";
import { Archive, Paperclip, Save, Trash2 } from "lucide-react";
import { useState } from "react";

import type {
  AssistantConversation,
  ConversationAttachment,
  ConversationScopeRequest,
} from "@/api/services/assistant";
import type { KnowledgeBaseSummary } from "@/api/services/knowledge";
import type { KnowledgeTag } from "@/api/services/knowledgeOrganization";

interface AssistantContextBarProps {
  /** 当前会话事实；切换会话时本地草稿重新取服务端值。 */
  conversation: AssistantConversation;
  /** 当前空间中可供会话选择的知识库。 */
  knowledgeBases: readonly KnowledgeBaseSummary[];
  /** 当前空间中可供会话选择的活动标签。 */
  knowledgeTags: readonly KnowledgeTag[];
  /** 会话下尚未归档的临时文本附件。 */
  attachments: readonly ConversationAttachment[];
  /** 当前账号是否拥有修改会话知识范围的权限。 */
  canManageScope: boolean;
  /** 当前账号是否拥有上传和删除临时附件的权限。 */
  canManageAttachments: boolean;
  /** 活动 Run 是否正在锁定范围和附件操作。 */
  busy: boolean;
  /** 知识范围保存请求是否进行中。 */
  savingScope: boolean;
  /** 附件上传请求是否进行中。 */
  uploading: boolean;
  /** 保存会话知识范围。 */
  onSaveScope: (body: ConversationScopeRequest) => void;
  /** 提交一个临时文本附件。 */
  onUpload: (file: File) => void;
  /** 删除一个临时文本附件。 */
  onDeleteAttachment: (attachmentId: string) => void;
}

/**
 * 把范围草稿与临时附件放在消息线程上方。
 * 范围保存和附件变更在活动 Run 期间全部禁用，页面状态与服务端并发规则保持一致。
 */
export function AssistantContextBar({
  conversation,
  knowledgeBases,
  knowledgeTags,
  attachments,
  canManageScope,
  canManageAttachments,
  busy,
  savingScope,
  uploading,
  onSaveScope,
  onUpload,
  onDeleteAttachment,
}: AssistantContextBarProps) {
  const [mode, setMode] = useState<"workspace" | "selected">(conversation.scope_mode);
  const [baseIds, setBaseIds] = useState<string[]>([...conversation.knowledge_base_ids]);
  const [tagIds, setTagIds] = useState<string[]>([...conversation.tag_ids]);

  const changed =
    mode !== conversation.scope_mode ||
    baseIds.join(",") !== conversation.knowledge_base_ids.join(",") ||
    tagIds.join(",") !== conversation.tag_ids.join(",");
  const scopeValid = mode === "workspace" || baseIds.length + tagIds.length > 0;
  const uploadProps: UploadProps = {
    accept: ".txt,.md,.markdown,.csv,.json,text/plain,text/markdown,text/csv,application/json",
    multiple: false,
    showUploadList: false,
    beforeUpload: (file) => {
      onUpload(file as File);
      return Upload.LIST_IGNORE;
    },
  };

  return (
    <div className="border-b border-b-solid border-border bg-surface-subtle px-5 py-3 phone-down:px-3">
      <div className="flex flex-wrap items-center gap-2">
        {conversation.status === "archived" ? (
          <Tag icon={<Archive size={13} />}>已归档</Tag>
        ) : canManageScope ? (
          <>
            <Segmented
              value={mode}
              options={[
                { label: "当前空间", value: "workspace" },
                { label: "指定范围", value: "selected" },
              ]}
              disabled={busy}
              onChange={(value) => {
                const next = value as "workspace" | "selected";
                setMode(next);
                if (next === "workspace") {
                  setBaseIds([]);
                  setTagIds([]);
                }
              }}
            />
            {mode === "selected" && (
              <>
                <Select
                  mode="multiple"
                  className="min-w-[220px] flex-1"
                  maxTagCount="responsive"
                  placeholder="选择知识库"
                  value={baseIds}
                  options={knowledgeBases.map((item) => ({
                    label: item.name,
                    value: item.knowledge_base_id,
                  }))}
                  disabled={busy}
                  onChange={setBaseIds}
                />
                <Select
                  mode="multiple"
                  className="min-w-[200px] flex-1"
                  maxTagCount="responsive"
                  placeholder="选择标签"
                  value={tagIds}
                  options={knowledgeTags
                    .filter((item) => item.status === "active")
                    .map((item) => ({ label: item.name, value: item.tag_id }))}
                  disabled={busy}
                  onChange={setTagIds}
                />
              </>
            )}
            <Tooltip title="保存知识范围">
              <Button
                aria-label="保存知识范围"
                icon={<Save size={16} />}
                loading={savingScope}
                disabled={!changed || !scopeValid || busy}
                onClick={() =>
                  onSaveScope({
                    scope_mode: mode,
                    knowledge_base_ids: mode === "selected" ? baseIds : [],
                    tag_ids: mode === "selected" ? tagIds : [],
                  })
                }
              />
            </Tooltip>
          </>
        ) : (
          <Typography.Text type="secondary" className="text-xs">
            {conversation.scope_mode === "workspace"
              ? "当前空间知识"
              : `指定范围 · ${conversation.knowledge_base_ids.length} 个知识库 · ${conversation.tag_ids.length} 个标签`}
          </Typography.Text>
        )}
      </div>

      {conversation.status === "active" && canManageAttachments && (
        <div className="mt-2 flex min-h-8 flex-wrap items-center gap-2">
          <Upload {...uploadProps} disabled={busy || uploading || attachments.length >= 3}>
            <Button size="small" icon={<Paperclip size={15} />} loading={uploading}>
              添加附件
            </Button>
          </Upload>
          {attachments.map((item) => (
            <Tag key={item.attachment_id} className="!m-0 flex max-w-[240px] items-center gap-1">
              <span className="truncate">{item.file_name}</span>
              <button
                type="button"
                className="inline-flex border-0 bg-transparent p-0 text-text-muted"
                aria-label={`删除附件 ${item.file_name}`}
                disabled={busy}
                onClick={() => onDeleteAttachment(item.attachment_id)}
              >
                <Trash2 size={13} />
              </button>
            </Tag>
          ))}
          <Typography.Text type="secondary" className="text-xs">
            {attachments.length}/3
          </Typography.Text>
        </div>
      )}
    </div>
  );
}
