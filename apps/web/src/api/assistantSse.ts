/** @description 助手 Run 的可恢复 SSE 客户端，集中处理游标、重连和顺序门禁。 */
import { apiStreamRequest, PlatformApiError } from "@/api/client";

/** 表示已通过 Run、游标、序号和载荷校验的单条助手流事件。 */
export interface AssistantStreamEvent {
  /** 事件稳定标识；派生 snapshot 不会作为后续回放游标。 */
  eventId: string;
  /** 服务端注册的事件类型。 */
  eventType: string;
  /** Run 内单调递增的持久化序号。 */
  sequenceNo: number;
  /** 已验证为对象的业务载荷。 */
  payload: Record<string, unknown>;
  /** 标识事件是否来自持久化事实。 */
  persisted: boolean;
  /** 保留完整协议对象，供诊断和后续兼容处理使用。 */
  raw: Record<string, unknown>;
}

/** 表示服务端 SSE 违反已冻结协议，调用方不得继续拼接正文。 */
export class AssistantSseProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AssistantSseProtocolError";
  }
}

interface RawSseEvent {
  id: string | null;
  data: string;
}

/**
 * 读取一个助手 Run，连接断开时按同一 Run 和最后确认游标重连。
 *
 * 客户端不创建新 Run；只在服务端返回的持久化事件上推进游标，派生的 snapshot
 * 不会覆盖 Last-Event-ID。顺序门禁失败时立即停止，避免把乱序正文展示给用户。
 */
export async function* streamAssistantRun(
  workspaceId: string,
  conversationId: string,
  runId: string,
  options: {
    signal?: AbortSignal;
    lastEventId?: string | null;
    maxReconnects?: number;
    eventsPath?: string;
  } = {},
): AsyncGenerator<AssistantStreamEvent, void, undefined> {
  let cursor = options.lastEventId ?? null;
  let lastSequence = 0;
  let reconnects = 0;
  const maxReconnects = options.maxReconnects ?? 6;
  let terminal = false;

  while (!terminal) {
    try {
      // 连接建立和响应体读取都可能因短暂网络故障失败，二者共享同一重连预算与游标。
      const response = await apiStreamRequest(
        options.eventsPath ??
          `/api/v1/workspaces/${workspaceId}/conversations/${conversationId}/runs/${runId}/events`,
        { signal: options.signal, lastEventId: cursor },
      );
      if (!response.body) throw new AssistantSseProtocolError("浏览器未提供 SSE 响应流");
      for await (const raw of parseSse(response.body, options.signal)) {
        const event = parseAssistantEvent(raw, runId, lastSequence);
        if (event.persisted) {
          cursor = event.eventId;
          lastSequence = event.sequenceNo;
        }
        yield event;
        terminal = event.eventType === "message.snapshot" || event.eventType === "message.failed";
        if (event.eventType === "message.completed") terminal = true;
      }
    } catch (error) {
      if (options.signal?.aborted) throw error;
      if (error instanceof PlatformApiError || error instanceof AssistantSseProtocolError)
        throw error;
      if (reconnects >= maxReconnects) throw new AssistantSseProtocolError("SSE 重连次数已达上限");
      await waitBeforeReconnect(reconnects, options.signal);
      reconnects += 1;
      continue;
    }

    if (!terminal) {
      if (reconnects >= maxReconnects) throw new AssistantSseProtocolError("SSE 重连次数已达上限");
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
        const valueText = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
        if (field === "id") id = valueText;
        if (field === "data") data.push(valueText);
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

function parseAssistantEvent(raw: RawSseEvent, runId: string, lastSequence: number) {
  let value: unknown;
  try {
    value = JSON.parse(raw.data);
  } catch (error) {
    throw new AssistantSseProtocolError(`SSE 事件 JSON 无法解析: ${String(error)}`);
  }
  if (!value || typeof value !== "object") throw new AssistantSseProtocolError("SSE 事件不是对象");
  const item = value as Record<string, unknown>;
  if (
    item.run_id !== runId ||
    typeof item.event_id !== "string" ||
    typeof item.event_type !== "string"
  ) {
    throw new AssistantSseProtocolError("SSE 事件缺少稳定 Run 标识");
  }
  const sequenceNo = item.sequence_no;
  if (typeof sequenceNo !== "number" || !Number.isInteger(sequenceNo) || sequenceNo < 1) {
    throw new AssistantSseProtocolError("SSE 事件序号无效");
  }
  const persisted = item.event_type !== "message.snapshot";
  if (persisted && sequenceNo <= lastSequence) {
    throw new AssistantSseProtocolError("SSE 事件序号未严格递增");
  }
  const payload = item.payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new AssistantSseProtocolError("SSE 事件载荷无效");
  }
  if (persisted && raw.id !== item.event_id) {
    throw new AssistantSseProtocolError("SSE 持久化事件游标不一致");
  }
  return {
    eventId: item.event_id,
    eventType: item.event_type,
    sequenceNo,
    payload: payload as Record<string, unknown>,
    persisted,
    raw: item,
  } satisfies AssistantStreamEvent;
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
