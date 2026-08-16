/** @description 从不可变 Agent Release 创建服务身份和首个 Route 的表单。 */
import { Form, Input, Modal, Radio, Select } from "antd";
import { useEffect } from "react";

import type { CreateServiceRequest } from "@/api/services/services";

import type { ServiceReleaseOption } from "../useServiceManagement";

interface CreateServiceFormValues {
  name: string;
  releaseId: string;
  serviceType: CreateServiceRequest["service_type"];
  visibility: CreateServiceRequest["visibility"];
  allowedDepartmentIds?: string[];
  allowedAccountIds?: string[];
}

/** 创建服务弹窗数据、状态和提交回调。 */
export interface ServiceCreateDialogProps {
  /** 是否打开创建弹窗。 */
  open: boolean;
  /** 可作为服务首个版本的不可变 Release。 */
  releaseOptions: readonly ServiceReleaseOption[];
  /** Release 选项仍在加载。 */
  isReleaseOptionsLoading: boolean;
  /** Release 选项查询失败，必须禁用创建提交。 */
  isReleaseOptionsError: boolean;
  /** 创建服务请求正在提交。 */
  isCreating: boolean;
  /** 关闭弹窗且不提交。 */
  onClose: () => void;
  /** 提交服务、受众策略和首个 Release。 */
  onCreate: (body: CreateServiceRequest) => Promise<unknown>;
}

/** 从已发布版本创建稳定服务身份，受限范围仍由后端复核成员与部门。 */
export function ServiceCreateDialog(props: ServiceCreateDialogProps) {
  const [form] = Form.useForm<CreateServiceFormValues>();
  const visibility = Form.useWatch("visibility", form);

  useEffect(() => {
    if (props.open) {
      form.setFieldsValue({
        serviceType: "custom_knowledge_agent",
        visibility: "workspace",
        allowedDepartmentIds: [],
        allowedAccountIds: [],
      });
    }
  }, [form, props.open]);

  const handleSubmit = async () => {
    const values = await form.validateFields();
    await props.onCreate({
      name: values.name.trim(),
      release_id: values.releaseId,
      service_type: values.serviceType,
      visibility: values.visibility,
      allowed_department_ids:
        values.visibility === "restricted" ? (values.allowedDepartmentIds ?? []) : [],
      allowed_account_ids:
        values.visibility === "restricted" ? (values.allowedAccountIds ?? []) : [],
    });
    form.resetFields();
    props.onClose();
  };

  return (
    <Modal
      open={props.open}
      title="创建服务"
      okText="创建并发布"
      cancelText="取消"
      width={680}
      confirmLoading={props.isCreating}
      okButtonProps={{
        disabled:
          props.isReleaseOptionsError ||
          props.isReleaseOptionsLoading ||
          props.releaseOptions.length === 0,
      }}
      onCancel={props.onClose}
      onOk={() => void handleSubmit()}
    >
      <Form form={form} layout="vertical" requiredMark="optional">
        <Form.Item name="name" label="服务名称" rules={[{ required: true, min: 1, max: 120 }]}>
          <Input placeholder="例如：制度问答服务" />
        </Form.Item>
        <Form.Item
          name="releaseId"
          label="首个 Agent Release"
          rules={[{ required: true }]}
          extra={
            props.isReleaseOptionsError
              ? "Release 选项加载失败，旧选项已清空。"
              : "只列出当前空间经授权可见的不可变 Release。"
          }
        >
          <Select
            showSearch
            loading={props.isReleaseOptionsLoading}
            disabled={props.isReleaseOptionsError}
            optionFilterProp="label"
            options={props.releaseOptions.map((item) => ({
              value: item.release.release_id,
              label: `${item.agentName} · v${item.release.version} · ${item.release.release_id.slice(0, 8)}`,
            }))}
          />
        </Form.Item>
        <Form.Item name="serviceType" label="服务类型" rules={[{ required: true }]}>
          <Select
            options={[
              { value: "custom_knowledge_agent", label: "知识 Agent" },
              { value: "scenario_application", label: "场景应用" },
              { value: "open_api", label: "Open API" },
            ]}
          />
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
