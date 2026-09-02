/** @description 角色选择、影响成员和可编辑权限矩阵。 */
import { Alert, Button, Checkbox, Empty, Select, Tag } from "antd";
import { useMemo, useState } from "react";

import type { RoleGovernance, RoleGrant } from "@/api/services/permissionsAudit";

interface Props {
  /** 服务端一次聚合的角色、授权和权限目录快照。 */
  snapshot: RoleGovernance;
  /** 当前菜单发布是否允许提交角色权限。 */
  canManage: boolean;
  /** 权限替换事务是否正在提交。 */
  saving: boolean;
  /** 携带可信角色版本整体保存授权集合。 */
  onSave: (roleId: string, version: number, items: readonly RoleGrant[]) => void;
}

const securityOptions = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"].map((value) => ({
  value,
  label: value,
}));

/** 默认使用工作空间范围；服务端仍会校验每项权限允许的数据范围。 */
function defaultGrant(permissionCode: string): RoleGrant {
  return {
    permission_code: permissionCode,
    scope_type: "workspace",
    department_ids: [],
    resource_ids: [],
    maximum_security_level: "RESTRICTED",
    field_mask: [],
  };
}

/** 以角色为编辑边界，系统角色和停用角色始终只读。 */
export function RoleGovernancePanel({ snapshot, canManage, saving, onSave }: Props) {
  const [roleId, setRoleId] = useState(snapshot.roles[0]?.role_id ?? "");
  const role = snapshot.roles.find((item) => item.role_id === roleId) ?? snapshot.roles[0];
  const [grants, setGrants] = useState<readonly RoleGrant[]>(role?.grants ?? []);
  const grantByCode = useMemo(
    () => new Map(grants.map((grant) => [grant.permission_code, grant])),
    [grants],
  );
  if (!role) return <Empty description="当前空间暂无角色" />;
  const editable = canManage && role.editable && role.status === "active";
  const replaceGrant = (permissionCode: string, next: RoleGrant | null) => {
    setGrants((current) => [
      ...current.filter((item) => item.permission_code !== permissionCode),
      ...(next ? [next] : []),
    ]);
  };

  return (
    <div className="grid grid-cols-[260px_minmax(0,1fr)] gap-5 nav-mobile:grid-cols-1">
      <aside className="ui-surface-panel p-4">
        <Select
          className="w-full"
          value={role.role_id}
          onChange={(nextRoleId) => {
            setRoleId(nextRoleId);
            setGrants(snapshot.roles.find((item) => item.role_id === nextRoleId)?.grants ?? []);
          }}
          options={snapshot.roles.map((item) => ({
            value: item.role_id,
            label: item.name + (item.system_managed ? " · 系统" : ""),
          }))}
        />
        <div className="mt-4 flex flex-wrap gap-2">
          <Tag color={role.status === "active" ? "success" : "default"}>
            {role.status === "active" ? "启用" : "停用"}
          </Tag>
          <Tag>{role.system_managed ? "系统角色" : "自定义角色"}</Tag>
          <Tag color={role.editable ? "blue" : "default"}>{role.editable ? "可编辑" : "只读"}</Tag>
        </div>
        <h3 className="mb-2 mt-5 text-sm">影响成员 · {role.affected_member_count}</h3>
        <div className="max-h-[280px] overflow-y-auto">
          {role.affected_members.length ? (
            role.affected_members.map((member) => (
              <div key={member.account_id} className="border-b border-b-solid border-border py-3">
                <p className="m-0 text-sm font-650">{member.display_name}</p>
                <p className="mb-0 mt-1 text-xs text-text-muted">
                  {member.sources.map((source) => source.scope_name).join("、")}
                </p>
              </div>
            ))
          ) : (
            <p className="text-sm text-text-muted">暂无有效成员</p>
          )}
        </div>
      </aside>
      <section className="ui-surface-panel min-w-0 overflow-hidden p-5">
        {!editable && (
          <Alert
            className="mb-4"
            type="info"
            showIcon
            message="当前角色只读"
            description="系统角色、停用角色或缺少管理权限时不能保存；服务端会再次校验。"
          />
        )}
        <div className="overflow-x-auto">
          <div className="min-w-[760px] space-y-5">
            {snapshot.permission_groups.map((group) => (
              <div key={group.domain}>
                <h3 className="mb-2 mt-0 text-sm">{group.domain}</h3>
                {group.items.map((permission) => {
                  const grant = grantByCode.get(permission.permission_code);
                  return (
                    <div
                      key={permission.permission_code}
                      className="grid grid-cols-[280px_160px_180px_minmax(180px,1fr)] items-center gap-3 border-t border-t-solid border-border py-3"
                    >
                      <Checkbox
                        checked={Boolean(grant)}
                        disabled={!editable}
                        onChange={(event) =>
                          replaceGrant(
                            permission.permission_code,
                            event.target.checked ? defaultGrant(permission.permission_code) : null,
                          )
                        }
                      >
                        <span className="text-xs">{permission.permission_code}</span>
                      </Checkbox>
                      <Select
                        value={grant?.scope_type ?? "workspace"}
                        disabled={!editable || !grant}
                        options={permission.allowed_scope_types.map((value) => ({
                          value,
                          label: value,
                        }))}
                        onChange={(scope_type) =>
                          grant &&
                          replaceGrant(permission.permission_code, {
                            ...grant,
                            scope_type,
                            department_ids: [],
                            resource_ids: [],
                          })
                        }
                      />
                      <Select
                        value={grant?.maximum_security_level ?? "RESTRICTED"}
                        disabled={!editable || !grant}
                        options={securityOptions}
                        onChange={(maximum_security_level) =>
                          grant &&
                          replaceGrant(permission.permission_code, {
                            ...grant,
                            maximum_security_level,
                          })
                        }
                      />
                      <Select
                        mode="multiple"
                        placeholder="字段遮罩"
                        value={grant?.field_mask ?? []}
                        disabled={!editable || !grant || permission.fields.length === 0}
                        options={permission.fields.map((field) => ({
                          value: field.field_name,
                          label: field.field_name + " · " + field.security_level,
                        }))}
                        onChange={(field_mask) =>
                          grant &&
                          replaceGrant(permission.permission_code, { ...grant, field_mask })
                        }
                      />
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
        <div className="mt-5 flex justify-end">
          <Button
            type="primary"
            disabled={!editable}
            loading={saving}
            onClick={() => onSave(role.role_id, snapshot.role_version, grants)}
          >
            保存角色权限
          </Button>
        </div>
      </section>
    </div>
  );
}
