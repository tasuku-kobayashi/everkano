import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatResponse, CreateCommentResponse, MemoryDTO } from "@everkano/shared";
import { ApiError, CHAT_TIMEOUT_MS, createApiClient, DEFAULT_TIMEOUT_MS } from "./client";
import { API_ERROR_MESSAGES, parseRetryAfter } from "./errors";

type FetchArgs = [input: RequestInfo | URL, init?: RequestInit];

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function setup(
  impl: (...args: FetchArgs) => Promise<Response>,
  token: string | null = "test-token",
) {
  const fetchMock = vi.fn(impl);
  const client = createApiClient({
    baseUrl: "http://api.test/",
    getAccessToken: async () => token,
    fetch: fetchMock as unknown as typeof fetch,
    generateRequestId: () => "req-123",
  });
  return { client, fetchMock };
}

function lastCall(fetchMock: ReturnType<typeof vi.fn>) {
  const call = fetchMock.mock.calls.at(-1) as FetchArgs;
  const init = call[1] ?? {};
  return {
    url: String(call[0]),
    init,
    headers: (init.headers ?? {}) as Record<string, string>,
  };
}

const memory: MemoryDTO = {
  id: "m1",
  character_id: "c1",
  content: "ユーザーは来週大阪に出張する",
  importance: 0.8,
  tags: [],
  is_user_edited: true,
  source_message_id: null,
  created_at: "2026-09-25T00:00:00Z",
  updated_at: "2026-09-25T00:00:00Z",
};

afterEach(() => {
  vi.useRealTimers();
});

describe("createApiClient: 正常系", () => {
  it("health は認証ヘッダなしで GET /health を呼ぶ", async () => {
    const { client, fetchMock } = setup(async () =>
      jsonResponse(200, {
        status: "ok",
        version: "0.1.0",
        env: "local",
        llm_mode: "mock",
        embedding_mode: "hash",
        db: "ok",
      }),
    );
    const res = await client.health();
    expect(res.status).toBe("ok");
    const { url, init, headers } = lastCall(fetchMock);
    expect(url).toBe("http://api.test/health");
    expect(init.method).toBe("GET");
    expect(headers.Authorization).toBeUndefined();
    expect(headers["X-Request-ID"]).toBe("req-123");
  });

  it("sendChat は Bearer トークン付きで JSON を POST する", async () => {
    const chat: ChatResponse = {
      message_id: "msg-2",
      reply: "おかえり！",
      memories_used: [],
      memories_created: [],
      user_message: {
        id: "msg-1",
        conversation_id: "conv-1",
        sender_type: "user",
        body: "ただいま",
        created_at: "2026-09-25T00:00:00Z",
      },
      character_message: {
        id: "msg-2",
        conversation_id: "conv-1",
        sender_type: "character",
        body: "おかえり！",
        created_at: "2026-09-25T00:00:01Z",
      },
      moderated: false,
    };
    const { client, fetchMock } = setup(async () => jsonResponse(200, chat));
    const res = await client.sendChat({
      character_id: "c1",
      conversation_id: "conv-1",
      message: "ただいま",
    });
    expect(res.reply).toBe("おかえり！");
    const { url, init, headers } = lastCall(fetchMock);
    expect(url).toBe("http://api.test/chat");
    expect(init.method).toBe("POST");
    expect(headers.Authorization).toBe("Bearer test-token");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({
      character_id: "c1",
      conversation_id: "conv-1",
      message: "ただいま",
    });
  });

  it("createConversation / createComment / generateCommentReply のパス", async () => {
    const commentRes: CreateCommentResponse = {
      comment: {
        id: "cm1",
        post_id: "p1",
        parent_comment_id: null,
        author_type: "user",
        author_user_id: "u1",
        author_character_id: null,
        body: "かわいい",
        created_at: "2026-09-25T00:00:00Z",
      },
      reply_scheduled: true,
    };
    const { client, fetchMock } = setup(async (input) => {
      const url = String(input);
      if (url.endsWith("/conversations")) {
        return jsonResponse(200, { conversation: {}, created: true, greeting_message: null });
      }
      if (url.endsWith("/comments/generate")) return jsonResponse(200, { comment: null });
      return jsonResponse(201, commentRes);
    });

    await client.createConversation({ character_id: "c1" });
    expect(lastCall(fetchMock).url).toBe("http://api.test/conversations");

    const created = await client.createComment({ post_id: "p1", body: "かわいい" });
    expect(created.reply_scheduled).toBe(true);
    expect(lastCall(fetchMock).url).toBe("http://api.test/comments");

    const generated = await client.generateCommentReply({
      post_id: "p1",
      parent_comment_id: "cm1",
    });
    expect(generated.comment).toBeNull();
    expect(lastCall(fetchMock).url).toBe("http://api.test/comments/generate");
  });

  it("メモリの CRUD（クエリ・PATCH・204）", async () => {
    const { client, fetchMock } = setup(async (_input, init) => {
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      if (init?.method === "GET") return jsonResponse(200, { memories: [memory] });
      return jsonResponse(init?.method === "POST" ? 201 : 200, memory);
    });

    const list = await client.listMemories("c 1");
    expect(list.memories).toHaveLength(1);
    expect(lastCall(fetchMock).url).toBe("http://api.test/memories?character_id=c+1");

    await client.createMemory({ character_id: "c1", content: "秘密", tags: ["secret"] });
    expect(lastCall(fetchMock).init.method).toBe("POST");

    await client.updateMemory("m/1", { importance: 0.9 });
    expect(lastCall(fetchMock).url).toBe("http://api.test/memories/m%2F1");
    expect(lastCall(fetchMock).init.method).toBe("PATCH");

    await expect(client.deleteMemory("m1")).resolves.toBeUndefined();
    expect(lastCall(fetchMock).init.method).toBe("DELETE");
  });
});

