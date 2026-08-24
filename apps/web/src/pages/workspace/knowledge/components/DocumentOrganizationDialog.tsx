/** @description 文档移动与标签设置弹窗。 */
import { Form, Modal, Select } from "antd";
import { useEffect } from "react";

import type { KnowledgeDocumentSummary } from "@/api/services/knowledge";
import type { KnowledgeFolder, KnowledgeTag } from "@/api/services/knowledgeOrganization";

/** 文档组织弹窗支持的操作模式。 */
export type DocumentOrganizationMode = "move" | "tags";

interface DocumentOrganizationDialogProps {
  /** 当前操作模式；为空时关闭弹窗。 */
  mode: DocumentOrganizationMode | null;
  /** 待操作的已授权文档。 */
  documents: readonly KnowledgeDocumentSummary[];
  /** 可选活动目录。 */
  folders: readonly KnowledgeFolder[];
  /** 可选活动标签。 */
  tags: readonly KnowledgeTag[];
  /** 组织关系是否正在提交。 */
  isPending: boolean;
  /** 关闭弹窗。 */
  onClose: () => void;
  /** 将全部选中文档移动到一个主目录。 */
  onMove: (folderId: string) => Promise<unknown>;
  /** 为单篇文档替换标签，或为多篇文档批量追加标签。 */
  onSetTags: (tagIds: readonly string[], preserveExisting: boolean) => Promise<unknown>;
}

interface OrganizationFormValues {
  folderId: string;
  tagIds: string[];
}

/** 根据单选或批量语义提交组织关系，最终关系合法性由服务端验证。 */
export function DocumentOrganizationDialog(props: DocumentOrganizationDialogProps) {
  const [form] = Form.useForm<OrganizationFormValues>();

  useEffect(() => {
    if (!props.mode || props.documents.length === 0) return;
    const tagIds = [...new Set(props.documents.flatMap((document) => document.tag_ids))];
    form.setFieldsValue({
      folderId: props.documents[0]?.folder_id,
      tagIds: props.documents.length === 1 ? tagIds : [],
    });
  }, [form, props.documents, props.mode]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    if (props.mode === "move") {
      await props.onMove(values.folderId);
    } else if (props.mode === "tags") {
      await props.onSetTags(values.tagIds ?? [], props.documents.length > 1);
    }
    form.resetFields();
    props.onClose();
  };

  return (
    <Modal
      title={props.mode === "move" ? "移动文档" : "设置标签"}
      open={props.mode !== null}
      okText={props.documents.length > 1 ? `应用到 ${props.documents.length} 篇` : "保存"}
      cancelText="取消"
      confirmLoading={props.isPending}
      onCancel={props.onClose}
      onOk={() => void handleSubmit().catch(() => undefined)}
    >
      <Form form={form} layout="vertical" requiredMark={false}>
        {props.mode === "move" ? (
          <Form.Item label="目标文件夹" name="folderId" rules={[{ required: true }]}>
            <Select
              options={props.folders.map((folder) => ({
                value: folder.folder_id,
                label: folder.name,
              }))}
            />
          </Form.Item>
        ) : (
          <Form.Item label={props.documents.length > 1 ? "批量添加标签" : "文档标签"} name="tagIds">
            <Select
              mode="multiple"
              allowClear
              options={props.tags.map((tag) => ({ value: tag.tag_id, label: tag.name }))}
            />
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}
