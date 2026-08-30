/** @description 个人知识工作台，聚合授权统计、最近活动、全局搜索和既有业务快捷入口。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  App,
  Button,
  Checkbox,
  Input,
  Pagination,
  Segmented,
  Select,
  Skeleton,
  Tag,
} from "antd";
import {
  ArrowRight,
  Bot,
  Clock3,
  FileSearch,
  FileText,
  FolderPlus,
  LibraryBig,
  MessageSquareText,
  Search,
  Star,
  Upload,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { errorMessage } from "@/api/client";
import { getAssistantConversations } from "@/api/services/assistant";
import {
  getKnowledgeBases,
  getPersonalKnowledgeWorkbench,
  recordPersonalWorkbenchDocumentAccess,
  searchPublishedKnowledgeDocuments,
  type KnowledgeSearchItem,
  type KnowledgeSearchResponse,
} from "@/api/services/knowledge";
import { PageHeader } from "@/components/PageHeader/PageHeader";
import { StateView } from "@/components/StateView/StateView";
import { pageRoutes } from "@/config/resources";
import { useCurrentWorkspace } from "@/hooks/useCurrentWorkspace";
import { useWorkspaceMenuNavigation } from "@/hooks/useWorkspaceMenuNavigation";
import { knowledgeQueryKeys } from "@/pages/workspace/knowledge/queryKeys";

const PAGE_SIZE = 8;

type MatchType = "all" | "title" | "content";

interface SearchState {
  query: string;
  knowledgeBaseId?: string;
  matchType: MatchType;
  favoriteOnly: boolean;
  page: number;
}

/** 渲染个人空间首页；所有可见数据都来自后端当前文档权限投影。 */
export function PersonalKnowledgeWorkbench({
  onAcceptInvitation,
}: {
  onAcceptInvitation: () => void;
}) {
  // 1. 先建立当前空间和前端体验权限，后端仍会对每个工作台请求独立授权。
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const { workspaceId, currentWorkspace } = useCurrentWorkspace();
  const { visiblePermissionCodes } = useWorkspaceMenuNavigation();
  const has = (permission: string) => visiblePermissionCodes.has(permission);
  const canReadDocuments = has("knowledge.document.read");
  const canReadBases = has("knowledge.base.read");
  const canReadAssistant = has("assistant.conversation.read");
  const canOpenAssistant = has("assistant.page.access");
  const canAskAssistant = canOpenAssistant && has("assistant.message.create");
  const [draftQuery, setDraftQuery] = useState("");
  const [matchType, setMatchType] = useState<MatchType>("all");
  const [knowledgeBaseId, setKnowledgeBaseId] = useState<string>();
  const [favoriteOnly, setFavoriteOnly] = useState(false);
  const [searchState, setSearchState] = useState<SearchState | null>(null);

  // 2. 工作台、筛选选项和最近会话独立降级，主聚合失败时才阻断整个页面。
  const workbench = useQuery({
    queryKey: knowledgeQueryKeys.workbench(workspaceId),
    queryFn: ({ signal }) => getPersonalKnowledgeWorkbench(workspaceId!, signal),
    enabled: Boolean(workspaceId && canReadDocuments),
    retry: false,
  });
  const bases = useQuery({
    queryKey: knowledgeQueryKeys.bases(workspaceId),
    queryFn: ({ signal }) => getKnowledgeBases(workspaceId!, signal),
    enabled: Boolean(workspaceId && canReadBases),
    retry: false,
  });
  const conversations = useQuery({
    queryKey: ["assistant-conversations", workspaceId],
    queryFn: ({ signal }) => getAssistantConversations(workspaceId!, signal),
    enabled: Boolean(workspaceId && canReadAssistant),
    retry: false,
  });
  const searchSignature = searchState ? JSON.stringify(searchState) : "idle";
  const searchResults = useQuery({
    queryKey: knowledgeQueryKeys.search(workspaceId, searchSignature),
    queryFn: ({ signal }) =>
      searchPublishedKnowledgeDocuments(
        workspaceId!,
        {
          query: searchState!.query,
          knowledgeBaseId: searchState!.knowledgeBaseId,
          matchType: searchState!.matchType,
          favoriteOnly: searchState!.favoriteOnly,
          limit: PAGE_SIZE,
          offset: (searchState!.page - 1) * PAGE_SIZE,
        },
        signal,
      ),
    enabled: Boolean(workspaceId && canReadDocuments && searchState),
    retry: false,
  });
  const recordAccess = useMutation({
    mutationFn: (documentId: string) =>
      recordPersonalWorkbenchDocumentAccess(workspaceId!, documentId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: knowledgeQueryKeys.workbench(workspaceId) }),
    onError: (error) => void message.error(errorMessage(error)),
  });

  // 3. 统计展示只消费服务端聚合，不在浏览器重新推导授权范围内的数量。
  const statistics = workbench.data?.statistics;
  const metricItems = useMemo(
    () => [
      { label: "知识库", value: statistics?.knowledge_base_count ?? 0, icon: LibraryBig },
      { label: "文档", value: statistics?.document_count ?? 0, icon: FileText },
      { label: "已发布", value: statistics?.published_document_count ?? 0, icon: FileSearch },
      { label: "收藏", value: statistics?.favorite_document_count ?? 0, icon: Star },
      { label: "待索引", value: statistics?.pending_index_document_count ?? 0, icon: Clock3 },
    ],
    [statistics],
  );

  // 4. 所有快捷动作复用现有知识和问答路由，最近访问由服务端复核后再跳转。
  function runSearch(page = 1) {
    const query = draftQuery.trim();
    if (!query) return;
    setSearchState({ query, knowledgeBaseId, matchType, favoriteOnly, page });
  }

  function openDocument(item: { document_id: string; knowledge_base_id: string }) {
    recordAccess.mutate(item.document_id, {
      onSuccess: () =>
        navigate(
          `${pageRoutes.KnowledgeProductionPage}?base=${item.knowledge_base_id}&document=${item.document_id}`,
        ),
    });
  }

  function askAboutDocument(item: KnowledgeSearchItem) {
    if (!canAskAssistant) return;
    const prompt = `请根据《${item.title}》回答我的问题：`;
    navigate(`${pageRoutes.AssistantConversationsPage}?prompt=${encodeURIComponent(prompt)}`);
  }

  if (!canReadDocuments) {
    return (
      <StateView
        kind="denied"
        headingLevel={1}
        title="没有个人知识读取权限"
        description="当前角色不能读取个人工作台中的文档统计和搜索结果。"
      />
    );
  }
  if (workbench.isLoading) return <Skeleton active paragraph={{ rows: 12 }} />;
  if (workbench.isError || !workbench.data) {
    return (
      <StateView
        kind="error"
        headingLevel={1}
        title="个人工作台未能加载"
        description={errorMessage(workbench.error)}
        action={<Button onClick={() => void workbench.refetch()}>重新加载</Button>}
      />
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="PERSONAL KNOWLEDGE"
        title={currentWorkspace?.name ?? "个人知识工作台"}
        description="最近使用的文件、已发布知识和问答会话集中在当前个人空间。"
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button onClick={onAcceptInvitation}>接受邀请</Button>
            {has("knowledge.folder.create") && (
              <Button
                icon={<FolderPlus size={16} />}
                onClick={() => navigate(`${pageRoutes.KnowledgeProductionPage}?action=folder`)}
              >
                新建文件夹
              </Button>
            )}
            {has("knowledge.document.create") && (
              <Button
                type="primary"
                icon={<Upload size={16} />}
                onClick={() => navigate(`${pageRoutes.KnowledgeProductionPage}?action=upload`)}
              >
                上传文件
              </Button>
            )}
          </div>
        }
      />

      {(bases.isError || conversations.isError) && (
        <Alert
          className="mb-4"
          type="warning"
          showIcon
          title="部分信息暂时不可用"
          description={bases.isError ? "知识库筛选暂不可用。" : "最近问答会话暂不可用。"}
        />
      )}

      <section
        className="ui-surface-panel overflow-hidden"
        aria-labelledby="personal-statistics-title"
      >
        <h2 id="personal-statistics-title" className="sr-only">
          个人知识统计
        </h2>
        <div className="grid grid-cols-5 tablet-down:grid-cols-2 phone-down:grid-cols-1">
          {metricItems.map((metric) => (
            <div
              className="flex min-h-26 items-center gap-3 border-r border-r-solid border-border-soft px-5 py-4 last:border-r-0 tablet-down:border-b tablet-down:border-b-solid phone-down:border-r-0"
              key={metric.label}
            >
              <span className="ui-icon-badge h-10 w-10 flex-none">
                <metric.icon size={18} />
              </span>
              <div className="min-w-0">
                <strong className="block text-[22px] leading-none text-text-strong">
                  {metric.value.toLocaleString("zh-CN")}
                </strong>
                <span className="mt-2 block text-xs text-text-muted">{metric.label}</span>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section
        className="ui-surface-panel mt-4 overflow-hidden"
        aria-labelledby="knowledge-search-title"
      >
        <div className="border-b border-b-solid border-border px-5 py-4 phone-down:px-4">
          <div className="flex items-center gap-3">
            <span className="ui-icon-badge h-9 w-9 flex-none">
              <Search size={17} />
            </span>
            <div>
              <h2 id="knowledge-search-title" className="m-0 text-base">
                全局搜索
              </h2>
              <p className="mb-0 mt-1 text-xs text-text-muted">已发布文档</p>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-[minmax(240px,1fr)_auto_auto_auto] items-center gap-2 tablet-down:grid-cols-2 phone-down:grid-cols-1">
            <Input.Search
              value={draftQuery}
              maxLength={200}
              allowClear
              enterButton="搜索"
              loading={searchResults.isFetching}
              placeholder="搜索标题或文档内容"
              aria-label="全局搜索已发布文档"
              onChange={(event) => setDraftQuery(event.target.value)}
              onSearch={() => runSearch()}
            />
            <Segmented<MatchType>
              value={matchType}
              options={[
                { label: "全部", value: "all" },
                { label: "标题", value: "title" },
                { label: "内容", value: "content" },
              ]}
              onChange={setMatchType}
            />
            <Select
              className="min-w-40 phone-down:w-full"
              allowClear
              disabled={!canReadBases}
              loading={bases.isLoading}
              value={knowledgeBaseId}
              placeholder="全部知识库"
              aria-label="按知识库筛选"
              options={(bases.data ?? []).map((base) => ({
                label: base.name,
                value: base.knowledge_base_id,
              }))}
              onChange={setKnowledgeBaseId}
            />
            <Checkbox
              checked={favoriteOnly}
              onChange={(event) => setFavoriteOnly(event.target.checked)}
            >
              仅收藏
            </Checkbox>
          </div>
        </div>
        <SearchResults
          state={searchState}
          result={searchResults.data}
          loading={searchResults.isLoading || searchResults.isFetching}
          error={searchResults.error}
          onRetry={() => void searchResults.refetch()}
          onOpen={openDocument}
          onAsk={askAboutDocument}
          canAsk={canAskAssistant}
          onPageChange={(page) => {
            if (!searchState) return;
            setSearchState({ ...searchState, page });
          }}
        />
      </section>

      <div className="mt-4 grid grid-cols-[minmax(0,1.4fr)_minmax(280px,0.6fr)] gap-4 tablet-down:grid-cols-1">
        <DocumentActivity
          recent={workbench.data.recent_documents}
          favorites={workbench.data.favorite_documents}
          opening={recordAccess.isPending}
          onOpen={openDocument}
        />
        <section
          className="ui-surface-panel overflow-hidden"
          aria-labelledby="recent-conversations-title"
        >
          <div className="flex items-center justify-between border-b border-b-solid border-border px-5 py-4">
            <div className="flex items-center gap-3">
              <MessageSquareText size={18} />
              <h2 id="recent-conversations-title" className="m-0 text-base">
                最近问答
              </h2>
            </div>
            {canOpenAssistant && (
              <Button
                type="text"
                icon={<Bot size={16} />}
                onClick={() => navigate(pageRoutes.AssistantConversationsPage)}
              >
                进入助手
              </Button>
            )}
          </div>
          <div className="divide-y divide-border-soft">
            {(conversations.data ?? []).slice(0, 5).map((conversation) => (
              <button
                type="button"
                className="flex min-h-15 w-full items-center gap-3 border-0 bg-transparent px-5 py-3 text-left hover:bg-surface-muted"
                key={conversation.conversation_id}
                onClick={() =>
                  navigate(
                    `${pageRoutes.AssistantConversationsPage}?conversation=${conversation.conversation_id}`,
                  )
                }
              >
                <MessageSquareText className="flex-none text-text-muted" size={16} />
                <span className="min-w-0 flex-1 truncate text-sm">
                  {conversation.title || "未命名问答"}
                </span>
                <span className="flex-none text-[11px] text-text-muted">
                  {formatActivityTime(conversation.updated_at)}
                </span>
              </button>
            ))}
            {!canReadAssistant && (
              <div className="px-5 py-8 text-center text-sm text-text-muted">
                当前角色不能读取问答会话
              </div>
            )}
            {canReadAssistant &&
              !conversations.isLoading &&
              (conversations.data?.length ?? 0) === 0 && (
                <div className="px-5 py-8 text-center text-sm text-text-muted">暂无问答会话</div>
              )}
            {conversations.isLoading && (
              <Skeleton className="px-5 py-4" active paragraph={{ rows: 3 }} />
            )}
          </div>
        </section>
      </div>
    </>
  );
}

interface SearchResultsProps {
  /** 最近一次已提交的搜索条件；空值表示尚未搜索。 */
  state: SearchState | null;
  /** 服务端按权限投影的搜索页。 */
  result: KnowledgeSearchResponse | undefined;
  /** 首次加载或翻页请求状态。 */
  loading: boolean;
  /** 搜索请求失败原因。 */
  error: unknown;
  /** 重新执行当前搜索。 */
  onRetry: () => void;
  /** 打开命中文档并记录访问。 */
  onOpen: (item: KnowledgeSearchItem) => void;
  /** 将文档标题带入问答草稿。 */
  onAsk: (item: KnowledgeSearchItem) => void;
  /** 当前角色是否可进入助手并发起提问。 */
  canAsk: boolean;
  /** 切换服务端分页。 */
  onPageChange: (page: number) => void;
}

/** 展示搜索生命周期、索引降级和文档动作。 */
function SearchResults(props: SearchResultsProps) {
  if (!props.state) {
    return <div className="px-5 py-9 text-center text-sm text-text-muted">输入关键词开始搜索</div>;
  }
  if (props.loading && !props.result)
    return <Skeleton className="px-5 py-5" active paragraph={{ rows: 5 }} />;
  if (props.error) {
    return (
      <StateView
        kind="error"
        title="搜索未完成"
        description={errorMessage(props.error)}
        action={<Button onClick={props.onRetry}>重新搜索</Button>}
      />
    );
  }
  if (!props.result) return null;
  const contentRestricted =
    props.state.matchType !== "title" && !props.result.content_search_available;
  const partiallyIndexed =
    props.state.matchType !== "title" &&
    props.result.content_search_available &&
    props.result.unavailable_index_document_count > 0;
  return (
    <div>
      {(contentRestricted || partiallyIndexed) && (
        <Alert
          className="m-4"
          type="warning"
          showIcon
          title="搜索范围部分受限"
          description={
            contentRestricted
              ? "当前字段权限不允许搜索文档正文，结果仅包含标题命中。"
              : `${props.result.unavailable_index_document_count} 份已发布文档的索引尚未就绪。`
          }
        />
      )}
      {props.result.items.length === 0 ? (
        <StateView kind="empty" title="没有搜索结果" description="调整关键词或筛选条件后重试。" />
      ) : (
        <div className="divide-y divide-border-soft">
          {props.result.items.map((item) => (
            <article className="px-5 py-4 phone-down:px-4" key={item.document_id}>
              <div className="flex items-start gap-4 phone-down:flex-col">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="m-0 min-w-0 truncate text-sm font-600">{item.title}</h3>
                    <Tag color={item.matched_by === "title" ? "blue" : "green"}>
                      {item.matched_by === "title"
                        ? "标题命中"
                        : item.matched_by === "content"
                          ? "内容命中"
                          : "标题与内容"}
                    </Tag>
                    {item.is_favorite && <Star size={14} fill="currentColor" />}
                  </div>
                  <p className="mb-0 mt-1 text-xs text-text-muted">
                    {item.knowledge_base_name} · {formatActivityTime(item.published_at)}
                  </p>
                  {item.excerpt && (
                    <p className="mb-0 mt-3 line-clamp-2 text-[13px] leading-6 text-text-secondary">
                      {item.excerpt}
                    </p>
                  )}
                </div>
                <div className="flex flex-none gap-2">
                  <Button size="small" onClick={() => props.onOpen(item)}>
                    打开
                  </Button>
                  {props.canAsk && (
                    <Button size="small" icon={<Bot size={14} />} onClick={() => props.onAsk(item)}>
                      进入问答
                    </Button>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
      {props.result.total > PAGE_SIZE && (
        <div className="flex justify-end border-t border-t-solid border-border px-5 py-3">
          <Pagination
            size="small"
            current={props.state.page}
            pageSize={PAGE_SIZE}
            total={props.result.total}
            showSizeChanger={false}
            onChange={props.onPageChange}
          />
        </div>
      )}
    </div>
  );
}

interface DocumentActivityProps {
  /** 当前账号最近访问的获权文档。 */
  recent: readonly PersonalDocument[];
  /** 当前账号收藏且仍获权的文档。 */
  favorites: readonly PersonalDocument[];
  /** 最近访问写入和跳转是否正在执行。 */
  opening: boolean;
  /** 打开目标文档。 */
  onOpen: (item: PersonalDocument) => void;
}

type PersonalDocument = Awaited<
  ReturnType<typeof getPersonalKnowledgeWorkbench>
>["recent_documents"][number];

/** 并列展示最近访问和收藏，两个列表复用同一服务端摘要。 */
function DocumentActivity(props: DocumentActivityProps) {
  const [view, setView] = useState<"recent" | "favorites">("recent");
  const items = view === "recent" ? props.recent : props.favorites;
  return (
    <section className="ui-surface-panel overflow-hidden" aria-labelledby="document-activity-title">
      <div className="flex items-center justify-between border-b border-b-solid border-border px-5 py-4 phone-down:flex-col phone-down:items-start phone-down:gap-3">
        <div className="flex items-center gap-3">
          <Clock3 size={18} />
          <h2 id="document-activity-title" className="m-0 text-base">
            文档活动
          </h2>
        </div>
        <Segmented
          value={view}
          options={[
            { label: "最近", value: "recent" },
            { label: "收藏", value: "favorites" },
          ]}
          onChange={setView}
        />
      </div>
      <div className="divide-y divide-border-soft">
        {items.map((item) => (
          <button
            type="button"
            className="flex min-h-16 w-full items-center gap-3 border-0 bg-transparent px-5 py-3 text-left hover:bg-surface-muted"
            disabled={props.opening}
            key={item.document_id}
            onClick={() => props.onOpen(item)}
          >
            <FileText className="flex-none text-text-muted" size={17} />
            <span className="min-w-0 flex-1">
              <strong className="block truncate text-sm font-500">{item.title}</strong>
              <span className="mt-1 block truncate text-[11px] text-text-muted">
                {item.knowledge_base_name} ·{" "}
                {formatActivityTime(item.last_accessed_at ?? item.updated_at)}
              </span>
            </span>
            <Tag color={item.is_indexed ? "success" : "warning"}>
              {item.is_indexed ? "已索引" : "待索引"}
            </Tag>
            <ArrowRight className="flex-none text-text-muted" size={15} />
          </button>
        ))}
        {items.length === 0 && (
          <div className="px-5 py-8 text-center text-sm text-text-muted">
            {view === "recent" ? "暂无最近文档" : "暂无收藏文档"}
          </div>
        )}
      </div>
    </section>
  );
}

function formatActivityTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
