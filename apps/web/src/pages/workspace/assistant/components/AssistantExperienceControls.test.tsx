/** @description P6A-05 助手知识范围、临时附件、快捷指令和归档筛选交互测试。 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { AssistantConversation, ConversationAttachment } from "@/api/services/assistant";
import type { KnowledgeBaseSummary } from "@/api/services/knowledge";
import type { KnowledgeTag } from "@/api/services/knowledgeOrganization";

import { AssistantComposer } from "./AssistantComposer";
import { AssistantContextBar } from "./AssistantContextBar";
import { ConversationRail } from "./ConversationRail";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000905";
const ACTIVE_ID = "30000000-0000-4000-8000-000000000905";
const ARCHIVED_ID = "30000000-0000-4000-8000-000000000906";
const BASE_ID = "40000000-0000-4000-8000-000000000905";
const TAG_ID = "50000000-0000-4000-8000-000000000905";
const ATTACHMENT_ID = "60000000-0000-4000-8000-000000000905";

const activeConversation: AssistantConversation = {
  conversation_id: ACTIVE_ID,
  workspace_id: WORKSPACE_ID,
  created_by_account_id: "10000000-0000-4000-8000-000000000905",
  title: "活动合成会话",
  status: "active",
  scope_mode: "selected",
  knowledge_base_ids: [BASE_ID],
  tag_ids: [TAG_ID],
  created_at: "2026-08-30T10:00:00Z",
  updated_at: "2026-08-30T10:00:00Z",
  version: 2,
};
const archivedConversation: AssistantConversation = {
  ...activeConversation,
  conversation_id: ARCHIVED_ID,
  title: "已归档合成会话",
  status: "archived",
};
const knowledgeBase: KnowledgeBaseSummary = {
  knowledge_base_id: BASE_ID,
  name: "合成知识库",
  description: null,
  default_visibility: "private",
  default_security_level: "INTERNAL",
  updated_at: "2026-08-30T10:00:00Z",
};
const knowledgeTag: KnowledgeTag = {
  tag_id: TAG_ID,
  workspace_id: WORKSPACE_ID,
  name: "合成标签",
  color: null,
  created_by_account_id: activeConversation.created_by_account_id,
  created_at: "2026-08-30T10:00:00Z",
  updated_at: "2026-08-30T10:00:00Z",
  status: "active",
  deleted_at: null,
  version: 1,
};
const attachment: ConversationAttachment = {
  attachment_id: ATTACHMENT_ID,
  workspace_id: WORKSPACE_ID,
  conversation_id: ACTIVE_ID,
  file_name: "synthetic-context.md",
  media_type: "text/markdown",
  size_bytes: 42,
  content_hash: "a".repeat(64),
  created_at: "2026-08-30T10:00:00Z",
};

describe("P6A-05 助手体验控件", () => {
  it("固定快捷指令只预填输入框并由用户显式发送", async () => {
    const onSend = vi.fn();
    render(
      <AssistantComposer
        disabled={false}
        sending={false}
        cancellable={false}
        onSend={onSend}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "选择快捷指令" }));
    fireEvent.click(await screen.findByText("依据来源回答"));
    expect(screen.getByLabelText("输入知识问答问题")).toHaveValue(
      "请只依据当前知识范围回答，并为每个关键结论标注可核对的来源；证据不足时请直接说明。",
    );
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(onSend).toHaveBeenCalledWith(
      "请只依据当前知识范围回答，并为每个关键结论标注可核对的来源；证据不足时请直接说明。",
    );
  });

  it("范围切回当前空间时清空选择，并转交附件上传和删除命令", async () => {
    const onSaveScope = vi.fn();
    const onUpload = vi.fn();
    const onDeleteAttachment = vi.fn();
    const view = render(
      <AssistantContextBar
        conversation={activeConversation}
        knowledgeBases={[knowledgeBase]}
        knowledgeTags={[knowledgeTag]}
        attachments={[attachment]}
        canManageScope
        canManageAttachments
        busy={false}
        savingScope={false}
        uploading={false}
        onSaveScope={onSaveScope}
        onUpload={onUpload}
        onDeleteAttachment={onDeleteAttachment}
      />,
    );

    fireEvent.click(screen.getByText("当前空间"));
    fireEvent.click(screen.getByRole("button", { name: "保存知识范围" }));
    expect(onSaveScope).toHaveBeenCalledWith({
      scope_mode: "workspace",
      knowledge_base_ids: [],
      tag_ids: [],
    });

    const input = view.container.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, {
      target: {
        files: [new File(["合成临时附件"], "new-context.md", { type: "text/markdown" })],
      },
    });
    await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "删除附件 synthetic-context.md" }));
    expect(onDeleteAttachment).toHaveBeenCalledWith(ATTACHMENT_ID);
  });

  it("活动和归档筛选读取同一列表，并只为活动会话显示归档入口", () => {
    const onArchive = vi.fn();

    function RailHarness() {
      const [filter, setFilter] = useState<"active" | "archived">("active");
      return (
        <ConversationRail
          items={[activeConversation, archivedConversation]}
          selectedId={filter === "active" ? ACTIVE_ID : ARCHIVED_ID}
          loading={false}
          creating={false}
          filter={filter}
          canArchive
          archiving={false}
          onSelect={vi.fn()}
          onFilter={setFilter}
          onCreate={vi.fn()}
          onArchive={onArchive}
        />
      );
    }

    render(<RailHarness />);
    expect(screen.getByText("活动合成会话")).toBeInTheDocument();
    expect(screen.queryByText("已归档合成会话")).not.toBeInTheDocument();

    expect(screen.getByRole("button", { name: "归档会话 活动合成会话" })).toBeEnabled();

    fireEvent.click(screen.getByText("已归档"));
    expect(screen.getByText("已归档合成会话")).toBeInTheDocument();
    expect(screen.queryByText("活动合成会话")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /归档会话/ })).not.toBeInTheDocument();
  });
});
