import { Button, Drawer, Form, Input, Select } from "antd";

import type { Department, Position } from "@/api/services/organization";
import type { WorkspaceMember } from "@/api/services/workspaces";

export interface DepartmentFormValues {
  name: string;
  parentId?: string;
}
export interface PositionFormValues {
  name: string;
  departmentId: string;
}
export interface AssignmentFormValues {
  accountId: string;
  departmentIds: string[];
  primaryDepartmentId: string;
  positionIds: string[];
}

interface OrganizationFormsProps {
  mode: "department" | "position" | "assignment" | null;
  departments: readonly Department[];
  positions: readonly Position[];
  members: readonly WorkspaceMember[];
  pending: boolean;
  onClose: () => void;
  onDepartment: (values: DepartmentFormValues) => void;
  onPosition: (values: PositionFormValues) => void;
  onAssignment: (values: AssignmentFormValues) => void;
}

export function OrganizationForms(props: OrganizationFormsProps) {
  const { mode, departments, positions, members, pending, onClose } = props;
  const [departmentForm] = Form.useForm<DepartmentFormValues>();
  const [positionForm] = Form.useForm<PositionFormValues>();
  const [assignmentForm] = Form.useForm<AssignmentFormValues>();
  const titles = { department: "新建部门", position: "新建岗位", assignment: "设置成员归属" };

  return (
    <Drawer
      title={mode ? titles[mode] : "组织设置"}
      size="default"
      open={mode !== null}
      onClose={onClose}
      destroyOnHidden
    >
      {mode === "department" && (
        <Form
          form={departmentForm}
          layout="vertical"
          requiredMark={false}
          onFinish={props.onDepartment}
        >
          <Form.Item
            label="部门名称"
            name="name"
            rules={[{ required: true, message: "请输入部门名称" }, { max: 120 }]}
          >
            <Input autoFocus />
          </Form.Item>
          <Form.Item label="上级部门" name="parentId">
            <Select
              allowClear
              placeholder="作为一级部门"
              options={departments
                .filter((item) => item.effective_active)
                .map((item) => ({ value: item.department_id, label: item.name }))}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={pending}>
            创建部门
          </Button>
        </Form>
      )}
      {mode === "position" && (
        <Form
          form={positionForm}
          layout="vertical"
          requiredMark={false}
          onFinish={props.onPosition}
        >
          <Form.Item
            label="所属部门"
            name="departmentId"
            rules={[{ required: true, message: "请选择所属部门" }]}
          >
            <Select
              options={departments
                .filter((item) => item.effective_active)
                .map((item) => ({ value: item.department_id, label: item.name }))}
            />
          </Form.Item>
          <Form.Item
            label="岗位名称"
            name="name"
            rules={[{ required: true, message: "请输入岗位名称" }, { max: 120 }]}
          >
            <Input />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={pending}>
            创建岗位
          </Button>
        </Form>
      )}
      {mode === "assignment" && (
        <Form
          form={assignmentForm}
          layout="vertical"
          requiredMark={false}
          onFinish={props.onAssignment}
        >
          <Form.Item
            label="企业成员"
            name="accountId"
            rules={[{ required: true, message: "请选择成员" }]}
          >
            <Select
              options={members
                .filter((item) => item.status === "active")
                .map((item) => ({ value: item.account_id, label: item.display_name }))}
            />
          </Form.Item>
          <Form.Item
            label="所属部门"
            name="departmentIds"
            rules={[{ required: true, message: "至少选择一个部门" }]}
          >
            <Select
              mode="multiple"
              options={departments
                .filter((item) => item.effective_active)
                .map((item) => ({ value: item.department_id, label: item.name }))}
            />
          </Form.Item>
          <Form.Item
            label="主部门"
            name="primaryDepartmentId"
            rules={[{ required: true, message: "请选择主部门" }]}
          >
            <Select
              options={departments
                .filter((item) => item.effective_active)
                .map((item) => ({ value: item.department_id, label: item.name }))}
            />
          </Form.Item>
          <Form.Item label="岗位" name="positionIds">
            <Select
              mode="multiple"
              options={positions
                .filter((item) => item.effective_active)
                .map((item) => ({ value: item.position_id, label: item.name }))}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={pending}>
            保存归属
          </Button>
        </Form>
      )}
    </Drawer>
  );
}
