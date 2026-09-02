/** @description P6B-04 企业知识 Service 的治理与发布审批 HTTP 映射测试。 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { configureApiClient } from "@/api/client";

import {
  archiveEnterpriseCategory,
  archiveTeamKnowledgeDomain,
  createEnterpriseCategory,
  createTeamKnowledgeDomain,
  getEnterpriseKnowledgePortal,
  getEnterpriseDocumentDetail,
  getEnterpriseDocumentPublishRequest,
  listEnterpriseDocumentPublishRequests,
  requestEnterpriseDocumentPublish,
  replaceEnterpriseCategoryDocuments,
  replaceTeamKnowledgeDomainScope,
  resolveTeamKnowledgeDomainScope,
  updateEnterpriseCategory,
  updateTeamKnowledgeDomain,
} from "./enterpriseKnowledge";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000903";
const CATEGORY_ID = "30000000-0000-4000-8000-000000000903";
const DOMAIN_ID = "40000000-0000-4000-8000-000000000903";

describe("P6B-04 企业知识 Service", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    configureApiClient(
      () => ({ workspaceId: null, csrfToken: null }),
      () => undefined,
    );
  });

  it("把十个治理能力映射为稳定路径、方法和整体替换命令", async () => {
    // 1. 为每次请求生成独立响应体，模拟浏览器不可重复消费的 Response。
    configureApiClient(
      () => ({ workspaceId: WORKSPACE_ID, csrfToken: "synthetic-csrf" }),
      () => undefined,
    );
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );

    // 2. 依次执行全部治理能力，并核对高风险整体替换命令没有被拆分。
    await getEnterpriseKnowledgePortal(WORKSPACE_ID);
    await createEnterpriseCategory(WORKSPACE_ID, {
      name: "合成分类",
      description: null,
      parent_category_id: null,
      visibility: "public",
      department_ids: [],
      document_ids: [],
      approval_required: false,
    });
    await updateEnterpriseCategory(WORKSPACE_ID, CATEGORY_ID, {
      expected_version: 2,
      name: "合成分类二版",
      description: null,
      parent_category_id: null,
      visibility: "private",
      department_ids: [],
      approval_required: true,
    });
    await archiveEnterpriseCategory(WORKSPACE_ID, CATEGORY_ID, 3);
    await replaceEnterpriseCategoryDocuments(WORKSPACE_ID, CATEGORY_ID, 4, ["document-1"]);
    await createTeamKnowledgeDomain(WORKSPACE_ID, {
      name: "合成知识域",
      description: null,
      member_ids: [],
      department_ids: [],
      knowledge_base_ids: [],
      rag_mode: "balanced",
      top_k: 8,
      minimum_score: 0.25,
    });
    await updateTeamKnowledgeDomain(WORKSPACE_ID, DOMAIN_ID, {
      expected_version: 2,
      name: "合成知识域二版",
      description: null,
      rag_mode: "precision",
      top_k: 6,
      minimum_score: 0.4,
    });
    await archiveTeamKnowledgeDomain(WORKSPACE_ID, DOMAIN_ID, 3);
    await replaceTeamKnowledgeDomainScope(WORKSPACE_ID, DOMAIN_ID, {
      expected_version: 4,
      member_ids: ["member-1"],
      department_ids: ["department-1"],
      knowledge_base_ids: ["knowledge-base-1"],
    });
    await resolveTeamKnowledgeDomainScope(WORKSPACE_ID, DOMAIN_ID);

    const calls = fetchMock.mock.calls.map(([path, request]) => ({
      path,
      method: request?.method,
      body: request?.body,
    }));
    expect(calls.map((call) => [call.method, call.path])).toEqual([
      ["GET", `/api/v1/workspaces/${WORKSPACE_ID}/enterprise-knowledge`],
      ["POST", `/api/v1/workspaces/${WORKSPACE_ID}/enterprise-categories`],
      ["PUT", `/api/v1/workspaces/${WORKSPACE_ID}/enterprise-categories/${CATEGORY_ID}`],
      ["POST", `/api/v1/workspaces/${WORKSPACE_ID}/enterprise-categories/${CATEGORY_ID}/archive`],
      ["PUT", `/api/v1/workspaces/${WORKSPACE_ID}/enterprise-categories/${CATEGORY_ID}/documents`],
      ["POST", `/api/v1/workspaces/${WORKSPACE_ID}/team-knowledge-domains`],
      ["PUT", `/api/v1/workspaces/${WORKSPACE_ID}/team-knowledge-domains/${DOMAIN_ID}`],
      ["POST", `/api/v1/workspaces/${WORKSPACE_ID}/team-knowledge-domains/${DOMAIN_ID}/archive`],
      ["PUT", `/api/v1/workspaces/${WORKSPACE_ID}/team-knowledge-domains/${DOMAIN_ID}/scope`],
      [
        "GET",
        `/api/v1/workspaces/${WORKSPACE_ID}/team-knowledge-domains/${DOMAIN_ID}/resolved-scope`,
      ],
    ]);
    expect(JSON.parse(String(calls[4]?.body))).toEqual({
      expected_version: 4,
      document_ids: ["document-1"],
    });
    expect(JSON.parse(String(calls[8]?.body))).toEqual({
      expected_version: 4,
      member_ids: ["member-1"],
      department_ids: ["department-1"],
      knowledge_base_ids: ["knowledge-base-1"],
    });
  });

  it("映射版本详情、发布申请和参与者台账路径", async () => {
    configureApiClient(
      () => ({ workspaceId: WORKSPACE_ID, csrfToken: "synthetic-csrf" }),
      () => undefined,
    );
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );

    await getEnterpriseDocumentDetail(WORKSPACE_ID, "knowledge-base-1", "document-1");
    await requestEnterpriseDocumentPublish(
      WORKSPACE_ID,
      "document-1",
      "version-1",
      "synthetic-p6b04-service",
    );
    await listEnterpriseDocumentPublishRequests(WORKSPACE_ID);
    await getEnterpriseDocumentPublishRequest(WORKSPACE_ID, "publish-request-1");

    const calls = fetchMock.mock.calls.map(([path, request]) => ({
      path,
      method: request?.method,
      body: request?.body,
    }));
    expect(calls.map((call) => [call.method, call.path])).toEqual([
      [
        "GET",
        `/api/v1/workspaces/${WORKSPACE_ID}/knowledge-bases/knowledge-base-1/documents/document-1`,
      ],
      [
        "POST",
        `/api/v1/workspaces/${WORKSPACE_ID}/enterprise-documents/document-1/versions/version-1/publish-requests`,
      ],
      ["GET", `/api/v1/workspaces/${WORKSPACE_ID}/document-publish-requests?limit=100`],
      ["GET", `/api/v1/workspaces/${WORKSPACE_ID}/document-publish-requests/publish-request-1`],
    ]);
    expect(JSON.parse(String(calls[1]?.body))).toEqual({
      idempotency_key: "synthetic-p6b04-service",
    });
  });
});
