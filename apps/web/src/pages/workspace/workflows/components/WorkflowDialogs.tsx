/** @description 工作流创建和运行输入对话框。 */
import { Form, Input, Modal } from "antd";
import { useState } from "react";

import type { CreateWorkflowRequest } from "@/api/services/workflows";

import { createStarterGraph } from "../config";

/** 工作流页面两个短命令对话框的开关、状态和提交回调。 */
export interface WorkflowDialogsProps {
  /** 创建工作流对话框是否打开。 */
  createOpen: boolean;
  /** 启动运行对话框是否打开。 */
  runOpen: boolean;
  /** 创建命令提交状态。 */
  isCreating: boolean;
  /** 启动运行命令提交状态。 */
  isRunning: boolean;
  /** 关闭创建对话框。 */
  onCloseCreate: () => void;
  /** 关闭运行对话框。 */
  onCloseRun: () => void;
  /** 创建工作流及有效最小图。 */
  onCreate: (body: CreateWorkflowRequest) => Promise<unknown>;
  /** 使用解析后的 JSON 对象启动运行。 */
  onRun: (input: Record<string, unknown>) => Promise<unknown>;
}

/** 收集名称和说明，并用最小合法图创建可立即编辑的工作流。 */
function CreateWorkflowDialog(props: WorkflowDialogsProps) {
  const [form] = Form.useForm<{ name: string; description?: string }>();
  const submit = async () => {
    const value = await form.validateFields();
    await props.onCreate({
      name: value.name.trim(),
      description: value.description?.trim() || null,
      graph: createStarterGraph(),
    });
    form.resetFields();
    props.onCloseCreate();
  };
  return (
    <Modal
      title="创建工作流"
      open={props.createOpen}
      okText="创建"
      cancelText="取消"
      confirmLoading={props.isCreating}
      onOk={() => void submit()}
      onCancel={props.onCloseCreate}
    >
      <Form form={form} layout="vertical">
        <Form.Item
          name="name"
          label="名称"
          rules={[{ required: true, whitespace: true, max: 120 }]}
        >
          <Input autoFocus />
        </Form.Item>
        <Form.Item name="description" label="说明" rules={[{ max: 1000 }]}>
          <Input.TextArea rows={4} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

/** 解析受限 JSON 对象后启动当前发布版本，非法输入不会离开浏览器。 */
function RunWorkflowDialog(props: WorkflowDialogsProps) {
  const [input, setInput] = useState("{}");
  const [error, setError] = useState(false);
  const submit = async () => {
    try {
      const value: unknown = JSON.parse(input);
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
      setError(false);
      await props.onRun(value as Record<string, unknown>);
      props.onCloseRun();
    } catch {
      setError(true);
    }
  };
  return (
    <Modal
      title="启动工作流"
      open={props.runOpen}
      okText="启动"
      cancelText="取消"
      confirmLoading={props.isRunning}
      onOk={() => void submit()}
      onCancel={props.onCloseRun}
    >
      <label className="text-xs font-650 text-text-muted">
        输入 JSON
        <Input.TextArea
          className="mt-1 font-mono"
          value={input}
          rows={10}
          status={error ? "error" : undefined}
          aria-invalid={error}
          onChange={(event) => setInput(event.target.value)}
        />
      </label>
    </Modal>
  );
}

/** 渲染页面级创建和运行对话框，避免关闭页面时遗留局部状态。 */
export function WorkflowDialogs(props: WorkflowDialogsProps) {
  return (
    <>
      <CreateWorkflowDialog {...props} />
      <RunWorkflowDialog {...props} />
    </>
  );
}
