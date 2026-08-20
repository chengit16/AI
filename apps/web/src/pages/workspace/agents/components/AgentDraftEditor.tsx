/** @description Agent 知识范围绑定、完整草稿 JSON 编辑与乐观锁保存交互。 */
import { Alert, Button, Input, Popconfirm, Select, Tag } from "antd";
import { Archive, Database, Save } from "lucide-react";
import { useState } from "react";

import type { AgentDetail } from "@/api/services/agents";
import type { KnowledgeBaseSummary } from "@/api/services/knowledge";

import { formatAgentTime, formatConfiguration, parseConfiguration } from "../config";

/** 草稿编辑器需要的权限、状态和命令。 */
export interface AgentDraftEditorProps {
  /** 当前选中的 Agent 与草稿聚合。 */
  detail: AgentDetail;
  /** 只控制编辑器体验，后端仍执行定义更新权限与资源范围校验。 */
  canUpdate: boolean;
  /** 只控制归档按钮展示，不构成授权边界。 */
  canArchive: boolean;
  /** 当前用户能否读取知识库清单；服务端仍独立校验每个范围成员。 */
  canReadKnowledgeBases: boolean;
  /** 当前空间经过服务端范围投影的知识库摘要。 */
  knowledgeBases: readonly KnowledgeBaseSummary[];
  /** 知识库清单正在首次加载。 */
  isKnowledgeLoading: boolean;
  /** 知识库清单读取失败，旧缓存不得继续参与范围绑定。 */
  isKnowledgeError: boolean;
  /** 保存或归档请求正在提交。 */
  isMutating: boolean;
  /** 提交完整配置和当前 revision。 */
  onSave: (configuration: Record<string, unknown>, expectedRevision: number) => void;
  /** 冻结选择并把新范围版本写入下一草稿 revision。 */
  onBindKnowledgeScope: (
    name: string,
    knowledgeBaseIds: string[],
    configuration: Record<string, unknown>,
    expectedRevision: number,
  ) => void;
  /** 重新读取知识库清单。 */
  onReloadKnowledgeBases: () => void;
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
  const [knowledgeBaseIds, setKnowledgeBaseIds] = useState(() =>
    Array.from(
      new Set(
        (props.detail.knowledge_scope_versions ?? []).flatMap((scope) => scope.knowledge_base_ids),
      ),
    ),
  );
  const parsed = parseConfiguration(value);
  const scopeVersions = props.detail.knowledge_scope_versions ?? [];
  const isActive = props.detail.agent.status === "active";
  const canBindKnowledgeScope = props.canUpdate && props.canReadKnowledgeBases && isActive;
  const scopeSuffix = ` 知识范围 r${props.detail.draft.revision + 1}`;
  const scopeName = `${props.detail.agent.name.slice(0, 120 - scopeSuffix.length)}${scopeSuffix}`;

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
        {props.canArchive && isActive && (
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
      <section
        className="mb-5 border-y border-solid border-border py-4"
        aria-labelledby="scope-title"
      >
        <div className="mb-3 flex items-center justify-between gap-3 nav-mobile:flex-col nav-mobile:items-start">
          <div className="flex min-w-0 items-center gap-2">
            <Database aria-hidden size={17} className="shrink-0 text-text-muted" />
            <h3 id="scope-title" className="m-0 text-base text-text-strong">
              知识范围
            </h3>
            <Tag>{scopeVersions.length} 个冻结版本</Tag>
          </div>
          <span className="text-sm text-text-muted">{knowledgeBaseIds.length} 个知识库</span>
        </div>
        {props.isKnowledgeError ? (
          <Alert
            type="error"
            showIcon
            title="知识库清单未能加载"
            action={<Button onClick={props.onReloadKnowledgeBases}>重新加载</Button>}
          />
        ) : (
          <div className="flex items-end gap-3 nav-mobile:flex-col nav-mobile:items-stretch">
            <div className="min-w-0 flex-1">
              <label className="mb-2 block text-sm font-650 text-text-strong" htmlFor="agent-scope">
                可检索知识库
              </label>
              <Select
                id="agent-scope"
                aria-label="可检索知识库"
                mode="multiple"
                allowClear
                showSearch
                optionFilterProp="label"
                maxTagCount="responsive"
                value={knowledgeBaseIds}
                options={props.knowledgeBases.map((base) => ({
                  value: base.knowledge_base_id,
                  label: `${base.name} · ${base.default_security_level}`,
                }))}
                loading={props.isKnowledgeLoading}
                disabled={!canBindKnowledgeScope || props.isMutating}
                placeholder="不选择时冻结为空范围"
                className="w-full"
                onChange={setKnowledgeBaseIds}
              />
            </div>
            {canBindKnowledgeScope && (
              <Button
                icon={<Save size={16} />}
                disabled={!parsed || props.isKnowledgeError}
                loading={props.isMutating}
                onClick={() =>
                  parsed &&
                  props.onBindKnowledgeScope(
                    scopeName,
                    knowledgeBaseIds,
                    parsed,
                    props.detail.draft.revision,
                  )
                }
              >
                冻结并保存范围
              </Button>
            )}
          </div>
        )}
      </section>
      <label className="mb-2 block text-sm font-650 text-text-strong" htmlFor="agent-configuration">
        完整配置 JSON
      </label>
      <Input.TextArea
        id="agent-configuration"
        aria-label="完整配置 JSON"
        value={value}
        readOnly={!props.canUpdate || !isActive}
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
      {props.canUpdate && isActive && (
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
