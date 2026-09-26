import type { ChatResponse, ChatStreamEvent } from "@everkano/shared";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, CHAT_STREAM_TIMEOUT_MS, createApiClient } from "./client";
import { API_ERROR_MESSAGES } from "./errors";

/**
 * POST /chat/stream（api.streamChat）の単体テスト。fetch をモックし、SSE の本文を任意の位置で分割して流す。
 */

type FetchArgs = [input: RequestInfo | URL, init?: RequestInit];

const BODY = { character_id: "c1", conversation_id: "v1", message: "ただいま" };

const response: ChatResponse = {
  message_id: "m2",
  reply: "おかえり！今日はどうだった？",
  memories_used: [],
  memories_created: [],
  user_message: {
    id: "m1",
    conversation_id: "v1",
    sender_type: "user",
    body: "ただいま",
    created_at: "2026-09-26T09:00:00.000001Z",
    is_proactive: false,
    safety_triggered: false,
  },
  character_message: {
    id: "m2",
    conversation_id: "v1",
    sender_type: "character",
    body: "おかえり！今日はどうだった？",
    created_at: "2026-09-26T09:00:00.000002Z",
    is_proactive: false,
    safety_triggered: false,
  },
  moderated: false,
  safety: null,
};

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** チャンク（文字列）を順に流す SSE 応答。bytes を分割するため UTF-8 の途中でも切る */
function streamResponse(
  chunks: readonly string[],
  init: { splitBytes?: boolean; status?: number; contentType?: string; hang?: boolean } = {},
): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        const bytes = encoder.encode(chunk);
        if (init.splitBytes) {
          // 1 バイトずつ（マルチバイト文字の途中で分割される）
          for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
        } else {
          controller.enqueue(bytes);
        }
      }
      if (!init.hang) controller.close();
    },
  });
  return new Response(body, {
    status: init.status ?? 200,
    headers: { "Content-Type": init.contentType ?? "text/event-stream; charset=utf-8" },
  });
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function setup(
  impl: (...args: FetchArgs) => Promise<Response>,
  options: { supportsStreaming?: boolean } = {},
) {
  const fetchMock = vi.fn(impl);
  const client = createApiClient({
    baseUrl: "http://api.test",
    getAccessToken: async () => "tok",
    fetch: fetchMock as unknown as typeof fetch,
    generateRequestId: () => "req-1",
    supportsStreaming: () => options.supportsStreaming ?? true,
  });
  const events: ChatStreamEvent[] = [];
  const run = () =>
    client.streamChat(BODY, { onEvent: (event) => events.push(event) }).catch((e: unknown) => e);
  return { client, fetchMock, events, run };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("streamChat: 正常系", () => {
  it("Bearer トークン付きで POST /chat/stream し、delta → done を順に通知して結果を返す", async () => {
    const { fetchMock, events, run } = setup(async () =>
      streamResponse(
        [
          ": connected\n\n",
          sse("delta", { text: "おかえり！" }),
          sse("delta", { text: "今日はどうだった？" }),
          sse("done", response),
        ],
        { splitBytes: true },
      ),
    );
    const result = await run();
    expect(result).toEqual(response);
    expect(events).toEqual([
      { type: "delta", data: { text: "おかえり！" } },
      { type: "delta", data: { text: "今日はどうだった？" } },
      { type: "done", data: response },
    ]);
    const [url, init] = fetchMock.mock.calls[0] as FetchArgs;
    expect(String(url)).toBe("http://api.test/chat/stream");
    expect(init?.method).toBe("POST");
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok");
    expect(headers.Accept).toBe("text/event-stream");
    expect(JSON.parse(String(init?.body))).toEqual(BODY);
  });

  it("replace を通知する（出力検査で差し止めた吹き出しの置き換え）", async () => {
    const { events, run } = setup(async () =>
      streamResponse([
        sse("delta", { text: "えっと" }),
        sse("replace", { text: "その話はやめておこうかな", reason: "moderated" }),
        sse("done", { ...response, moderated: true }),
      ]),
    );
    const result = (await run()) as ChatResponse;
    expect(result.moderated).toBe(true);
    expect(events.map((e) => e.type)).toEqual(["delta", "replace", "done"]);
  });

  it("done を受け取ったら残りを読まずに返す（接続が閉じなくても待たない）", async () => {
    const { run } = setup(async () =>
      streamResponse([sse("delta", { text: "a" }), sse("done", response)], { hang: true }),
    );
    await expect(run()).resolves.toEqual(response);
  });

  it("Content-Type が JSON なら ChatResponse として受け取り、done だけを通知する", async () => {
    const { events, run } = setup(async () => jsonResponse(200, response));
    await expect(run()).resolves.toEqual(response);
    expect(events).toEqual([{ type: "done", data: response }]);
  });
});

describe("streamChat: エラー", () => {
  it("ストリーム開始前の HTTP エラー（429）は ApiError（Retry-After 付き）", async () => {
    const { fetchMock, run } = setup(
      async () =>
        new Response(
          JSON.stringify({ error: { code: "rate_limited", message: "送信が多すぎます。" } }),
          { status: 429, headers: { "Content-Type": "application/json", "Retry-After": "12" } },
        ),
    );
    const error = (await run()) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, code: "rate_limited", retryAfterSeconds: 12 });
    expect(error.message).toBe("送信が多すぎます。");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("401 は unauthorized（グローバルな処理でログイン画面へ）", async () => {
    const { run } = setup(async () =>
      jsonResponse(401, { error: { code: "unauthorized", message: "期限切れ" } }),
    );
    await expect(run()).resolves.toMatchObject({ status: 401, code: "unauthorized" });
  });

  it("error イベントはコードに対応するステータスの ApiError（通知もする）", async () => {
    const { events, run } = setup(async () =>
      streamResponse([
        sse("delta", { text: "えっと" }),
        sse("error", { code: "llm_unavailable", message: "ただいま返信できません。" }),
      ]),
    );
    const error = (await run()) as ApiError;
    expect(error).toMatchObject({
      status: 503,
      code: "llm_unavailable",
      message: "ただいま返信できません。",
      requestId: "req-1",
    });
    expect(events.map((e) => e.type)).toEqual(["delta", "error"]);
  });

  it("error イベントの未知のコード・空のメッセージは internal_error の既定文言", async () => {
    const { run } = setup(async () =>
      streamResponse([sse("error", { code: "weird", message: "" })]),
    );
    await expect(run()).resolves.toMatchObject({
      status: 500,
      code: "internal_error",
      message: API_ERROR_MESSAGES.internal_error,
    });
  });

  it("done の前に接続が閉じたら network_error", async () => {
    const { run } = setup(async () => streamResponse([sse("delta", { text: "おか" })]));
    await expect(run()).resolves.toMatchObject({ status: 0, code: "network_error" });
  });

  it("通信失敗は network_error", async () => {
    const { run } = setup(async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(run()).resolves.toMatchObject({
      code: "network_error",
      message: API_ERROR_MESSAGES.network_error,
    });
  });

  it("本文の読み取り中の通信失敗も network_error", async () => {
    const { run } = setup(async () => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(sse("delta", { text: "a" })));
          controller.error(new TypeError("network error"));
        },
      });
      return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
    });
    await expect(run()).resolves.toMatchObject({ code: "network_error" });
  });

  it("接続から done まで 45 秒でタイムアウト（本文の読み取り中も）", async () => {
    vi.useFakeTimers();
    const { run } = setup(
      async (_input, init) =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(new TextEncoder().encode(sse("delta", { text: "a" })));
              init?.signal?.addEventListener("abort", () =>
                controller.error(new DOMException("aborted", "AbortError")),
              );
            },
          }),
          { headers: { "Content-Type": "text/event-stream" } },
        ),
    );
    let settled = false;
    const promise = run().finally(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(CHAT_STREAM_TIMEOUT_MS - 1);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await expect(promise).resolves.toMatchObject({
      code: "timeout",
      message: API_ERROR_MESSAGES.timeout,
    });
  });
});

