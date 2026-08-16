/** @description Agent 完整草稿 JSON 编辑器与乐观锁保存交互。 */
import { Alert, Button, Input, Popconfirm, Tag } from "antd";
import { Archive, Save } from "lucide-react";
import { useState } from "react";

import type { AgentDetail } from "@/api/services/agents";

import { formatAgentTime, formatConfiguration, parseConfiguration } from "../config";

/** 草稿编辑器需要的权限、状态和命令。 */
export interface AgentDraftEditorProps {
  /** 当前选中的 Agent 与草稿聚合。 */
  detail: AgentDetail;
  /** 只控制编辑器体验，后端仍执行定义更新权限与资源范围校验。 */
  canUpdate: boolean;
  /** 只控制归档按钮展示，不构成授权边界。 */
  canArchive: boolean;
  /** 保存或归档请求正在提交。 */
  isMutating: boolean;
  /** 提交完整配置和当前 revision。 */
  onSave: (configuration: Record<string, unknown>, expectedRevision: number) => void;
  /** 按当前 Agent version 归档定义。 */
  onArchive: (expectedVersion: number) => void;
}

/**
 * 编辑完整版本引用配置，并在本地只校验 JSON 对象结构。
 *
 * 服务端返回新 revision 后重置文本；未保存输入不会被后台查询覆盖，防止编辑过程中丢失。
 */
export function AgentDraftEditor(props: AgentDraftEditorProps) {
  const [value, setValue] = useState(() => formatConfiguration(props.detail.draft.configuration));
  const parsed = parseConfiguration(value);

  return (
    <section aria-labelledby="agent-draft-title">
      <div className="mb-4 flex items-start justify-between gap-4 nav-mobile:flex-col">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="agent-draft-title" className="m-0 text-xl text-text-strong">
              {props.detail.agent.name}
            </h2>
            <Tag>草稿 r{props.detail.draft.revision}</Tag>
            <Tag color={props.detail.draft.status === "editing" ? "processing" : "default"}>
              {props.detail.draft.status}
            </Tag>
          </div>
          <p className="mb-0 mt-2 text-sm text-text-muted">
            {props.detail.agent.description || "暂无说明"} · 更新于{" "}
            {formatAgentTime(props.detail.draft.updated_at)}
          </p>
        </div>
        {props.canArchive && props.detail.agent.status === "active" && (
          <Popconfirm
            title="归档这个 Agent？"
            description="历史候选、Release 和服务路由不会删除。"
            okText="确认归档"
            cancelText="取消"
            onConfirm={() => props.onArchive(props.detail.agent.version)}
          >
            <Button danger icon={<Archive size={16} />} loading={props.isMutating}>
              归档
            </Button>
          </Popconfirm>
        )}
      </div>
      <Alert
        className="mb-4"
        type="info"
        showIcon
        title="草稿不会直接进入 Runtime"
        description="此处保存完整版本引用配置；资源可见性、安全策略、预算和只读工具仍由服务端严格校验。"
      />
      <label className="mb-2 block text-sm font-650 text-text-strong" htmlFor="agent-configuration">
        完整配置 JSON
      </label>
      <Input.TextArea
        id="agent-configuration"
        aria-label="完整配置 JSON"
        value={value}
        readOnly={!props.canUpdate || props.detail.agent.status !== "active"}
        autoSize={{ minRows: 18, maxRows: 30 }}
        status={parsed ? undefined : "error"}
        className="font-mono text-xs leading-6"
        onChange={(event) => setValue(event.target.value)}
      />
      {!parsed && (
        <p className="mb-0 mt-2 text-sm text-danger-text" role="alert">
          配置必须是有效 JSON 对象后才能保存。
        </p>
      )}
      {props.canUpdate && props.detail.agent.status === "active" && (
        <div className="mt-4 flex justify-end">
          <Button
            type="primary"
            icon={<Save size={16} />}
            disabled={!parsed}
            loading={props.isMutating}
            onClick={() => parsed && props.onSave(parsed, props.detail.draft.revision)}
          >
            保存草稿
          </Button>
        </div>
      )}
    </section>
  );
}
