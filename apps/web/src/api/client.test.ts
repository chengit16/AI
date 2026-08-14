/** @description API Client 会话、错误转换与请求边界测试。 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiRequest, configureApiClient } from "@/api/client";

describe("平台 API Client", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    configureApiClient(
      () => ({ workspaceId: null, csrfToken: null }),
      () => undefined,
    );
  });

  it("multipart 上传保留浏览器生成的 boundary 并附加可信请求头", async () => {
    configureApiClient(
      () => ({ workspaceId: "workspace-id", csrfToken: "synthetic-csrf" }),
      () => undefined,
    );
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ accepted: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const body = new FormData();
    body.append("title", "合成上传文档");

    await apiRequest("/api/v1/synthetic-upload", { method: "POST", body });

    const request = fetchMock.mock.calls[0]?.[1];
    const headers = new Headers(request?.headers);
    expect(request?.body).toBe(body);
    expect(headers.get("Content-Type")).toBeNull();
    expect(headers.get("X-Workspace-ID")).toBe("workspace-id");
    expect(headers.get("X-CSRF-Token")).toBe("synthetic-csrf");
  });
});
