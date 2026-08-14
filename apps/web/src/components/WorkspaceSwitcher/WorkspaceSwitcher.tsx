/**
 * @description 当前工作空间切换器
 * 切换后统一失效查询缓存；选项展示不替代服务端成员资格检查。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { App, Button, Form, Input, Modal, Select, Tooltip } from "antd";
import { Building2, Plus, UserRound } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";

import { errorMessage } from "@/api/client";
import {
  createEnterpriseWorkspace,
  getWorkspaces,
  switchWorkspace,
} from "@/api/services/workspaces";
import { useSessionStore } from "@/store/session";

interface CreateWorkspaceForm {
  name: string;
}

/**
 * 管理当前工作空间切换和企业空间创建入口。
 *
 * 工作空间决定后续查询的隔离上下文，因此切换成功后会失效全部查询缓存；
 * 前端选项只提供体验层入口，成员资格和空间权限仍由服务端逐请求校验。
 */
export function WorkspaceSwitcher() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const workspaceId = useSessionStore((state) => state.workspaceId);
  const setWorkspaceId = useSessionStore((state) => state.setWorkspaceId);
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<CreateWorkspaceForm>();
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: ({ signal }) => getWorkspaces(signal),
    enabled: Boolean(workspaceId),
  });
  const switchMutation = useMutation({
    mutationFn: switchWorkspace,
    onSuccess: async (workspace) => {
      setWorkspaceId(workspace.workspace_id);
      // 工作空间属于所有业务 Query 的隐式隔离维度，切换后必须清除旧空间缓存。
      await queryClient.invalidateQueries();
      navigate("/workspace/overview");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });
  const createMutation = useMutation({
    mutationFn: ({ name }: CreateWorkspaceForm) => createEnterpriseWorkspace(name),
    onSuccess: async (workspace) => {
      await queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      setCreateOpen(false);
      form.resetFields();
      switchMutation.mutate(workspace.workspace_id);
      void message.success("企业空间已创建");
    },
    onError: (error) => void message.error(errorMessage(error)),
  });

  const options = (workspaces.data ?? []).map((workspace) => ({
    value: workspace.workspace_id,
    label: (
      <span className="inline-flex items-center gap-2">
        {workspace.workspace_type === "personal" ? (
          <UserRound size={15} />
        ) : (
          <Building2 size={15} />
        )}
        {workspace.name}
      </span>
    ),
  }));

  return (
    <div className="flex min-w-0 items-center gap-2">
      {/* 移动态需为菜单、账号、创建按钮和两级间距预留空间，并留出 4px 取整余量。 */}
      <Select
        aria-label="切换工作空间"
        className="w-[min(260px,28vw)] nav-mobile:w-[min(210px,calc(100vw-196px))]"
        loading={workspaces.isLoading || switchMutation.isPending}
        options={options}
        value={workspaceId ?? undefined}
        onChange={(value) => switchMutation.mutate(value)}
        status={workspaces.isError ? "error" : undefined}
      />
      <Tooltip title="创建企业空间">
        <Button
          aria-label="创建企业空间"
          icon={<Plus size={17} />}
          onClick={() => setCreateOpen(true)}
        />
      </Tooltip>
      <Modal
        title="创建企业空间"
        open={createOpen}
        confirmLoading={createMutation.isPending}
        okText="创建"
        cancelText="取消"
        onCancel={() => setCreateOpen(false)}
        onOk={() => void form.validateFields().then((values) => createMutation.mutate(values))}
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item
            label="空间名称"
            name="name"
            rules={[{ required: true, message: "请输入空间名称" }, { max: 120 }]}
          >
            <Input autoFocus placeholder="例如：合成企业试点空间" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
