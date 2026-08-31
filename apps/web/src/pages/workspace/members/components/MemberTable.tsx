/** @description 团队成员组织、角色、活动和生命周期操作表。 */
import { Button, Popconfirm, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import { PencilLine, RotateCcw, UserRoundMinus, UserRoundX } from "lucide-react";

import type { TeamManagement, TeamMember } from "@/api/services/teamManagement";

import { formatTeamTime, memberStatusLabel } from "../teamUtils";

interface MemberTableProps {
  /** 当前空间的团队聚合快照，用于解释组织和角色标识。 */
  snapshot: TeamManagement;
  /** 已按页面条件筛选的成员列表。 */
  members: readonly TeamMember[];
  /** 任一成员写操作是否正在提交，用于避免重复治理操作。 */
  pending: boolean;
  /** 打开成员组织与直接角色编辑入口。 */
  onEdit: (member: TeamMember) => void;
  /** 停用活动成员并立即撤销其访问。 */
  onDisable: (accountId: string) => void;
  /** 恢复已停用成员的访问资格。 */
  onActivate: (accountId: string) => void;
  /** 将成员移出企业并清理可撤销的组织关系。 */
  onRemove: (accountId: string) => void;
}

/** 显示成员有效权限解释，并把停用、恢复和移除动作明确分开。 */
export function MemberTable({
  snapshot,
  members,
  pending,
  onEdit,
  onDisable,
  onActivate,
  onRemove,
}: MemberTableProps) {
  const departmentName = new Map(
    snapshot.departments.map((item) => [item.department_id, item.name]),
  );
  const positionName = new Map(snapshot.positions.map((item) => [item.position_id, item.name]));
  const columns: TableColumnsType<TeamMember> = [
    {
      title: "成员",
      key: "member",
      fixed: "left",
      width: 210,
      render: (_, record) => (
        <div>
          <div className="flex items-center gap-2">
            <strong className="truncate text-text-strong">
              {record.display_name ?? "受字段权限保护"}
            </strong>
            {record.membership_type === "owner" && <Tag color="gold">Owner</Tag>}
          </div>
          <span className="mt-1 block truncate text-xs text-text-muted">
            {record.login_name ?? "登录名不可见"}
          </span>
        </div>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (value: TeamMember["status"]) => (
        <Tag color={value === "active" ? "success" : value === "disabled" ? "warning" : "default"}>
          {memberStatusLabel(value)}
        </Tag>
      ),
    },
    {
      title: "组织归属",
      key: "organization",
      width: 220,
      render: (_, record) => {
        const departmentLabels = record.department_ids.map((id) => departmentName.get(id) ?? id);
        const positionLabels = record.position_ids.map((id) => positionName.get(id) ?? id);
        return (
          <div className="flex flex-wrap gap-1">
            {departmentLabels.length === 0 && (
              <span className="text-xs text-text-muted">未配置部门</span>
            )}
            {departmentLabels.map((name) => (
              <Tag key={name}>{name}</Tag>
            ))}
            {positionLabels.map((name) => (
              <Tag color="blue" key={name}>
                {name}
              </Tag>
            ))}
          </div>
        );
      },
    },
    {
      title: "有效角色",
      key: "roles",
      width: 230,
      render: (_, record) => (
        <div className="flex flex-wrap gap-1">
          {record.effective_roles.length === 0 && (
            <span className="text-xs text-text-muted">暂无有效角色</span>
          )}
          {record.effective_roles.map((role) => (
            <Tag
              color={role.source_types.includes("member") ? "green" : "default"}
              key={role.role_id}
            >
              {role.name}
            </Tag>
          ))}
        </div>
      ),
    },
    {
      title: "最后活跃",
      dataIndex: "last_active_at",
      width: 170,
      render: (value: string | null) => (
        <span className="text-xs text-text-muted">{formatTeamTime(value)}</span>
      ),
    },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      width: 250,
      render: (_, record) => {
        if (record.membership_type === "owner") {
          return <span className="text-xs text-text-muted">系统所有者不可变更</span>;
        }
        if (record.status === "left") {
          return <span className="text-xs text-text-muted">需通过新邀请重新加入</span>;
        }
        return (
          <div className="flex items-center gap-1">
            {record.status === "active" && (
              <>
                <Button
                  type="link"
                  size="small"
                  icon={<PencilLine size={15} />}
                  onClick={() => onEdit(record)}
                >
                  编辑
                </Button>
                <Popconfirm
                  title="停用该成员？"
                  description="访问会立即失效，组织和角色配置将保留。"
                  okText="停用"
                  cancelText="取消"
                  onConfirm={() => onDisable(record.account_id)}
                >
                  <Button
                    type="link"
                    size="small"
                    icon={<UserRoundX size={15} />}
                    disabled={pending}
                  >
                    停用
                  </Button>
                </Popconfirm>
              </>
            )}
            {record.status === "disabled" && (
              <Button
                type="link"
                size="small"
                icon={<RotateCcw size={15} />}
                disabled={pending}
                onClick={() => onActivate(record.account_id)}
              >
                恢复
              </Button>
            )}
            <Popconfirm
              title="移除该成员？"
              description="组织和自定义直接角色会被清理，重新加入需要新的邀请。"
              okText="确认移除"
              cancelText="取消"
              onConfirm={() => onRemove(record.account_id)}
            >
              <Button
                type="link"
                size="small"
                danger
                icon={<UserRoundMinus size={15} />}
                disabled={pending}
              >
                移除
              </Button>
            </Popconfirm>
          </div>
        );
      },
    },
  ];
  return (
    <Table<TeamMember>
      rowKey="account_id"
      columns={columns}
      dataSource={[...members]}
      pagination={{ pageSize: 10, hideOnSinglePage: true }}
      scroll={{ x: 1180 }}
      locale={{ emptyText: "没有符合当前条件的成员" }}
    />
  );
}
