/** @description API Client 会话、错误转换与请求边界测试。 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiDownloadRequest, apiRequest, configureApiClient, PlatformApiError } from "@/api/client";

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

  it("204 响应即使保留 JSON Content-Type 也不解析空响应体", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, {
        status: 204,
        headers: { "Content-Type": "application/json" },
      }),
    );

    await expect(
      apiRequest<void>("/api/v1/synthetic-resource", { method: "DELETE" }),
    ).resolves.toBe(undefined);
  });

  it("文件下载附加工作空间并解码服务端文件名", async () => {
    configureApiClient(
      () => ({ workspaceId: "workspace-id", csrfToken: null }),
      () => undefined,
    );
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("synthetic-content", {
        status: 200,
        headers: {
          "Content-Type": "text/plain",
          "Content-Disposition": "attachment; filename*=UTF-8''%E5%90%88%E6%88%90.txt",
        },
      }),
    );

    const result = await apiDownloadRequest("/api/v1/synthetic-download");

    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers);
    expect(headers.get("X-Workspace-ID")).toBe("workspace-id");
    expect(result.fileName).toBe("合成.txt");
    expect(await result.blob.text()).toBe("synthetic-content");
  });

  it("文件下载失败时保留后端稳定错误码", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "RESOURCE_NOT_FOUND",
          message: "资源不存在或不可见",
          retryable: false,
        }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(apiDownloadRequest("/api/v1/synthetic-download")).rejects.toEqual(
      expect.objectContaining<Partial<PlatformApiError>>({
        status: 404,
        code: "RESOURCE_NOT_FOUND",
      }),
    );
  });
});
