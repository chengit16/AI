/** @description 选择同 Agent Release 和固定百分比的灰度发布弹窗。 */
import { Form, InputNumber, Modal, Select, Slider } from "antd";
import { useEffect } from "react";

import type { ServiceDeployment } from "@/api/services/services";

import type { ServiceReleaseOption, StartCanaryInput } from "../useServiceManagement";

interface CanaryFormValues {
  releaseId: string;
  canaryPercent: number;
}

/** 灰度弹窗当前服务、Release 选项和命令回调。 */
export interface ServiceCanaryDialogProps {
  /** 是否打开灰度弹窗。 */
  open: boolean;
  /** 当前服务及 generation。 */
  deployment: ServiceDeployment;
  /** 当前空间可见的不可变 Release 选项。 */
  releaseOptions: readonly ServiceReleaseOption[];
  /** Release 选项查询失败，旧选项已清空。 */
  isReleaseOptionsError: boolean;
  /** 灰度命令正在提交。 */
  isSubmitting: boolean;
  /** 关闭弹窗且不提交。 */
  onClose: () => void;
  /** 提交目标 Release、比例和当前 generation。 */
  onSubmit: (input: StartCanaryInput) => Promise<unknown>;
}

/** 仅列出同一 Agent 的其他 Release，服务端仍执行最终来源和状态校验。 */
export function ServiceCanaryDialog(props: ServiceCanaryDialogProps) {
  const [form] = Form.useForm<CanaryFormValues>();
  const canaryPercent = Form.useWatch("canaryPercent", form) ?? 10;
  const options = props.releaseOptions.filter(
    (item) =>
      item.agentId === props.deployment.service.agent_id &&
      item.release.release_id !== props.deployment.route.primary_release_id,
  );

  useEffect(() => {
    if (props.open) form.setFieldValue("canaryPercent", 10);
  }, [form, props.open]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    await props.onSubmit({
      releaseId: values.releaseId,
      canaryPercent: values.canaryPercent,
      expectedGeneration: props.deployment.publication.generation,
    });
    form.resetFields();
    props.onClose();
  };

  return (
    <Modal
      open={props.open}
      title="启动稳定灰度"
      okText="启动灰度"
      cancelText="取消"
      width={620}
      confirmLoading={props.isSubmitting}
      okButtonProps={{ disabled: props.isReleaseOptionsError || options.length === 0 }}
      onCancel={props.onClose}
      onOk={() => void handleSubmit()}
    >
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item
          name="releaseId"
          label="灰度 Release"
          rules={[{ required: true }]}
          extra={props.isReleaseOptionsError ? "Release 选项加载失败，旧选项已清空。" : undefined}
        >
          <Select
            disabled={props.isReleaseOptionsError}
            options={options.map((item) => ({
              value: item.release.release_id,
              label: `${item.agentName} · v${item.release.version} · ${item.release.release_id.slice(0, 8)}`,
            }))}
          />
        </Form.Item>
        <Form.Item label="灰度比例" required>
          <div className="grid grid-cols-[minmax(0,1fr)_90px] items-center gap-4">
            <Slider
              min={1}
              max={99}
              value={canaryPercent}
              onChange={(value) => form.setFieldValue("canaryPercent", value)}
            />
            <Form.Item name="canaryPercent" noStyle rules={[{ required: true, type: "number" }]}>
              <InputNumber min={1} max={99} addonAfter="%" className="w-full" />
            </Form.Item>
          </div>
        </Form.Item>
      </Form>
    </Modal>
  );
}
