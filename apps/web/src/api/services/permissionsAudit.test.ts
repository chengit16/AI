/** @description P6B-05 权限与审计 Service 路径、筛选与安全请求测试。 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { configureApiClient } from "@/api/client";

import {
  createAuditExport,
  getAuditRecord,
  getRoleGovernance,
  listAuditExports,
  listAuditRecords,
  replaceRolePermissions,
} from "./permissionsAudit";

const WORKSPACE_ID = "20000000-0000-4000-8000-000000000905";

describe("P6B-05 权限与审计 Service", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    configureApiClient(
      () => ({ workspaceId: null, csrfToken: null }),
      () => undefined,
    );
  });

  it("映射角色版本保存、审计筛选、详情和导出接口", async () => {
    configureApiClient(
      () => ({ workspaceId: WORKSPACE_ID, csrfToken: "synthetic-csrf" }),
      () => undefined,
    );
    vi.spyOn(crypto, "randomUUID").mockReturnValue("00000000-0000-4000-8000-000000000905");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      async () =>
        new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );

    await getRoleGovernance(WORKSPACE_ID);
    await replaceRolePermissions(WORKSPACE_ID, "role-1", 7, []);
    await listAuditRecords(WORKSPACE_ID, {
      action: "role.permissions.replace",
      outcome: "succeeded",
    });
    await getAuditRecord(WORKSPACE_ID, "audit-1");
    await listAuditExports(WORKSPACE_ID);
    await createAuditExport(WORKSPACE_ID, { resourceType: "role" });

    const calls = fetchMock.mock.calls.map(([path, request]) => ({
      path: String(path),
      method: request?.method,
      body: request?.body,
    }));
    expect(calls.map((call) => call.method)).toEqual(["GET", "PUT", "GET", "GET", "GET", "POST"]);
    expect(calls[2]?.path).toContain("action=role.permissions.replace");
    expect(calls[2]?.path).toContain("outcome=succeeded");
    expect(JSON.parse(String(calls[1]?.body))).toEqual({
      expected_role_version: 7,
      items: [],
    });
    expect(JSON.parse(String(calls[5]?.body))).toMatchObject({
      idempotency_key: "audit-export-00000000-0000-4000-8000-000000000905",
      resource_type: "role",
      occurred_to: null,
    });
    expect(String(calls[5]?.body)).not.toContain("payload");
    expect(String(calls[5]?.body)).not.toContain("object_key");
  });
});
