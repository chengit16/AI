/** @description 文件夹创建、重命名和移动弹窗。 */
import { Form, Input, Modal, Select } from "antd";
import { useEffect } from "react";

import type {
  CreateKnowledgeFolderRequest,
  KnowledgeFolder,
} from "@/api/services/knowledgeOrganization";

import type { FolderEditAction } from "./KnowledgeNavigationRail";

interface FolderDialogProps {
  /** 当前操作；为空时关闭弹窗。 */
  action: FolderEditAction | null;
  /** 重命名/移动目标，或创建子目录时的父目录。 */
  folder: KnowledgeFolder | null;
  /** 可作为父级的活动目录。 */
  folders: readonly KnowledgeFolder[];
  /** 目录命令是否正在提交。 */
  isPending: boolean;
  /** 关闭弹窗并放弃未提交表单。 */
  onClose: () => void;
  /** 提交目录创建。 */
  onCreate: (body: CreateKnowledgeFolderRequest) => Promise<unknown>;
  /** 提交目录重命名。 */
  onRename: (folderId: string, name: string) => Promise<unknown>;
  /** 提交目录移动。 */
  onMove: (folderId: string, parentFolderId: string | null) => Promise<unknown>;
}

interface FolderFormValues {
  name: string;
  parentFolderId: string;
}

const actionTitles: Record<FolderEditAction, string> = {
  create: "新建文件夹",
  rename: "重命名文件夹",
  move: "移动文件夹",
};

/** 收集目录命令字段，循环、跨空间和重名关系仍交由服务端最终校验。 */
export function FolderDialog(props: FolderDialogProps) {
  const [form] = Form.useForm<FolderFormValues>();
  const defaultFolder = props.folders.find((folder) => folder.is_default);

  useEffect(() => {
    if (!props.action) return;
    form.setFieldsValue({
      name: props.action === "rename" ? (props.folder?.name ?? "") : "",
      parentFolderId:
        props.action === "create"
          ? (props.folder?.folder_id ?? defaultFolder?.folder_id ?? "")
          : (props.folder?.parent_folder_id ?? defaultFolder?.folder_id ?? ""),
    });
  }, [defaultFolder?.folder_id, form, props.action, props.folder]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    if (props.action === "create") {
      await props.onCreate({
        name: values.name,
        parent_folder_id: values.parentFolderId || null,
      });
    } else if (props.action === "rename" && props.folder) {
      await props.onRename(props.folder.folder_id, values.name);
    } else if (props.action === "move" && props.folder) {
      await props.onMove(props.folder.folder_id, values.parentFolderId || null);
    }
    form.resetFields();
    props.onClose();
  };

  const parentOptions = props.folders
    .filter((folder) => folder.folder_id !== props.folder?.folder_id)
    .map((folder) => ({ value: folder.folder_id, label: folder.name }));

  return (
    <Modal
      title={props.action ? actionTitles[props.action] : "文件夹"}
      open={props.action !== null}
      okText="保存"
      cancelText="取消"
      confirmLoading={props.isPending}
      onCancel={props.onClose}
      onOk={() => void handleSubmit().catch(() => undefined)}
    >
      <Form form={form} layout="vertical" requiredMark={false}>
        {props.action !== "move" && (
          <Form.Item label="名称" name="name" rules={[{ required: true }, { max: 120 }]}>
            <Input autoFocus />
          </Form.Item>
        )}
        {props.action !== "rename" && (
          <Form.Item label="上级文件夹" name="parentFolderId" rules={[{ required: true }]}>
            <Select options={parentOptions} />
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}
