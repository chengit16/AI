/** @description 成员组织和自定义直接角色原子编辑抽屉。 */
import { Alert, Drawer, Form, Select } from "antd";
import { useEffect } from "react";

import type { TeamManagement, TeamMember } from "@/api/services/teamManagement";

import type { TeamMemberFormValues } from "../types";

interface MemberEditorProps {
  /** 当前编辑成员；为空时关闭抽屉。 */
  member: TeamMember | null;
  /** 团队聚合中的可选部门、岗位和角色。 */
  snapshot: TeamManagement;
  /** 保存请求是否正在提交。 */
  pending: boolean;
  /** 关闭抽屉。 */
  onClose: () => void;
  /** 一次提交完整组织与直接角色配置。 */
  onSubmit: (values: TeamMemberFormValues) => void;
}

/** 以一个表单提交跨组织与角色的完整配置，避免连续请求产生中间授权态。 */
export function MemberEditor({ member, snapshot, pending, onClose, onSubmit }: MemberEditorProps) {
  const [form] = Form.useForm<TeamMemberFormValues>();
  const selectedDepartments = Form.useWatch("departmentIds", form) ?? [];
  useEffect(() => {
    if (!member) return;
    form.setFieldsValue({
      departmentIds: [...member.department_ids],
      primaryDepartmentId: member.primary_department_id ?? undefined,
      positionIds: [...member.position_ids],
      directRoleIds: [...member.direct_role_ids],
    });
  }, [form, member]);

  const departmentOptions = snapshot.departments
    .filter((item) => item.effective_active)
    .map((item) => ({
      value: item.department_id,
      label: `${"— ".repeat(item.depth)}${item.name}`,
    }));
  const positionOptions = snapshot.positions
    .filter((item) => item.effective_active && selectedDepartments.includes(item.department_id))
    .map((item) => ({ value: item.position_id, label: item.name }));
  const roleOptions = snapshot.roles.map((item) => ({ value: item.role_id, label: item.name }));

  return (
    <Drawer
      title={member ? `配置成员 · ${member.display_name ?? "受限成员"}` : "配置成员"}
      open={Boolean(member)}
      size={520}
      destroyOnHidden
      onClose={onClose}
      extra={
        <button
          className="ant-btn ant-btn-primary"
          disabled={pending}
          type="button"
          onClick={() => void form.validateFields().then(onSubmit)}
        >
          {pending ? "保存中" : "保存配置"}
        </button>
      }
    >
      <Alert
        className="mb-5"
        type="info"
        showIcon
        message="本次保存为原子更新"
        description="部门、主部门、岗位和自定义直接角色会一起生效；系统角色和继承角色不会被修改。"
      />
      <Form form={form} layout="vertical" requiredMark={false}>
        <Form.Item label="所属部门" name="departmentIds">
          <Select
            mode="multiple"
            options={departmentOptions}
            placeholder="选择一个或多个部门"
            onChange={(values: string[]) => {
              const primary = form.getFieldValue("primaryDepartmentId") as string | undefined;
              if (primary && !values.includes(primary)) {
                form.setFieldValue("primaryDepartmentId", undefined);
              }
              const allowedPositions = new Set(
                snapshot.positions
                  .filter((position) => values.includes(position.department_id))
                  .map((position) => position.position_id),
              );
              const selected = (form.getFieldValue("positionIds") as string[] | undefined) ?? [];
              form.setFieldValue(
                "positionIds",
                selected.filter((id) => allowedPositions.has(id)),
              );
            }}
          />
        </Form.Item>
        <Form.Item
          label="主部门"
          name="primaryDepartmentId"
          rules={[
            {
              validator: (_, value: string | undefined) =>
                selectedDepartments.length === 0 || value
                  ? Promise.resolve()
                  : Promise.reject(new Error("选择部门后必须指定主部门")),
            },
          ]}
        >
          <Select
            allowClear
            disabled={selectedDepartments.length === 0}
            options={departmentOptions.filter((item) => selectedDepartments.includes(item.value))}
            placeholder="指定主部门"
          />
        </Form.Item>
        <Form.Item label="岗位" name="positionIds">
          <Select mode="multiple" options={positionOptions} placeholder="选择所属部门内的岗位" />
        </Form.Item>
        <Form.Item label="自定义直接角色" name="directRoleIds">
          <Select
            mode="multiple"
            options={roleOptions}
            placeholder={roleOptions.length ? "选择直接角色" : "当前没有可分配的自定义角色"}
          />
        </Form.Item>
      </Form>
    </Drawer>
  );
}
