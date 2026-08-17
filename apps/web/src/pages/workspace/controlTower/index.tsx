/** @description 质量、成本、隔离与合规运营控制台页面。 */
import { useQuery } from "@tanstack/react-query";
import { Alert, Button, Skeleton, Tag } from "antd";
import { RefreshCw } from "lucide-react";

import { errorMessage } from "@/api/client";
import { getOperationsControlTowerSnapshot } from "@/api/services/operations";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";

import { DangerousOperationsPanel } from "./components/DangerousOperationsPanel";
import { ControlTowerSectionPanel } from "./components/ControlTowerSectionPanel";

/** 只读聚合当前工作空间的五个治理分区，并明确外部输入仍未配置的边界。 */
export default function OperationsControlTowerPage() {
  const { workspaceId } = useCurrentWorkspace();
  const { visiblePermissionCodes } = useWorkspaceMenuNavigation();
  const canRead = visiblePermissionCodes.has("operations.control_tower.read");
  const snapshot = useQuery({
    queryKey: ["operations-control-tower", workspaceId],
    queryFn: ({ signal }) => getOperationsControlTowerSnapshot(workspaceId!, signal),
    enabled: Boolean(workspaceId && canRead),
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
  });

  if (!canRead) {
    return (
      <StateView
        kind="denied"
        headingLevel={1}
        title="当前角色没有治理控制台权限"
        description="控制台仅展示当前空间的治理状态；后端接口仍按独立 permission_code 授权。"
      />
    );
  }
  if (snapshot.isLoading) return <Skeleton active paragraph={{ rows: 14 }} />;
  if (snapshot.isError || !snapshot.data) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="治理状态暂时无法加载"
        description={errorMessage(snapshot.error)}
        action={<Button onClick={() => void snapshot.refetch()}>重新加载</Button>}
      />
    );
  }

  const data = snapshot.data;
  return (
    <>
      <PageHeader
        eyebrow="GOVERNANCE CONTROL TOWER"
        title="治理控制台"
        description="统一查看质量、成本、隔离、法规和私有实例状态；未配置或未执行的真实门禁会保持原始状态。"
        actions={
          <Button
            aria-label="刷新治理控制台"
            icon={<RefreshCw size={16} />}
            loading={snapshot.isFetching}
            onClick={() => void snapshot.refetch()}
          >
            刷新
          </Button>
        }
      />
      <Alert
        className="mb-6"
        type="warning"
        showIcon
        message="当前快照不提供真实供应商、价格、法域、客户环境或容量认证结论。"
        description="合成数据和本地机制只能证明采集、隔离和失败关闭流程，不能替代外部审核输入。"
      />
      <section className="grid grid-cols-2 gap-4 tablet-down:grid-cols-1" aria-label="治理分区">
        {data.sections.map((section) => (
          <ControlTowerSectionPanel key={section.key} section={section} />
        ))}
      </section>
      <DangerousOperationsPanel items={data.dangerous_operations} />
      <section
        className="ui-surface-panel mt-6 px-5 py-4 phone-down:px-4"
        aria-labelledby="sources-title"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="sources-title" className="m-0 text-lg text-text-strong">
              来源契约
            </h2>
            <p className="mb-0 mt-1 text-sm text-text-muted">
              快照版本 {data.snapshot_version}，生成于{" "}
              {new Date(data.generated_at).toLocaleString("zh-CN")}
            </p>
          </div>
          <Tag>仅低敏字段</Tag>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {data.source_contracts.map((item) => (
            <Tag key={item.contract_id}>
              {item.contract_id} · v{item.version} · {item.status}
            </Tag>
          ))}
        </div>
      </section>
    </>
  );
}
