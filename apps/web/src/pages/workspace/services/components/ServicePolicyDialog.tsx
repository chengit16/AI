/** @description 更新服务名称与受众访问策略的乐观锁表单。 */
import { Form, Input, Modal, Radio, Select } from "antd";
import { useEffect } from "react";

import type { ServiceDeployment, UpdateServiceRequest } from "@/api/services/services";

interface ServicePolicyFormValues {
  name: string;
  visibility: "workspace" | "restricted";
  allowedDepartmentIds: string[];
  allowedAccountIds: string[];
}

/** 策略弹窗的当前服务、状态和提交回调。 */
export interface ServicePolicyDialogProps {
  /** 是否打开编辑弹窗。 */
  open: boolean;
  /** 当前服务、策略和 Route 聚合。 */
  deployment: ServiceDeployment;
  /** 更新请求正在提交。 */
  isUpdating: boolean;
  /** 关闭弹窗且不提交。 */
  onClose: () => void;
  /** 提交服务当前 version 和完整受众范围。 */
  onUpdate: (body: UpdateServiceRequest) => Promise<unknown>;
}

/** 更新服务可变定义并生成新的不可变访问策略版本。 */
export function ServicePolicyDialog(props: ServicePolicyDialogProps) {
  const [form] = Form.useForm<ServicePolicyFormValues>();
  const visibility = Form.useWatch("visibility", form);

  useEffect(() => {
    if (props.open) {
      form.setFieldsValue({
        name: props.deployment.service.name,
        visibility: props.deployment.access_policy.visibility,
        allowedDepartmentIds: [...props.deployment.access_policy.allowed_department_ids],
        allowedAccountIds: [...props.deployment.access_policy.allowed_account_ids],
      });
    }
  }, [form, props.deployment, props.open]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    await props.onUpdate({
      expected_version: props.deployment.service.version,
      name: values.name.trim(),
      target_status: null,
      visibility: values.visibility,
      allowed_department_ids: values.visibility === "restricted" ? values.allowedDepartmentIds : [],
      allowed_account_ids: values.visibility === "restricted" ? values.allowedAccountIds : [],
    });
    props.onClose();
  };

  return (
    <Modal
      open={props.open}
      title="服务定义与访问策略"
      okText="保存新版本"
      cancelText="取消"
      width={640}
      confirmLoading={props.isUpdating}
      onCancel={props.onClose}
      onOk={() => void handleSubmit()}
    >
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="name" label="服务名称" rules={[{ required: true, min: 1, max: 120 }]}>
          <Input />
        </Form.Item>
        <Form.Item name="visibility" label="访问范围" rules={[{ required: true }]}>
          <Radio.Group
            options={[
              { value: "workspace", label: "空间成员" },
              { value: "restricted", label: "指定成员或部门" },
            ]}
          />
        </Form.Item>
        {visibility === "restricted" && (
          <>
            <Form.Item name="allowedDepartmentIds" label="允许的部门 ID">
              <Select mode="tags" tokenSeparators={[",", " "]} placeholder="输入部门 UUID" />
            </Form.Item>
            <Form.Item name="allowedAccountIds" label="允许的账号 ID">
              <Select mode="tags" tokenSeparators={[",", " "]} placeholder="输入账号 UUID" />
            </Form.Item>
          </>
        )}
      </Form>
    </Modal>
  );
}