describe("streamChat: フォールバック（/chat）", () => {
  it("ストリーミングを使えない環境では /chat を呼び、done だけを通知する", async () => {
    const { fetchMock, events, run } = setup(async () => jsonResponse(200, response), {
      supportsStreaming: false,
    });
    await expect(run()).resolves.toEqual(response);
    expect(String((fetchMock.mock.calls[0] as FetchArgs)[0])).toBe("http://api.test/chat");
    expect(events).toEqual([{ type: "done", data: response }]);
  });

  it("/chat/stream が無い API（404）なら /chat で送り、以降は /chat を直接使う", async () => {
    const { client, fetchMock } = setup(async (input) =>
      String(input).endsWith("/chat/stream")
        ? jsonResponse(404, { error: { code: "not_found", message: "見つかりませんでした。" } })
        : jsonResponse(200, response),
    );
    await expect(client.streamChat(BODY)).resolves.toEqual(response);
    await expect(client.streamChat(BODY)).resolves.toEqual(response);
    expect(fetchMock.mock.calls.map((call) => String((call as FetchArgs)[0]))).toEqual([
      "http://api.test/chat/stream",
      "http://api.test/chat",
      "http://api.test/chat",
    ]);
  });

  it("会話が見つからない 404 は /chat でも 404 になり、そのエラーを返す（以降もストリームを使う）", async () => {
    const notFound = { error: { code: "not_found", message: "会話が見つかりません。" } };
    const { client, fetchMock } = setup(async () => jsonResponse(404, notFound));
    const error = (await client.streamChat(BODY).catch((e: unknown) => e)) as ApiError;
    expect(error).toMatchObject({
      status: 404,
      code: "not_found",
      message: "会話が見つかりません。",
    });
    await client.streamChat(BODY).catch(() => undefined);
    expect(String((fetchMock.mock.calls[2] as FetchArgs)[0])).toBe("http://api.test/chat/stream");
  });
});
