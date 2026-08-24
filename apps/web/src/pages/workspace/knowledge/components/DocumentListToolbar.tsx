/** @description 知识文档名称、标签筛选和批量操作工具栏。 */
import { Button, Input, Popconfirm, Select } from "antd";
import { FolderInput, Search, Tags, Trash2, X } from "lucide-react";

import type { KnowledgeTag } from "@/api/services/knowledgeOrganization";

interface DocumentListToolbarProps {
  /** 当前名称搜索词。 */
  searchText: string;
  /** 当前标签筛选 ID。 */
  selectedTagId: string | null;
  /** 当前可用活动标签。 */
  tags: readonly KnowledgeTag[];
  /** 当前选中文档数量。 */
  selectedCount: number;
  /** 只控制批量移动和标签入口，服务端仍逐篇授权。 */
  canOrganize: boolean;
  /** 只控制批量删除入口，服务端仍逐篇授权。 */
  canDelete: boolean;
  /** 更新名称搜索词。 */
  onSearchChange: (value: string) => void;
  /** 更新标签筛选。 */
  onTagChange: (tagId: string | null) => void;
  /** 清空当前批量选择。 */
  onClearSelection: () => void;
  /** 打开批量移动流程。 */
  onMoveSelected: () => void;
  /** 打开批量标签流程。 */
  onTagSelected: () => void;
  /** 将选中文档移入回收站。 */
  onDeleteSelected: () => void;
}

/** 提供不会改变服务端事实的筛选，并将批量命令交回页面状态层。 */
export function DocumentListToolbar(props: DocumentListToolbarProps) {
  return (
    <div className="mb-4 flex min-h-11 flex-wrap items-center gap-2">
      <Input
        className="w-[260px] phone-down:w-full"
        value={props.searchText}
        allowClear
        prefix={<Search size={16} />}
        placeholder="搜索文档名称"
        onChange={(event) => props.onSearchChange(event.target.value)}
      />
      <Select
        className="w-[180px] phone-down:w-full"
        value={props.selectedTagId ?? undefined}
        allowClear
        placeholder="全部标签"
        options={props.tags.map((tag) => ({ value: tag.tag_id, label: tag.name }))}
        onChange={(value) => props.onTagChange(value ?? null)}
      />
      {props.selectedCount > 0 && (
        <div className="flex min-h-11 flex-wrap items-center gap-1 border-l border-l-solid border-border pl-2 phone-down:w-full phone-down:border-l-0 phone-down:pl-0">
          <span className="px-2 text-sm text-text-muted">已选 {props.selectedCount} 篇</span>
          {props.canOrganize && (
            <>
              <Button type="text" icon={<FolderInput size={16} />} onClick={props.onMoveSelected}>
                移动
              </Button>
              <Button type="text" icon={<Tags size={16} />} onClick={props.onTagSelected}>
                标签
              </Button>
            </>
          )}
          {props.canDelete && (
            <Popconfirm
              title={`删除选中的 ${props.selectedCount} 篇文档？`}
              okText="删除"
              cancelText="取消"
              onConfirm={props.onDeleteSelected}
            >
              <Button type="text" danger icon={<Trash2 size={16} />}>
                删除
              </Button>
            </Popconfirm>
          )}
          <Button
            type="text"
            aria-label="清空文档选择"
            icon={<X size={16} />}
            onClick={props.onClearSelection}
          />
        </div>
      )}
    </div>
  );
}
