/** @description 企业组织部门、岗位和成员归属表单集合及其提交类型。 */
import { Button, Drawer, Form, Input, Select } from "antd";

import type { Department, Position } from "@/api/services/organization";
import type { WorkspaceMember } from "@/api/services/workspaces";

/** 新建部门表单值；未提供上级时创建一级部门。 */
export interface DepartmentFormValues {
  name: string;
  parentId?: string;
}

/** 新建岗位表单值；岗位必须归属一个有效部门。 */
export interface PositionFormValues {
  name: string;
  departmentId: string;
}

/** 成员组织归属表单值；主部门必须同时存在于所属部门集合中。 */
export interface AssignmentFormValues {
  accountId: string;
  departmentIds: string[];
  primaryDepartmentId: string;
  positionIds: string[];
}

interface OrganizationFormsProps {
  /** 当前打开的组织表单类型；为空时关闭抽屉。 */
  mode: "department" | "position" | "assignment" | null;
  /** 服务端返回的部门层级事实。 */
  departments: readonly Department[];
  /** 服务端返回的岗位事实。 */
  positions: readonly Position[];
  /** 可选择组织归属的企业成员。 */
  members: readonly WorkspaceMember[];
  /** 任一组织命令是否正在提交。 */
  pending: boolean;
  /** 关闭当前表单抽屉。 */
  onClose: () => void;
  /** 提交部门创建表单。 */
  onDepartment: (values: DepartmentFormValues) => void;
  /** 提交岗位创建表单。 */
  onPosition: (values: PositionFormValues) => void;
  /** 原子替换成员的部门、主部门和岗位归属。 */
  onAssignment: (values: AssignmentFormValues) => void;
}

/**
 * 统一承载部门、岗位和成员归属三类组织表单。
 *
 * 下拉选项仅展示服务端投影中的有效事实，提交值的工作空间归属、层级约束和权限仍由后端校验。
 */
export function OrganizationForms(props: OrganizationFormsProps) {
  const { mode, departments, positions, members, pending, onClose } = props;
  const [departmentForm] = Form.useForm<DepartmentFormValues>();
  const [positionForm] = Form.useForm<PositionFormValues>();
  const [assignmentForm] = Form.useForm<AssignmentFormValues>();
  const titles = { department: "新建部门", position: "新建岗位", assignment: "设置成员归属" };
  const activeDepartmentOptions = departments
    .filter((item) => item.effective_active)
    .map((item) => ({ value: item.department_id, label: item.name }));
  const activePositionOptions = positions
    .filter((item) => item.effective_active)
    .map((item) => ({ value: item.position_id, label: item.name }));
  const activeMemberOptions = members
    .filter((item) => item.status === "active")
    .map((item) => ({ value: item.account_id, label: item.display_name }));

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
            <Select allowClear placeholder="作为一级部门" options={activeDepartmentOptions} />
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
            <Select options={activeDepartmentOptions} />
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
            <Select options={activeMemberOptions} />
          </Form.Item>
          <Form.Item
            label="所属部门"
            name="departmentIds"
            rules={[{ required: true, message: "至少选择一个部门" }]}
          >
            <Select mode="multiple" options={activeDepartmentOptions} />
          </Form.Item>
          <Form.Item
            label="主部门"
            name="primaryDepartmentId"
            rules={[{ required: true, message: "请选择主部门" }]}
          >
            <Select options={activeDepartmentOptions} />
          </Form.Item>
          <Form.Item label="岗位" name="positionIds">
            <Select mode="multiple" options={activePositionOptions} />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={pending}>
            保存归属
          </Button>
        </Form>
      )}
    </Drawer>
  );
}
