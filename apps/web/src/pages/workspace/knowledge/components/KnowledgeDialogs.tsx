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
  createOpen: boolean;
  uploadOpen: boolean;
  versionDocument: KnowledgeDocumentSummary | null;
  isCreating: boolean;
  isUploading: boolean;
  onCloseCreate: () => void;
  onCloseUpload: () => void;
  onCloseVersion: () => void;
  onCreate: (values: CreateKnowledgeBaseRequest) => Promise<unknown>;
  onUpload: (values: UploadDocumentValues) => Promise<unknown>;
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