describe("createApiClient: エラー処理", () => {
  it("トークンが無い場合は fetch せず 401 unauthorized", async () => {
    const { client, fetchMock } = setup(async () => jsonResponse(200, {}), null);
    const error = await client.listMemories("c1").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect((error as ApiError).code).toBe("unauthorized");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("ApiErrorBody のメッセージとコードをそのまま使う（422 moderation_blocked）", async () => {
    const { client } = setup(async () =>
      jsonResponse(422, {
        error: {
          code: "moderation_blocked",
          message: "この内容は投稿できません",
          request_id: "r-9",
        },
      }),
    );
    const error = (await client
      .createComment({ post_id: "p1", body: "x" })
      .catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(422);
    expect(error.code).toBe("moderation_blocked");
    expect(error.message).toBe("この内容は投稿できません");
    expect(error.requestId).toBe("r-9");
    expect(error.isClientError).toBe(true);
  });

  it("429 は rate_limited と Retry-After 秒を返す", async () => {
    const { client } = setup(async () =>
      jsonResponse(
        429,
        { error: { code: "rate_limited", message: "少し時間をおいてから送信してください" } },
        { "Retry-After": "30" },
      ),
    );
    const error = (await client
      .sendChat({ character_id: "c", conversation_id: "v", message: "m" })
      .catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(429);
    expect(error.code).toBe("rate_limited");
    expect(error.retryAfterSeconds).toBe(30);
    expect(error.isClientError).toBe(false);
  });

  it("本文が JSON でない 429 / 5xx は既定の日本語メッセージ", async () => {
    const statuses: Array<[number, keyof typeof API_ERROR_MESSAGES]> = [
      [429, "rate_limited"],
      [500, "internal_error"],
      [502, "llm_unavailable"],
      [503, "llm_unavailable"],
    ];
    for (const [status, code] of statuses) {
      const { client } = setup(async () => new Response("<html>Bad Gateway</html>", { status }));
      const error = (await client.health().catch((e: unknown) => e)) as ApiError;
      expect(error.status).toBe(status);
      expect(error.code).toBe(code);
      expect(error.message).toBe(API_ERROR_MESSAGES[code]);
    }
  });

  it("未知のエラーコードはステータスから推定する", async () => {
    const { client } = setup(async () =>
      jsonResponse(403, { error: { code: "account_deleted", message: "退会済みです" } }),
    );
    const error = (await client.listMemories("c").catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("account_deleted");

    const { client: client2 } = setup(async () =>
      jsonResponse(404, { error: { code: "weird_code", message: "" } }),
    );
    const error2 = (await client2.listMemories("c").catch((e: unknown) => e)) as ApiError;
    expect(error2.code).toBe("not_found");
    expect(error2.message).toBe(API_ERROR_MESSAGES.not_found);
  });

  it("通信失敗は network_error（日本語メッセージ）", async () => {
    const { client } = setup(async () => {
      throw new TypeError("Failed to fetch");
    });
    const error = (await client.health().catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(0);
    expect(error.code).toBe("network_error");
    expect(error.message).toBe("通信できませんでした。電波の良い場所で再度お試しください");
  });

  it("2xx だが本文が JSON でない場合は internal_error", async () => {
    const { client } = setup(async () => new Response("ok", { status: 200 }));
    const error = (await client.health().catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("internal_error");
  });
});

describe("createApiClient: タイムアウトと中断", () => {
  /** signal が abort されるまで解決しない fetch */
  function hangingFetch(...[, init]: FetchArgs): Promise<Response> {
    return new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => {
        reject(new DOMException("The operation was aborted.", "AbortError"));
      });
    });
  }

  it("既定は 15 秒でタイムアウト", async () => {
    vi.useFakeTimers();
    const { client } = setup(hangingFetch);
    const promise = client.listMemories("c1").catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS - 1);
    await vi.advanceTimersByTimeAsync(1);
    const error = (await promise) as ApiError;
    expect(error.code).toBe("timeout");
    expect(error.message).toBe(API_ERROR_MESSAGES.timeout);
  });

  it("sendChat は 45 秒まで待つ", async () => {
    vi.useFakeTimers();
    const { client } = setup(hangingFetch);
    let settled = false;
    const promise = client
      .sendChat({ character_id: "c", conversation_id: "v", message: "m" })
      .catch((e: unknown) => e)
      .finally(() => {
        settled = true;
      });
    await vi.advanceTimersByTimeAsync(DEFAULT_TIMEOUT_MS + 1000);
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(CHAT_TIMEOUT_MS);
    const error = (await promise) as ApiError;
    expect(error.code).toBe("timeout");
  });

  it("呼び出し側の signal で中断すると aborted", async () => {
    const { client } = setup(hangingFetch);
    const controller = new AbortController();
    const promise = client.health({ signal: controller.signal }).catch((e: unknown) => e);
    controller.abort();
    const error = (await promise) as ApiError;
    expect(error.code).toBe("aborted");
  });

  it("中断済み signal なら fetch しない", async () => {
    const { client, fetchMock } = setup(hangingFetch);
    const controller = new AbortController();
    controller.abort();
    const error = (await client
      .health({ signal: controller.signal })
      .catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("aborted");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("parseRetryAfter", () => {
  it("秒数と HTTP-date を解釈する", () => {
    expect(parseRetryAfter("12")).toBe(12);
    const now = Date.parse("2026-09-25T00:00:00Z");
    expect(parseRetryAfter("Fri, 25 Sep 2026 00:00:30 GMT", now)).toBe(30);
    expect(parseRetryAfter(null)).toBeUndefined();
    expect(parseRetryAfter("abc")).toBeUndefined();
  });
});
