/** @description 服务定义、访问策略和当前版本化 Route 的治理面板。 */
import { Button, Descriptions, Popconfirm, Progress, Tag, Typography } from "antd";
import { Archive, CirclePause, CirclePlay, Pencil, Rocket, RotateCcw, Split } from "lucide-react";

import type { ServiceDeployment, UpdateServiceRequest } from "@/api/services/services";

import { formatServiceTime, routeMode, serviceStatus, serviceTypeLabel } from "../config";
import type { PromoteRouteInput } from "../useServiceManagement";

/** 当前服务权限、命令状态和治理回调。 */
export interface ServiceRoutePanelProps {
  /** 当前服务、访问策略、Route 和发布指针聚合。 */
  deployment: ServiceDeployment;
  /** 只控制编辑策略入口，后端仍执行独立授权。 */
  canUpdate: boolean;
  /** 只控制灰度入口。 */
  canCanary: boolean;
  /** 只控制晋级入口。 */
  canPromote: boolean;
  /** 只控制回滚入口。 */
  canRollback: boolean;
  /** 任一治理命令正在提交。 */
  isMutating: boolean;
  /** 打开服务定义和访问策略编辑弹窗。 */
  onEdit: () => void;
  /** 打开灰度 Release 与比例选择弹窗。 */
  onCanary: () => void;
  /** 提交服务状态变化或归档。 */
  onUpdate: (body: UpdateServiceRequest) => void;
  /** 把当前灰度 Release 晋级为唯一正式版本。 */
  onPromote: (input: PromoteRouteInput) => void;
  /** 使用当前 generation 回滚最近稳定版本。 */
  onRollback: (expectedGeneration: number) => void;
}

function idList(values: readonly string[]) {
  return values.length ? values.join("、") : "未指定";
}

/**
 * 展示服务当前可变定义和不可变路由事实，并将高风险命令保持在当前上下文内。
 *
 * 暂停只拒绝新 Run，已有 Run 继续按冻结路由收敛；归档和回滚需要二次确认。
 */
