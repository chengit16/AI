/** @description 企业组织治理页，组合部门树、岗位、成员归属表单及状态操作。 */
import { Button, Dropdown, Skeleton, Table, Tag } from "antd";
import type { MenuProps, TableColumnsType } from "antd";
import { BriefcaseBusiness, Network, Plus, UserCog } from "lucide-react";
import { useState } from "react";

import { errorMessage, PlatformApiError } from "@/api/client";
import type { Department, Position } from "@/api/services/organization";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";

import { OrganizationForms } from "./OrganizationForms";
import { useOrganizationManagement } from "./useOrganizationManagement";

type FormMode = "department" | "position" | "assignment" | null;

/**
 * 展示企业组织事实并编排治理操作。
 *
 * 个人空间不发起组织查询；企业普通成员的拒绝状态来自后端 PDP，前端不自行推断授权结果。
 */
export default function WorkspaceOrganizationPage() {
  const { workspaceId, workspaces, currentWorkspace } = useCurrentWorkspace();
  const [formMode, setFormMode] = useState<FormMode>(null);
  // 1. 组织接口只属于企业空间，先确认空间类型可避免个人空间产生无意义的拒绝请求。
  const canQuery = Boolean(workspaceId && currentWorkspace?.workspace_type === "enterprise");
  const {
    departments,
    positions,
    members,
    createDepartmentMutation,
    createPositionMutation,
    assignmentMutation,
    departmentStatus,
    positionStatus,
  } = useOrganizationManagement({
    workspaceId,
    enabled: canQuery,
    closeForm: () => setFormMode(null),
  });

  // 2. 先收敛空间和权限状态，再构造治理表格，避免失败数据进入可操作界面。
  if (workspaces.isLoading || (!currentWorkspace && !workspaces.isError))
    return <Skeleton active paragraph={{ rows: 9 }} />;
  if (workspaces.isError || !currentWorkspace)
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="空间信息未能加载"
        description={errorMessage(workspaces.error)}
      />
    );
  if (currentWorkspace.workspace_type === "personal")
    return (
      <StateView
        kind="empty"
        headingLevel={1}
        title="个人空间不需要组织架构"
        description="组织部门、岗位和成员归属只在企业空间中启用。"
      />
    );
  const queryError = departments.error ?? positions.error ?? members.error;
  if (queryError instanceof PlatformApiError && queryError.code === "POLICY_DENIED")
    return (
      <StateView
        kind="denied"
        headingLevel={1}
        title="当前账号没有组织治理权限"
        description="组织树、岗位和成员归属属于企业治理能力，仅空间所有者可查看和修改。"
      />
    );
  if (queryError)
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="组织数据未能加载"
        description={errorMessage(queryError)}
        action={
          <Button
            onClick={() => {
              void departments.refetch();
              void positions.refetch();
              void members.refetch();
            }}
          >
            重新加载
          </Button>
        }
      />
    );

  const departmentColumns: TableColumnsType<Department> = [
    {
      title: "部门",
      dataIndex: "name",
      key: "name",
      render: (name, record) => {
        // 层级深度来自服务端树事实，使用运行时内联缩进，避免拼接无法静态提取的 Utility。
        return (
          <span
            className="inline-flex items-center gap-2 font-650"
            style={{ paddingInlineStart: record.depth * 20 }}
          >
            <Network size={15} />
            {name}
          </span>
        );
      },
    },
    {
      title: "层级",
      dataIndex: "depth",
      key: "depth",
      width: 90,
      render: (depth) => `${depth + 1} 级`,
    },
    {
      title: "状态",
      key: "status",
      width: 120,
      render: (_, record) => (
        <Tag color={record.effective_active ? "success" : "default"}>
          {record.effective_active ? "有效" : "已停用"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 110,
      render: (_, record) => (
        <Button
          type="link"
          onClick={() =>
            departmentStatus.mutate({
              id: record.department_id,
              active: record.status !== "active",
            })
          }
        >
          {record.status === "active" ? "停用" : "启用"}
        </Button>
      ),
    },
  ];
  const positionColumns: TableColumnsType<Position> = [
    { title: "岗位", dataIndex: "name", key: "name" },
    {
      title: "所属部门",
      dataIndex: "department_id",
      key: "department_id",
      render: (id) => departments.data?.find((item) => item.department_id === id)?.name ?? id,
    },
    {
      title: "状态",
      key: "status",
      width: 120,
      render: (_, record) => (
        <Tag color={record.effective_active ? "success" : "default"}>
          {record.effective_active ? "有效" : "已停用"}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 110,
      render: (_, record) => (
        <Button
          type="link"
          onClick={() =>
            positionStatus.mutate({ id: record.position_id, active: record.status !== "active" })
          }
        >
          {record.status === "active" ? "停用" : "启用"}
        </Button>
      ),
    },
  ];
  const createItems: MenuProps["items"] = [
    { key: "department", icon: <Network size={16} />, label: "新建部门" },
    { key: "position", icon: <BriefcaseBusiness size={16} />, label: "新建岗位" },
    { key: "assignment", icon: <UserCog size={16} />, label: "设置成员归属" },
  ];

  return (
    <>
      <PageHeader
        eyebrow="ORGANIZATION"
        title="组织架构"
        description="维护多级部门、岗位和成员归属。停用上级部门会使下级组织权限一并失效。"
        actions={
          <Dropdown
            menu={{
              items: createItems,
              onClick: ({ key }) => setFormMode(key as Exclude<FormMode, null>),
            }}
          >
            <Button type="primary" icon={<Plus size={17} />}>
              新建
            </Button>
          </Dropdown>
        }
      />
      <section
        className="ui-surface-panel mb-6 overflow-hidden"
        aria-labelledby="department-section-title"
      >
        <div className="flex items-center justify-between border-b border-b-solid border-border px-6 py-5">
          <div>
            <h2 className="m-0 text-[17px]" id="department-section-title">
              部门层级
            </h2>
            <p className="mb-0 mt-1 text-xs text-text-muted">
              {departments.data?.length ?? 0} 个部门
            </p>
          </div>
        </div>
        <Table<Department>
          rowKey="department_id"
          columns={departmentColumns}
          dataSource={departments.data ?? []}
          pagination={false}
          scroll={{ x: 640 }}
          locale={{ emptyText: "还没有部门，请先创建一级部门" }}
        />
      </section>
      <section
        className="ui-surface-panel mb-6 overflow-hidden"
        aria-labelledby="position-section-title"
      >
        <div className="flex items-center justify-between border-b border-b-solid border-border px-6 py-5">
          <div>
            <h2 className="m-0 text-[17px]" id="position-section-title">
              岗位清单
            </h2>
            <p className="mb-0 mt-1 text-xs text-text-muted">
              {positions.data?.length ?? 0} 个岗位
            </p>
          </div>
        </div>
        <Table<Position>
          rowKey="position_id"
          columns={positionColumns}
          dataSource={positions.data ?? []}
          pagination={false}
          scroll={{ x: 640 }}
          locale={{ emptyText: "还没有岗位" }}
        />
      </section>
      <OrganizationForms
        mode={formMode}
        departments={departments.data ?? []}
        positions={positions.data ?? []}
        members={members.data ?? []}
        pending={
          createDepartmentMutation.isPending ||
          createPositionMutation.isPending ||
          assignmentMutation.isPending
        }
        onClose={() => setFormMode(null)}
        onDepartment={(values) => createDepartmentMutation.mutate(values)}
        onPosition={(values) => createPositionMutation.mutate(values)}
        onAssignment={(values) => assignmentMutation.mutate(values)}
      />
    </>
  );
}
