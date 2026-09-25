import type { MessageDTO } from "@everkano/shared";
import { InfiniteQueryObserver, QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";
import { queryKeys } from "./keys";
import {
  addMessagesToCache,
  CATCH_UP_LIMIT,
  CATCH_UP_LOOKBACK_MS,
  catchUpSince,
  compareMessagesAsc,
  flattenMessagesAsc,
  isMessageCached,
  mergeTimeline,
  MESSAGES_PAGE_SIZE,
  parseMessageRow,
  resyncMessages,
  seedMessagesCache,
  shiftTimestamp,
  timestampToMicros,
  toMessagesPage,
  upsertMessagesInPages,
  type LocalMessage,
  type MessagesCursor,
  type MessagesData,
  type MessagesPage,
} from "./messages";

const CONV = "11111111-1111-4111-8111-111111111111";

function msg(
  id: string,
  createdAt: string,
  sender: "user" | "character" = "character",
  body = `body-${id}`,
): MessageDTO {
  return { id, conversation_id: CONV, sender_type: sender, body, created_at: createdAt };
}

function data(...pages: MessageDTO[][]): MessagesData {
  return {
    pages: pages.map((messages, index) => ({
      messages,
      nextCursor:
        index < pages.length - 1
          ? { createdAt: messages.at(-1)!.created_at, id: messages.at(-1)!.id }
          : null,
    })),
    pageParams: pages.map((_, index) => (index === 0 ? null : { createdAt: "x", id: "x" })),
  };
}

describe("timestampToMicros", () => {
  it("マイクロ秒まで読む（ミリ秒が同じでも順序が付く）", () => {
    const a = timestampToMicros("2026-09-25T03:00:00.123456+00:00");
    const b = timestampToMicros("2026-09-25T03:00:00.123789+00:00");
    expect(b - a).toBe(333);
    expect(Date.parse("2026-09-25T03:00:00.123456+00:00") * 1000).toBe(a - 456);
  });

  it("Postgres 形式・タイムゾーン表記の揺れを吸収する", () => {
    const iso = timestampToMicros("2026-09-25T03:00:00.5Z");
    expect(timestampToMicros("2026-09-25 03:00:00.5+00")).toBe(iso);
    expect(timestampToMicros("2026-09-25T12:00:00.500000+09:00")).toBe(iso);
    expect(timestampToMicros("2026-09-25T12:00:00.500+0900")).toBe(iso);
    expect(timestampToMicros("2026-09-25T03:00:00.500")).toBe(iso); // TZ なし = UTC
  });

  it("解釈できない値は NaN", () => {
    expect(timestampToMicros("not a date")).toBeNaN();
  });
});

describe("compareMessagesAsc", () => {
  it("同一ミリ秒内のユーザー発言 → キャラ返答の順序を保つ", () => {
    const user = msg("b-user", "2026-09-25T03:00:00.100001+00:00", "user");
    const reply = msg("a-char", "2026-09-25T03:00:00.100002+00:00");
    expect([reply, user].sort(compareMessagesAsc).map((m) => m.id)).toEqual(["b-user", "a-char"]);
  });

  it("同時刻なら id で決定的に並べる", () => {
    const x = msg("x", "2026-09-25T03:00:00Z");
    const y = msg("y", "2026-09-25T03:00:00Z");
    expect(compareMessagesAsc(x, y)).toBe(-1);
    expect(compareMessagesAsc(y, x)).toBe(1);
  });
});

describe("parseMessageRow", () => {
  it("Realtime のペイロードを検証して MessageDTO にする", () => {
    const row = { ...msg("m1", "2026-09-25T03:00:00Z", "user"), extra: 1 };
    expect(parseMessageRow(row)).toEqual(msg("m1", "2026-09-25T03:00:00Z", "user"));
  });

  it("形が違えば null", () => {
    expect(parseMessageRow(null)).toBeNull();
    expect(parseMessageRow({ id: "x" })).toBeNull();
    expect(
      parseMessageRow({ ...msg("m", "2026-09-25T03:00:00Z"), sender_type: "admin" }),
    ).toBeNull();
  });
});

describe("flattenMessagesAsc", () => {
  it("新しい順のページ群を古い順の 1 本にし、重複を除く", () => {
    const d = data(
      [msg("m4", "2026-09-25T03:04:00Z"), msg("m3", "2026-09-25T03:03:00Z")],
      [msg("m3", "2026-09-25T03:03:00Z"), msg("m2", "2026-09-25T03:02:00Z")],
      [msg("m1", "2026-09-25T03:01:00Z")],
    );
    expect(flattenMessagesAsc(d).map((m) => m.id)).toEqual(["m1", "m2", "m3", "m4"]);
    expect(flattenMessagesAsc(undefined)).toEqual([]);
  });
});

describe("upsertMessagesInPages", () => {
  const base = data(
    [msg("m3", "2026-09-25T03:03:00Z"), msg("m2", "2026-09-25T03:02:00Z")],
    [msg("m1", "2026-09-25T03:01:00Z")],
  );

  it("新着を先頭ページに降順で差し込む", () => {
    const next = upsertMessagesInPages(base, [msg("m4", "2026-09-25T03:04:00Z")])!;
    expect(next.pages[0]!.messages.map((m) => m.id)).toEqual(["m4", "m3", "m2"]);
    expect(next.pages[1]).toBe(base.pages[1]);
    // 元のデータは変更しない
    expect(base.pages[0]!.messages).toHaveLength(2);
  });

  it("送信応答と Realtime の二重受信・既存ページとの重複を除く", () => {
    const user = msg("u1", "2026-09-25T03:05:00.000001Z", "user");
    const reply = msg("c1", "2026-09-25T03:05:00.000002Z");
    const afterRealtime = upsertMessagesInPages(base, [user])!;
    const afterResponse = upsertMessagesInPages(afterRealtime, [user, reply])!;
    const afterEcho = upsertMessagesInPages(afterResponse, [reply]);
    expect(afterEcho).toBe(afterResponse); // 変化なしなら同じ参照（再描画しない）
    expect(flattenMessagesAsc(afterEcho).map((m) => m.id)).toEqual(["m1", "m2", "m3", "u1", "c1"]);
    expect(upsertMessagesInPages(base, [msg("m1", "2026-09-25T03:01:00Z")])).toBe(base);
  });

  it("到着順が前後しても created_at 順に並ぶ", () => {
    const late = upsertMessagesInPages(base, [
      msg("c2", "2026-09-25T03:06:00.000002Z"),
      msg("u2", "2026-09-25T03:06:00.000001Z", "user"),
    ])!;
    expect(late.pages[0]!.messages.map((m) => m.id)).toEqual(["c2", "u2", "m3", "m2"]);
  });

  it("キャッシュ未作成なら何もしない", () => {
    expect(upsertMessagesInPages(undefined, [msg("x", "2026-09-25T03:00:00Z")])).toBeUndefined();
  });
});

describe("mergeTimeline（楽観的メッセージの合成）", () => {
  const server = [
    msg("m1", "2026-09-25T03:01:00Z", "character", "こんにちは"),
    msg("m2", "2026-09-25T03:02:00Z", "user", "やあ"),
  ];

  function local(
    localId: string,
    body: string,
    afterCreatedAt: string | null,
    status: LocalMessage["status"] = "sending",
  ): LocalMessage {
    return { localId, body, createdAt: "2026-09-25T03:10:00Z", afterCreatedAt, status };
  }

  it("送信中のメッセージを送信時点の最新メッセージの直後に置く", () => {
    const { items, confirmed } = mergeTimeline(server, [
      local("local-1", "元気？", "2026-09-25T03:02:00Z"),
    ]);
    expect(items.map((i) => [i.id, i.status])).toEqual([
      ["m1", "sent"],
      ["m2", "sent"],
      ["local-1", "sending"],
    ]);
    expect(confirmed).toEqual([]);
  });

  it("キャラ返答が HTTP 応答より先に Realtime で届いても、送信中の発言の後に並ぶ", () => {
    const withReply = [
      ...server,
      msg("c9", "2026-09-25T03:03:00.000002Z", "character", "元気だよ"),
    ];
    const { items } = mergeTimeline(withReply, [
      local("local-1", "元気？", "2026-09-25T03:02:00Z"),
    ]);
    expect(items.map((i) => i.id)).toEqual(["m1", "m2", "local-1", "c9"]);
  });

  it("サーバーに同じ本文の発言（エコー）が現れたらローカルを隠し、key を引き継ぐ", () => {
    const echoed = [
      ...server,
      msg("u9", "2026-09-25T03:03:00.000001Z", "user", "元気？"),
      msg("c9", "2026-09-25T03:03:00.000002Z", "character", "元気だよ"),
    ];
    const { items, confirmed } = mergeTimeline(echoed, [
      local("local-1", " 元気？ ", "2026-09-25T03:02:00Z"),
    ]);
    expect(items.map((i) => i.id)).toEqual(["m1", "m2", "u9", "c9"]);
    expect(items.find((i) => i.id === "u9")!.key).toBe("local-1");
    expect(confirmed).toEqual([{ localId: "local-1", messageId: "u9" }]);
  });

  it("送信前からあった同じ本文の発言はエコーとみなさない", () => {
    const { items } = mergeTimeline(server, [local("local-1", "やあ", "2026-09-25T03:02:00Z")]);
    expect(items.map((i) => i.id)).toEqual(["m1", "m2", "local-1"]);
  });

  it("同じ本文を 2 回送った場合、1 つのエコーは 1 つのローカルにしか対応しない", () => {
    const echoed = [...server, msg("u9", "2026-09-25T03:03:00Z", "user", "うん")];
    const { items, confirmed } = mergeTimeline(echoed, [
      local("local-1", "うん", "2026-09-25T03:02:00Z"),
      local("local-2", "うん", "2026-09-25T03:02:00Z"),
    ]);
    expect(confirmed.map((c) => c.localId)).toEqual(["local-1"]);
    expect(items.map((i) => i.id)).toEqual(["m1", "m2", "u9", "local-2"]);
  });

  it("失敗したメッセージは後続のやり取りがあってもその位置に残る", () => {
    const later = [
      ...server,
      msg("u3", "2026-09-25T03:05:00Z", "user", "別の話"),
      msg("c3", "2026-09-25T03:05:01Z", "character", "うんうん"),
    ];
    const { items } = mergeTimeline(later, [
      local("local-1", "失敗した発言", "2026-09-25T03:02:00Z", "failed"),
    ]);
    expect(items.map((i) => [i.id, i.status])).toEqual([
      ["m1", "sent"],
      ["m2", "sent"],
      ["local-1", "failed"],
      ["u3", "sent"],
      ["c3", "sent"],
    ]);
  });

  it("会話が空のときに送った発言（afterCreatedAt = null）は先頭から並ぶ", () => {
    const { items } = mergeTimeline([], [local("local-1", "はじめまして", null)]);
    expect(items.map((i) => i.id)).toEqual(["local-1"]);
  });

  it("keyAliases（送信応答で置き換えたもの）を key に使う", () => {
    const { items } = mergeTimeline(server, [], new Map([["m2", "local-0"]]));
    expect(items.map((i) => i.key)).toEqual(["m1", "local-0"]);
  });
});

// ---------------------------------------------------------------------------
// キャッシュ操作（実際の QueryClient + オブザーバー = 画面にマウントされた useMessages 相当）
// ---------------------------------------------------------------------------

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function page(...messagesDesc: MessageDTO[]): MessagesPage {
  return { messages: messagesDesc, nextCursor: null };
}

/** 呼ばれるたびに次の Deferred を返す queryFn で useMessages 相当のオブザーバーを作る */
function mountMessages(queryClient: QueryClient) {
  const calls: Deferred<MessagesPage>[] = [];
  const observer = new InfiniteQueryObserver<
    MessagesPage,
    Error,
    MessagesData,
    ReturnType<typeof queryKeys.messages>,
    MessagesCursor | null
  >(queryClient, {
    queryKey: queryKeys.messages(CONV),
    queryFn: () => {
      const call = deferred<MessagesPage>();
      calls.push(call);
      return call.promise;
    },
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    retry: false,
  });
  const unsubscribe = observer.subscribe(() => undefined);
  return { calls, unsubscribe, observer };
}

/** 差分取得（fetchSince）の呼び出しを記録し、Deferred で結果を返す */
function fetchSinceStub() {
  const calls: { since: string; limit: number; result: Deferred<MessageDTO[]> }[] = [];
  const fetchSince = (since: string, limit: number) => {
    const result = deferred<MessageDTO[]>();
    calls.push({ since, limit, result });
    return result.promise;
  };
  return { calls, fetchSince };
}

/** 保留中の Promise の後続処理を流す */
async function flush(): Promise<void> {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

function cachedIds(queryClient: QueryClient): string[] | undefined {
  const data = queryClient.getQueryData<MessagesData>(queryKeys.messages(CONV));
  return data ? flattenMessagesAsc(data).map((m) => m.id) : undefined;
}

describe("addMessagesToCache / resyncMessages", () => {
  const greeting = msg("g1", "2026-09-25T03:00:00Z", "character", "はじめまして");
  const sent = msg("u1", "2026-09-25T03:05:00.000001Z", "user", "読み込み中に送信");
  const reply = msg("c1", "2026-09-25T03:05:00.000002Z", "character", "届いたよ");
  let queryClient: QueryClient;
  let unmount: () => void = () => undefined;

  afterEach(() => {
    unmount();
    queryClient.clear();
  });

  it("キャッシュがあれば先頭ページへ差し込み true", async () => {
    queryClient = new QueryClient();
    const { calls, unsubscribe } = mountMessages(queryClient);
    unmount = unsubscribe;
    calls[0]!.resolve(page(greeting));
    await flush();

    expect(addMessagesToCache(queryClient, CONV, [sent, reply])).toBe(true);
    expect(cachedIds(queryClient)).toEqual(["g1", "u1", "c1"]);
    expect(calls).toHaveLength(1); // 再取得しない
  });

  it("初回取得中（保存前のスナップショット）でも、取得完了後に差し込まれて消えない", async () => {
    queryClient = new QueryClient();
    const { calls, unsubscribe } = mountMessages(queryClient);
    unmount = unsubscribe;
    expect(calls).toHaveLength(1); // 初回取得が進行中（/chat の保存より前に読んだ）

    expect(addMessagesToCache(queryClient, CONV, [sent, reply])).toBe(false);
    calls[0]!.resolve(page(greeting)); // 保存前のスナップショット
    await flush();

    expect(cachedIds(queryClient)).toEqual(["g1", "u1", "c1"]);
  });

  it("初回取得の失敗後に届いたメッセージは、取り直した結果に含まれる", async () => {
    queryClient = new QueryClient();
    const { calls, unsubscribe } = mountMessages(queryClient);
    unmount = unsubscribe;
    calls[0]!.reject(new Error("network"));
    await flush();
    expect(cachedIds(queryClient)).toBeUndefined();

    expect(addMessagesToCache(queryClient, CONV, [reply])).toBe(false);
    expect(calls).toHaveLength(2); // 取り直しを始める
    calls[1]!.resolve(page(reply, sent, greeting));
    await flush();
    expect(cachedIds(queryClient)).toEqual(["g1", "u1", "c1"]);
  });

  it("購読開始が初回取得の途中でも、終わってから差分を取って取りこぼしを回収する（ページは取り直さない）", async () => {
    queryClient = new QueryClient();
    const { calls, unsubscribe } = mountMessages(queryClient);
    unmount = unsubscribe;
    const since = fetchSinceStub();

    // SUBSCRIBED（初回取得はまだ進行中）
    const resync = resyncMessages(queryClient, CONV, { fetchSince: since.fetchSince });
    calls[0]!.resolve(page(greeting)); // 購読開始より前のスナップショット
    await flush();
    expect(since.calls).toHaveLength(1); // 合流で終わらせず、差分を取りに行く
    expect(since.calls[0]!.since).toBe(shiftTimestamp(greeting.created_at, -CATCH_UP_LOOKBACK_MS));
    expect(since.calls[0]!.limit).toBe(CATCH_UP_LIMIT);
    since.calls[0]!.result.resolve([greeting, sent]); // その間に保存された発言（遡った分は重複排除）
    await resync;
    expect(cachedIds(queryClient)).toEqual(["g1", "u1"]);
    expect(calls, "読み込み済みのページは取り直さない").toHaveLength(1);
  });

  it("取りこぼしが無ければ何も変えない（同じ参照）", async () => {
    queryClient = new QueryClient();
    const { calls, unsubscribe } = mountMessages(queryClient);
    unmount = unsubscribe;
    calls[0]!.resolve(page(greeting));
    await flush();
    const before = queryClient.getQueryData(queryKeys.messages(CONV));

    await resyncMessages(queryClient, CONV, { fetchSince: async () => [greeting] });
    expect(queryClient.getQueryData(queryKeys.messages(CONV))).toBe(before);
    expect(calls).toHaveLength(1);
  });

  it("取りこぼしが上限以上なら、遡って読んだページを捨てて最新の 1 ページだけを取り直す", async () => {
    queryClient = new QueryClient();
    const { calls, unsubscribe, observer } = mountMessages(queryClient);
    unmount = unsubscribe;
    const older = Array.from({ length: MESSAGES_PAGE_SIZE }, (_, i) =>
      msg(`o${i}`, `2026-09-25T02:${String(59 - i).padStart(2, "0")}:00Z`),
    );
    calls[0]!.resolve({
      messages: [greeting],
      nextCursor: { createdAt: greeting.created_at, id: greeting.id },
    });
    await flush();
    const next = observer.fetchNextPage();
    calls[1]!.resolve({ messages: older, nextCursor: null });
    await next;
    expect(queryClient.getQueryData<MessagesData>(queryKeys.messages(CONV))?.pages).toHaveLength(2);

    const flood = Array.from({ length: CATCH_UP_LIMIT }, (_, i) =>
      msg(`n${i}`, `2026-09-25T04:00:00.${String(i).padStart(6, "0")}Z`),
    );
    const resync = resyncMessages(queryClient, CONV, { fetchSince: async () => flood });
    await flush();
    expect(calls, "1 ページ目だけを取り直す（N ページの順次再取得にしない）").toHaveLength(3);
    calls[2]!.resolve({ messages: flood.slice(-MESSAGES_PAGE_SIZE).reverse(), nextCursor: null });
    await resync;
    const pages = queryClient.getQueryData<MessagesData>(queryKeys.messages(CONV))?.pages;
    expect(pages).toHaveLength(1);
    expect(calls).toHaveLength(3);
  });

  it("中断されたら差し込まない", async () => {
    queryClient = new QueryClient();
    const { calls, unsubscribe } = mountMessages(queryClient);
    unmount = unsubscribe;
    calls[0]!.resolve(page(greeting));
    await flush();
    const controller = new AbortController();
    const since = fetchSinceStub();
    const resync = resyncMessages(queryClient, CONV, {
      fetchSince: since.fetchSince,
      signal: controller.signal,
    });
    await flush();
    controller.abort();
    since.calls[0]!.result.resolve([sent]);
    await resync;
    expect(cachedIds(queryClient)).toEqual(["g1"]);
  });

  it("過去ログの読み込み中に届いたメッセージも、読み込み完了で上書きされずに残る", async () => {
    queryClient = new QueryClient();
    const { calls, unsubscribe, observer } = mountMessages(queryClient);
    unmount = unsubscribe;
    calls[0]!.resolve({
      messages: [greeting],
      nextCursor: { createdAt: greeting.created_at, id: greeting.id },
    });
    await flush();

    const next = observer.fetchNextPage(); // 取得開始時点のページ（新着なし）+ 次のページで上書きされる
    expect(addMessagesToCache(queryClient, CONV, [sent, reply])).toBe(true);
    expect(isMessageCached(queryClient, CONV, sent.id)).toBe(true);
    calls[1]!.resolve({ messages: [msg("o1", "2026-09-25T02:00:00Z")], nextCursor: null });
    await next;
    await flush();
    expect(cachedIds(queryClient)).toEqual(["o1", "g1", "u1", "c1"]);
  });
});

describe("toMessagesPage / seedMessagesCache / catchUpSince", () => {
  it("ちょうど 1 ページ分あれば最古の行を次のカーソルにする", () => {
    const rows = Array.from({ length: MESSAGES_PAGE_SIZE }, (_, i) =>
      msg(`m${i}`, `2026-09-25T03:${String(59 - i).padStart(2, "0")}:00Z`),
    );
    expect(toMessagesPage(rows).nextCursor).toEqual({
      createdAt: rows.at(-1)!.created_at,
      id: rows.at(-1)!.id,
    });
    expect(toMessagesPage(rows.slice(0, 3)).nextCursor).toBeNull();
    expect(toMessagesPage([{ ...rows[0]!, sender_type: "unknown" }]).messages[0]?.sender_type).toBe(
      "character",
    );
  });

  it("キャッシュが無いときだけ埋める（追従済みの内容を古いスナップショットで戻さない）", () => {
    const queryClient = new QueryClient();
    const greeting = msg("g1", "2026-09-25T03:00:00Z");
    const later = msg("c2", "2026-09-25T03:10:00Z");
    seedMessagesCache(queryClient, CONV, toMessagesPage([greeting]));
    addMessagesToCache(queryClient, CONV, [later]);
    seedMessagesCache(queryClient, CONV, toMessagesPage([greeting]));
    expect(
      flattenMessagesAsc(queryClient.getQueryData<MessagesData>(queryKeys.messages(CONV))).map(
        (m) => m.id,
      ),
    ).toEqual(["g1", "c2"]);
    queryClient.clear();
  });

  it("差分の起点は最新メッセージの少し前（空なら先頭から）", () => {
    expect(shiftTimestamp("2026-09-25T03:00:05.123456+00:00", -5_000)).toBe(
      "2026-09-25T03:00:00.123Z",
    );
    expect(shiftTimestamp("not a date", -5_000)).toBeNull();
    expect(
      catchUpSince(data([msg("b", "2026-09-25T03:00:10Z"), msg("a", "2026-09-25T03:00:00Z")])),
    ).toBe("2026-09-25T03:00:05.000Z");
    expect(catchUpSince(undefined)).toBe("1970-01-01T00:00:00.000Z");
  });
});