export function ServiceRoutePanel(props: ServiceRoutePanelProps) {
  const { service, access_policy: policy, route, publication } = props.deployment;
  const serviceDisplay = serviceStatus[service.status] ?? {
    label: service.status,
    color: "default",
  };
  const routeDisplay = routeMode[route.route_mode] ?? {
    label: route.route_mode,
    color: "default",
  };
  const updateStatus = (target: "active" | "suspended" | "archived") =>
    props.onUpdate({
      expected_version: service.version,
      name: null,
      target_status: target,
      visibility: policy.visibility,
      allowed_department_ids: policy.allowed_department_ids,
      allowed_account_ids: policy.allowed_account_ids,
    });

  return (
    <section aria-labelledby="service-route-title">
      <div className="mb-5 flex items-start justify-between gap-4 nav-mobile:flex-col">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="service-route-title" className="m-0 text-xl text-text-strong">
              {service.name}
            </h2>
            <Tag color={serviceDisplay.color}>{serviceDisplay.label}</Tag>
            <Tag>{serviceTypeLabel[service.service_type] ?? service.service_type}</Tag>
          </div>
          <p className="mb-0 mt-2 text-sm text-text-muted">
            {service.service_key} · 定义 v{service.version} · 发布 generation{" "}
            {publication.generation}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {props.canUpdate && service.status !== "archived" && (
            <Button icon={<Pencil size={16} />} onClick={props.onEdit}>
              编辑策略
            </Button>
          )}
          {props.canUpdate && service.status === "active" && (
            <Button
              icon={<CirclePause size={16} />}
              loading={props.isMutating}
              onClick={() => updateStatus("suspended")}
            >
              暂停
            </Button>
          )}
          {props.canUpdate && service.status === "suspended" && (
            <Button
              icon={<CirclePlay size={16} />}
              loading={props.isMutating}
              onClick={() => updateStatus("active")}
            >
              恢复
            </Button>
          )}
          {props.canUpdate && service.status !== "archived" && (
            <Popconfirm
              title="归档这个服务？"
              description="历史 Route 和 Run 绑定不会删除，归档后不能恢复。"
              okText="确认归档"
              cancelText="取消"
              onConfirm={() => updateStatus("archived")}
            >
              <Button danger icon={<Archive size={16} />} loading={props.isMutating}>
                归档
              </Button>
            </Popconfirm>
          )}
        </div>
      </div>

      <Descriptions
        bordered
        size="small"
        column={{ xs: 1, sm: 1, md: 2 }}
        items={[
          {
            key: "service-id",
            label: "Service ID",
            children: <Typography.Text copyable>{service.service_id}</Typography.Text>,
          },
          {
            key: "agent-id",
            label: "Agent ID",
            children: <Typography.Text copyable>{service.agent_id}</Typography.Text>,
          },
          {
            key: "visibility",
            label: "访问范围",
            children: policy.visibility === "workspace" ? "空间成员" : "指定成员或部门",
          },
          {
            key: "policy-version",
            label: "策略版本",
            children: `v${policy.version}`,
          },
          {
            key: "departments",
            label: "允许部门",
            children: idList(policy.allowed_department_ids),
          },
          {
            key: "accounts",
            label: "允许账号",
            children: idList(policy.allowed_account_ids),
          },
        ]}
      />

      <div className="mt-6 border-0 border-t border-t-solid border-border-soft pt-5">
        <div className="mb-4 flex items-center justify-between gap-4 nav-mobile:items-start">
          <div>
            <h3 className="m-0 text-lg text-text-strong">当前 Route</h3>
            <p className="mb-0 mt-1 text-sm text-text-muted">
              Route v{route.route_version} · {formatServiceTime(publication.published_at)}
            </p>
          </div>
          <Tag color={routeDisplay.color}>{routeDisplay.label}</Tag>
        </div>
        <div className="grid grid-cols-2 gap-4 form-down:grid-cols-1">
          <div className="rounded-ui border border-solid border-border-soft bg-surface-subtle p-4">
            <p className="m-0 text-xs font-650 text-text-muted">正式 Release</p>
            <Typography.Text className="mt-2 block" copyable>
              {route.primary_release_id}
            </Typography.Text>
          </div>
          <div className="rounded-ui border border-solid border-border-soft bg-surface-subtle p-4">
            <p className="m-0 text-xs font-650 text-text-muted">灰度 Release</p>
            {route.canary_release_id ? (
              <>
                <Typography.Text className="mt-2 block" copyable>
                  {route.canary_release_id}
                </Typography.Text>
                <Progress className="mb-0 mt-3" percent={route.canary_percent} size="small" />
              </>
            ) : (
              <p className="mb-0 mt-2 text-sm text-text-muted">当前没有灰度版本</p>
            )}
          </div>
        </div>
        {service.status === "active" && (
          <div className="mt-5 flex flex-wrap gap-2">
            {props.canCanary && route.route_mode !== "canary" && (
              <Button icon={<Split size={16} />} onClick={props.onCanary}>
                启动灰度
              </Button>
            )}
            {props.canPromote && route.canary_release_id && (
              <Button
                type="primary"
                icon={<Rocket size={16} />}
                loading={props.isMutating}
                onClick={() =>
                  props.onPromote({
                    releaseId: route.canary_release_id!,
                    expectedGeneration: publication.generation,
                  })
                }
              >
                晋级正式版本
              </Button>
            )}
            {props.canRollback && route.previous_route_id && (
              <Popconfirm
                title="回滚到最近稳定版本？"
                description="该操作只追加新 Route，不修改历史 Release。"
                okText="确认回滚"
                cancelText="取消"
                onConfirm={() => props.onRollback(publication.generation)}
              >
                <Button danger icon={<RotateCcw size={16} />} loading={props.isMutating}>
                  一键回滚
                </Button>
              </Popconfirm>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
