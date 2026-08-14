/** @description 知识库创建、首版上传和新版本上传弹窗集合。 */
import { Button, Form, Input, Modal, Select, Upload } from "antd";
import type { UploadFile } from "antd";
import { Upload as UploadIcon } from "lucide-react";
import { useState } from "react";

import type {
  CreateKnowledgeBaseRequest,
  KnowledgeDocumentSummary,
} from "@/api/services/knowledge";

import type { UploadDocumentValues } from "../useKnowledgeProduction";

interface KnowledgeDialogsProps {
  /** 是否打开知识库创建弹窗。 */
  createOpen: boolean;
  /** 是否打开文档首版上传弹窗。 */
  uploadOpen: boolean;
  /** 当前准备上传新版本的文档；为空时关闭对应弹窗。 */
  versionDocument: KnowledgeDocumentSummary | null;
  /** 知识库创建命令是否正在提交。 */
  isCreating: boolean;
  /** 首版或新版本上传是否正在提交。 */
  isUploading: boolean;
  /** 关闭创建弹窗。 */
  onCloseCreate: () => void;
  /** 关闭首版上传弹窗。 */
  onCloseUpload: () => void;
  /** 关闭新版本上传弹窗。 */
  onCloseVersion: () => void;
  /** 提交知识库创建请求。 */
  onCreate: (values: CreateKnowledgeBaseRequest) => Promise<unknown>;
  /** 提交文档标题、可见范围、敏感级别和文件。 */
  onUpload: (values: UploadDocumentValues) => Promise<unknown>;
  /** 为指定文档提交一个不可变新版本文件。 */
  onUploadVersion: (documentId: string, file: File) => Promise<unknown>;
}

interface UploadFormValues {
  title: string;
  visibility: "private" | "workspace";
  securityLevel: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
}

function selectedFile(files: UploadFile[]): File | null {
  const file = files[0]?.originFileObj;
  return file instanceof File ? file : null;
}

/** 编排知识库与文档上传表单，只在服务端成功后清空对应本地状态。 */
export function KnowledgeDialogs({
  createOpen,
  uploadOpen,
  versionDocument,
  isCreating,
  isUploading,
  onCloseCreate,
  onCloseUpload,
  onCloseVersion,
  onCreate,
  onUpload,
  onUploadVersion,
}: KnowledgeDialogsProps) {
  const [createForm] = Form.useForm<CreateKnowledgeBaseRequest>();
  const [uploadForm] = Form.useForm<UploadFormValues>();
  const [uploadFiles, setUploadFiles] = useState<UploadFile[]>([]);
  const [versionFiles, setVersionFiles] = useState<UploadFile[]>([]);

  const finishCreate = async () => {
    const values = await createForm.validateFields();
    await onCreate(values);
    createForm.resetFields();
    onCloseCreate();
  };
  const finishUpload = async () => {
    const values = await uploadForm.validateFields();
    const file = selectedFile(uploadFiles);
    if (!file) return;
    await onUpload({ ...values, file });
    uploadForm.resetFields();
    setUploadFiles([]);
    onCloseUpload();
  };
  const finishVersion = async () => {
    const file = selectedFile(versionFiles);
    if (!file || !versionDocument) return;
    await onUploadVersion(versionDocument.document_id, file);
    setVersionFiles([]);
    onCloseVersion();
  };

  return (
    <>
      <Modal
        title="创建知识库"
        open={createOpen}
        okText="创建"
        cancelText="取消"
        confirmLoading={isCreating}
        onCancel={onCloseCreate}
        onOk={() => void finishCreate().catch(() => undefined)}
      >
        <Form
          form={createForm}
          layout="vertical"
          requiredMark={false}
          initialValues={{ default_visibility: "private", default_security_level: "INTERNAL" }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true }, { max: 120 }]}>
            <Input autoFocus placeholder="例如：产品与交付知识库" />
          </Form.Item>
          <Form.Item label="说明" name="description" rules={[{ max: 1000 }]}>
            <Input.TextArea rows={3} placeholder="说明知识边界和维护责任" />
          </Form.Item>
          <Form.Item label="默认可见范围" name="default_visibility">
            <Select
              options={[
                { value: "private", label: "仅创建者" },
                { value: "workspace", label: "整个空间" },
              ]}
            />
          </Form.Item>
          <Form.Item label="默认密级" name="default_security_level">
            <Select
              options={[
                { value: "PUBLIC", label: "公开" },
                { value: "INTERNAL", label: "内部" },
                { value: "CONFIDENTIAL", label: "机密" },
                { value: "RESTRICTED", label: "严格限制" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="上传文档"
        open={uploadOpen}
        okText="上传并解析"
        cancelText="取消"
        confirmLoading={isUploading}
        okButtonProps={{ disabled: uploadFiles.length === 0 }}
        onCancel={onCloseUpload}
        onOk={() => void finishUpload().catch(() => undefined)}
      >
        <Form
          form={uploadForm}
          layout="vertical"
          requiredMark={false}
          initialValues={{ visibility: "private", securityLevel: "INTERNAL" }}
        >
          <Form.Item label="文档名称" name="title" rules={[{ required: true }, { max: 255 }]}>
            <Input placeholder="用于知识库内识别" />
          </Form.Item>
          <Form.Item label="原始文件" required>
            <Upload
              accept=".pdf,.docx,.md,.txt,.html,.htm,.png,.jpg,.jpeg"
              fileList={uploadFiles}
              maxCount={1}
              beforeUpload={() => false}
              onChange={({ fileList }) => setUploadFiles(fileList.slice(-1))}
            >
              <Button icon={<UploadIcon size={16} />}>选择文件</Button>
            </Upload>
          </Form.Item>
          <Form.Item label="可见范围" name="visibility">
            <Select
              options={[
                { value: "private", label: "仅创建者" },
                { value: "workspace", label: "整个空间" },
              ]}
            />
          </Form.Item>
          <Form.Item label="密级" name="securityLevel">
            <Select
              options={[
                { value: "PUBLIC", label: "公开" },
                { value: "INTERNAL", label: "内部" },
                { value: "CONFIDENTIAL", label: "机密" },
                { value: "RESTRICTED", label: "严格限制" },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`上传新版本${versionDocument ? `：${versionDocument.title}` : ""}`}
        open={Boolean(versionDocument)}
        okText="上传新版本"
        cancelText="取消"
        confirmLoading={isUploading}
        okButtonProps={{ disabled: versionFiles.length === 0 }}
        onCancel={onCloseVersion}
        onOk={() => void finishVersion().catch(() => undefined)}
      >
        <Upload
          accept=".pdf,.docx,.md,.txt,.html,.htm,.png,.jpg,.jpeg"
          fileList={versionFiles}
          maxCount={1}
          beforeUpload={() => false}
          onChange={({ fileList }) => setVersionFiles(fileList.slice(-1))}
        >
          <Button icon={<UploadIcon size={16} />}>选择新版本文件</Button>
        </Upload>
      </Modal>
    </>
  );
}
