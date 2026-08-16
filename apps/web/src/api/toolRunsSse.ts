/** @description 工具 Run 的可恢复 SSE 客户端，严格校验整数游标和脱敏事件。 */
import { apiStreamRequest, PlatformApiError } from "@/api/client";

/** 服务端允许浏览器消费的最小工具进度事件。 */
export interface ToolRunProgressEvent {
  /** 当前工作空间。 */
  workspaceId: string;
  /** 当前工具 Run。 */
  runId: string;
  /** Run 内严格连续的持久化游标。 */
  cursor: number;
  /** 稳定事件类型。 */
  eventType: string;
  /** 事件发生后的 Run 状态。 */
  runState: string;
  /** 事件关联 Step，没有关联时为空。 */
  stepId: string | null;
  /** Step 状态，没有关联时为空。 */
  stepState: string | null;
  /** 稳定错误码，不包含异常正文。 */
  errorCode: string | null;
}

/** 表示工具进度流违反已冻结协议。 */
export class ToolRunSseProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ToolRunSseProtocolError";
  }
}

interface RawSseEvent {
  id: string | null;
  data: string;
}

const TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "timed_out"]);

/** 按最后确认游标读取同一 Run；断线只重连，不创建新任务。 */
export async function* streamToolRun(
  workspaceId: string,
  runId: string,
  options: { signal?: AbortSignal; lastCursor?: number; maxReconnects?: number } = {},
): AsyncGenerator<ToolRunProgressEvent, void, undefined> {
  let cursor = options.lastCursor ?? 0;
  let reconnects = 0;
  const maxReconnects = options.maxReconnects ?? 6;
  let terminal = false;

  while (!terminal) {
    try {
      const response = await apiStreamRequest(
        `/api/v1/workspaces/${workspaceId}/tool-runs/${runId}/events`,
        { signal: options.signal, lastEventId: cursor > 0 ? String(cursor) : null },
      );
      if (!response.body) throw new ToolRunSseProtocolError("浏览器未提供工具进度响应流");
      for await (const raw of parseSse(response.body, options.signal)) {
        const event = parseToolRunEvent(raw, workspaceId, runId, cursor);
        cursor = event.cursor;
        terminal = TERMINAL_STATES.has(event.runState);
        yield event;
      }
    } catch (error) {
      if (options.signal?.aborted) throw error;
      if (error instanceof PlatformApiError || error instanceof ToolRunSseProtocolError)
        throw error;
      if (reconnects >= maxReconnects) {
        throw new ToolRunSseProtocolError("工具进度 SSE 重连次数已达上限");
      }
      await waitBeforeReconnect(reconnects, options.signal);
      reconnects += 1;
      continue;
    }
    if (!terminal) {
      if (reconnects >= maxReconnects) {
        throw new ToolRunSseProtocolError("工具进度 SSE 重连次数已达上限");
      }
      await waitBeforeReconnect(reconnects, options.signal);
      reconnects += 1;
    }
  }
}

async function* parseSse(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<RawSseEvent, void, undefined> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let id: string | null = null;
  let data: string[] = [];
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException("请求已取消", "AbortError");
      const { done, value } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const lines = buffer.split(/\r?\n/);
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line) {
          if (data.length) yield { id, data: data.join("\n") };
          id = null;
          data = [];
          continue;
        }
        if (line.startsWith(":")) continue;
        const separator = line.indexOf(":");
        const field = separator === -1 ? line : line.slice(0, separator);
        const text = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
        if (field === "id") id = text;
        if (field === "data") data.push(text);
      }
      if (done) {
        if (data.length) yield { id, data: data.join("\n") };
        return;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseToolRunEvent(
  raw: RawSseEvent,
  workspaceId: string,
  runId: string,
  lastCursor: number,
): ToolRunProgressEvent {
  let value: unknown;
  try {
    value = JSON.parse(raw.data);
  } catch {
    throw new ToolRunSseProtocolError("工具进度事件不是有效 JSON");
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ToolRunSseProtocolError("工具进度事件不是对象");
  }
  const item = value as Record<string, unknown>;
  const cursor = item.cursor;
  if (
    item.schema_version !== 1 ||
    item.workspace_id !== workspaceId ||
    item.run_id !== runId ||
    typeof item.event_type !== "string" ||
    typeof item.run_state !== "string"
  ) {
    throw new ToolRunSseProtocolError("工具进度事件身份或版本无效");
  }
  if (!Number.isInteger(cursor) || (cursor as number) !== lastCursor + 1) {
    throw new ToolRunSseProtocolError("工具进度游标不连续");
  }
  if (raw.id !== String(cursor)) throw new ToolRunSseProtocolError("SSE 帧与事件游标不一致");
  return {
    workspaceId,
    runId,
    cursor: cursor as number,
    eventType: item.event_type,
    runState: item.run_state,
    stepId: typeof item.step_id === "string" ? item.step_id : null,
    stepState: typeof item.step_state === "string" ? item.step_state : null,
    errorCode: typeof item.error_code === "string" ? item.error_code : null,
  };
}

async function waitBeforeReconnect(attempt: number, signal?: AbortSignal) {
  const delay = Math.min(4_000, 250 * 2 ** attempt);
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, delay);
    signal?.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timer);
        reject(new DOMException("请求已取消", "AbortError"));
      },
      { once: true },
    );
  });
}
