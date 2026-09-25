/**
 * DM メッセージのデータ層（C-2）。
 *
 * - 読み取りは supabase-js で messages を直接参照（RLS: 自分の会話のみ）。created_at 降順・30 件ずつ
 *   useInfiniteQuery で過去に遡る。キャッシュの各ページは「新しい順」。
 * - 新着は Realtime（postgres_changes INSERT, filter conversation_id=eq.<id>）でキャッシュの先頭ページへ差し込む。
 * - 取りこぼしの回収（購読開始・再接続・画面復帰・オンライン復帰）は「キャッシュの最新より新しいメッセージ」だけを
 *   取りに行く差分取得（resyncMessages）。無限クエリの再取得は読み込み済みの全ページを 1 ページずつ順に取り直すため
 *   （過去ログを遡った後だと N 往復）、invalidate / フォーカス時の再取得は使わない。
 * - 送信中 / 送信失敗の「ローカルメッセージ」はキャッシュに入れず、呼び出し側の state で持つ
 *   （再取得でキャッシュが置き換わっても消えないようにするため）。表示時に mergeTimeline() で合成する。
 *
 * 純粋関数（ページのマージ・重複排除・楽観的メッセージの合成・時刻比較）はこのファイルの前半にまとめ、
 * messages.test.ts で単体テストしている。
 */

import type { MessageDTO, SenderType } from "@everkano/shared";
import {
  infiniteQueryOptions,
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { toAppError } from "@/lib/api/errors";
import { quotePostgrestValue } from "@/lib/queries/feed";
import { queryKeys } from "@/lib/queries/keys";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import type { TypedSupabaseClient } from "@/lib/supabase/types";

/** 1 ページの件数（仕様: 30 件ずつ遡る） */
export const MESSAGES_PAGE_SIZE = 30;

/** messages の select 列 */
export const MESSAGE_COLUMNS = "id, conversation_id, sender_type, body, created_at" as const;

/**
 * 差分取得（resyncMessages）で一度に取る最大件数。これ以上取りこぼしていたら差分では埋めず、
 * 最新の 1 ページを取り直す（遡って読み込んだ古いページは捨てる）。
 */
export const CATCH_UP_LIMIT = 100;

/**
 * 差分取得の起点を「キャッシュの最新メッセージ」より少し前にずらす幅。
 * created_at は INSERT 時刻（clock_timestamp()）でコミット時刻ではないため、ほぼ同時に走った 2 つの
 * トランザクションでは「後からコミットされた方の created_at が既に見えている行より古い」ことがあり得る。
 * 少し遡って取り、id で重複排除する（API の保存トランザクションはミリ秒単位なので 5 秒で十分）。
 */
export const CATCH_UP_LOOKBACK_MS = 5_000;

/**
 * 前回の表示で読み込んだキャッシュがこれより古ければ、画面を開いた時点で（購読の完了を待たずに）差分を取る。
 * Realtime に接続できない環境でも、開き直せば最新になるようにするため（以前の staleTime 30 秒と同じ）。
 */
export const MESSAGES_CATCH_UP_ON_MOUNT_MS = 30_000;

/** 過去ログのカーソル（このメッセージより古いものを取得する） */
export interface MessagesCursor {
  createdAt: string;
  id: string;
}

export interface MessagesPage {
  /** 新しい順（created_at 降順） */
  messages: MessageDTO[];
  /** 次（より古い）ページのカーソル。null = これ以上無い */
  nextCursor: MessagesCursor | null;
}

export type MessagesData = InfiniteData<MessagesPage, MessagesCursor | null>;

// ---------------------------------------------------------------------------
// 時刻（マイクロ秒精度）
// ---------------------------------------------------------------------------

const TIMESTAMP_RE =
  /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:\.(\d+))?(Z|[+-]\d{2}(?::?\d{2})?)?$/i;

/**
 * ISO 8601 / Postgres 形式のタイムスタンプを「エポックからのマイクロ秒」に変換する。
 *
 * ユーザー発言とキャラ返答は 1 トランザクションで clock_timestamp() により保存されるため、
 * 両者の差が 1 ミリ秒未満になることがある。Date.parse()（ミリ秒精度）では順序が崩れるので、
 * 小数部をマイクロ秒まで読んで比較する。解釈できない値は NaN。
 */
export function timestampToMicros(value: string): number {
  const match = TIMESTAMP_RE.exec(value.trim());
  if (!match) {
    const ms = Date.parse(value);
    return Number.isNaN(ms) ? Number.NaN : ms * 1000;
  }
  const [, date, time, fraction = "", rawZone] = match;
  let zone = rawZone ?? "Z";
  if (/^[+-]\d{2}$/.test(zone)) zone = `${zone}:00`;
  else if (/^[+-]\d{4}$/.test(zone)) zone = `${zone.slice(0, 3)}:${zone.slice(3)}`;
  const seconds = time!.length === 5 ? `${time}:00` : time;
  const ms = Date.parse(`${date}T${seconds}${zone.toUpperCase()}`);
  if (Number.isNaN(ms)) return Number.NaN;
  const micros = Number((fraction + "000000").slice(0, 6));
  return ms * 1000 + micros;
}

/** 古い順の比較（created_at → id）。 */
export function compareMessagesAsc(
  a: Pick<MessageDTO, "created_at" | "id">,
  b: Pick<MessageDTO, "created_at" | "id">,
): number {
  const diff = timestampToMicros(a.created_at) - timestampToMicros(b.created_at);
  if (diff !== 0 && !Number.isNaN(diff)) return diff < 0 ? -1 : 1;
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
}

// ---------------------------------------------------------------------------
// 行の検証（DB / Realtime のペイロード → MessageDTO）
// ---------------------------------------------------------------------------

export function toSenderType(value: unknown): SenderType {
  return value === "user" ? "user" : "character";
}

/** Realtime のペイロード等（unknown）を MessageDTO に変換する。形が合わなければ null。 */
export function parseMessageRow(row: unknown): MessageDTO | null {
  if (!row || typeof row !== "object") return null;
  const r = row as Record<string, unknown>;
  if (
    typeof r.id !== "string" ||
    typeof r.conversation_id !== "string" ||
    typeof r.body !== "string" ||
    typeof r.created_at !== "string" ||
    (r.sender_type !== "user" && r.sender_type !== "character")
  ) {
    return null;
  }
  return {
    id: r.id,
    conversation_id: r.conversation_id,
    sender_type: r.sender_type,
    body: r.body,
    created_at: r.created_at,
  };
}

// ---------------------------------------------------------------------------
// キャッシュ（InfiniteData）操作 — すべて純粋関数。変更が無ければ同じ参照を返す
// ---------------------------------------------------------------------------

/** 全ページのメッセージを古い順に並べ、id で重複排除する */
export function flattenMessagesAsc(data: MessagesData | undefined): MessageDTO[] {
  if (!data) return [];
  const byId = new Map<string, MessageDTO>();
  for (const page of data.pages) {
    for (const message of page.messages) {
      if (!byId.has(message.id)) byId.set(message.id, message);
    }
  }
  return Array.from(byId.values()).sort(compareMessagesAsc);
}

/**
 * メッセージをキャッシュへ追加する（Realtime 受信・送信 API の応答）。
 * - 既にどこかのページにある id は無視（重複排除）
 * - 新しいものは先頭ページ（最新）に created_at 降順を保って挿入
 * - キャッシュが未作成（undefined）なら何もしない（初回取得に任せる）
 */
export function upsertMessagesInPages(
  data: MessagesData | undefined,
  incoming: readonly MessageDTO[],
): MessagesData | undefined {
  if (!data || data.pages.length === 0 || incoming.length === 0) return data;
  const known = new Set<string>();
  for (const page of data.pages) for (const message of page.messages) known.add(message.id);

  const additions: MessageDTO[] = [];
  for (const message of incoming) {
    if (known.has(message.id)) continue;
    known.add(message.id);
    additions.push(message);
  }
  if (additions.length === 0) return data;

  const [first, ...rest] = data.pages;
  const merged = [...first!.messages, ...additions].sort((a, b) => compareMessagesAsc(b, a));
  return { ...data, pages: [{ ...first!, messages: merged }, ...rest] };
}

/** キャッシュ内の最新メッセージ（無ければ null） */
export function latestMessage(data: MessagesData | undefined): MessageDTO | null {
  const all = flattenMessagesAsc(data);
  return all[all.length - 1] ?? null;
}

// ---------------------------------------------------------------------------
// ローカル（送信中・送信失敗）メッセージの合成
// ---------------------------------------------------------------------------

export type LocalMessageStatus = "sending" | "failed";

/** まだサーバーに保存されていない自分の発言（楽観的表示） */
export interface LocalMessage {
  /** "local-..."。React の key にもなる */
  localId: string;
  body: string;
  /** 端末時刻（表示用） */
  createdAt: string;
  /**
   * 送信時点でキャッシュにあった最新メッセージの created_at（無ければ null）。
   * 端末とサーバーの時計ずれに左右されないよう、並び位置とエコー照合はこれを基準にする。
   */
  afterCreatedAt: string | null;
  status: LocalMessageStatus;
  /** 失敗時のエラーメッセージ */
  error?: string;
}

export type TimelineStatus = "sent" | LocalMessageStatus;

/** 画面に並べるメッセージ（サーバー保存済み + ローカル） */
export interface TimelineMessage {
  /** React の key（楽観的メッセージ → 保存済みに置き換わっても変わらない） */
  key: string;
  /** サーバーの id、またはローカルの localId */
  id: string;
  senderType: SenderType;
  body: string;
  createdAt: string;
  status: TimelineStatus;
  /** ローカルメッセージの場合のみ */
  localId?: string;
  error?: string;
}

export interface ConfirmedLocal {
  localId: string;
  messageId: string;
}

export interface MergeTimelineResult {
  items: TimelineMessage[];
  /**
   * サーバー側に同じ発言（エコー）が見つかったローカルメッセージと、対応する保存済みメッセージの id。
   * Realtime が HTTP 応答より先に届いた場合や、タイムアウト扱いだったが実は保存されていた場合に起きる。
   * これらは表示から除外済み（呼び出し側で state から消し、keyAliases に移してよい）。
   */
  confirmed: ConfirmedLocal[];
}

function afterMicros(local: LocalMessage): number {
  return local.afterCreatedAt === null
    ? Number.NEGATIVE_INFINITY
    : timestampToMicros(local.afterCreatedAt);
}

/**
 * サーバー保存済みメッセージ（古い順）とローカルメッセージを 1 本のタイムラインに合成する。
 *
 * - ローカルメッセージは「送信時点の最新メッセージの直後」に置く（失敗したメッセージは後続の
 *   やり取りがあってもその場に残る。送信中のものはキャラ返答より前に並ぶ）。
 * - ローカルと同じ本文のユーザー発言が afterCreatedAt より後にサーバー側に現れたら、それはエコーとみなし
 *   ローカルを非表示にする（1 つのサーバー発言は 1 つのローカルにしか対応させない）。
 * - keyAliases（サーバー id → ローカル id）があれば、保存済みメッセージの key にローカル id を使う
 *   （置き換え時に吹き出しが再マウントされてチラつかないように）。
 */
export function mergeTimeline(
  serverAsc: readonly MessageDTO[],
  locals: readonly LocalMessage[],
  keyAliases: ReadonlyMap<string, string> = new Map(),
): MergeTimelineResult {
  const aliases = new Map(keyAliases);
  const confirmed = new Map<string, string>(); // localId → messageId
  const claimed = new Set<string>();

  // エコー照合（送信順に、より早いサーバー発言から割り当てる）
  const orderedLocals = [...locals].sort(
    (a, b) => afterMicros(a) - afterMicros(b) || (a.createdAt < b.createdAt ? -1 : 1),
  );
  // 並び位置の基準。先に送ったローカルのエコーより後ろに置く（送信順を保つ）
  const anchors = new Map<string, number>();
  let floor = Number.NEGATIVE_INFINITY;
  for (const local of orderedLocals) {
    const after = afterMicros(local);
    const body = local.body.trim();
    const echo = serverAsc.find(
      (message) =>
        message.sender_type === "user" &&
        !claimed.has(message.id) &&
        timestampToMicros(message.created_at) > after &&
        message.body.trim() === body,
    );
    if (echo) {
      claimed.add(echo.id);
      confirmed.set(local.localId, echo.id);
      if (!aliases.has(echo.id)) aliases.set(echo.id, local.localId);
      floor = Math.max(floor, timestampToMicros(echo.created_at));
    } else {
      anchors.set(local.localId, Math.max(after, floor));
    }
  }

  const pending = orderedLocals.filter((local) => !confirmed.has(local.localId));
  const anchorOf = (local: LocalMessage) => anchors.get(local.localId) ?? afterMicros(local);
  const items: TimelineMessage[] = [];
  let index = 0;
  const pushLocal = (local: LocalMessage) => {
    items.push({
      key: local.localId,
      id: local.localId,
      localId: local.localId,
      senderType: "user",
      body: local.body,
      createdAt: local.createdAt,
      status: local.status,
      error: local.error,
    });
  };

  for (const message of serverAsc) {
    const micros = timestampToMicros(message.created_at);
    while (index < pending.length && anchorOf(pending[index]!) < micros) {
      pushLocal(pending[index]!);
      index += 1;
    }
    items.push({
      key: aliases.get(message.id) ?? message.id,
      id: message.id,
      senderType: message.sender_type,
      body: message.body,
      createdAt: message.created_at,
      status: "sent",
    });
  }
  while (index < pending.length) {
    pushLocal(pending[index]!);
    index += 1;
  }

  return {
    items,
    confirmed: Array.from(confirmed, ([localId, messageId]) => ({ localId, messageId })),
  };
}

// ---------------------------------------------------------------------------
// 取得
// ---------------------------------------------------------------------------

/** messages を新しい順に 1 ページ取得する */
export async function fetchMessagesPage(
  supabase: TypedSupabaseClient,
  conversationId: string,
  cursor: MessagesCursor | null,
  signal?: AbortSignal,
): Promise<MessagesPage> {
  let query = supabase
    .from("messages")
    .select(MESSAGE_COLUMNS)
    .eq("conversation_id", conversationId)
    .order("created_at", { ascending: false })
    .order("id", { ascending: false })
    .limit(MESSAGES_PAGE_SIZE);
  if (cursor) {
    // (created_at, id) < (cursor.createdAt, cursor.id) — 同時刻のメッセージを取りこぼさない
    const at = quotePostgrestValue(cursor.createdAt);
    query = query.or(`created_at.lt.${at},and(created_at.eq.${at},id.lt.${cursor.id})`);
  }
  if (signal) query = query.abortSignal(signal);

  const { data, error } = await query;
  if (error) throw toAppError(error);
  return toMessagesPage(data ?? []);
}

type MessageRowLike = Omit<MessageDTO, "sender_type"> & { sender_type: string };

function toMessageDTO(row: MessageRowLike): MessageDTO {
  return {
    id: row.id,
    conversation_id: row.conversation_id,
    sender_type: toSenderType(row.sender_type),
    body: row.body,
    created_at: row.created_at,
  };
}

/** 新しい順に最大 MESSAGES_PAGE_SIZE 件の行 → 1 ページ（ちょうど 1 ページ分あれば、さらに古いページがあり得る） */
export function toMessagesPage(rowsDesc: readonly MessageRowLike[]): MessagesPage {
  const messages = rowsDesc.map(toMessageDTO);
  const oldest = messages[messages.length - 1];
  return {
    messages,
    nextCursor:
      messages.length === MESSAGES_PAGE_SIZE && oldest
        ? { createdAt: oldest.created_at, id: oldest.id }
        : null,
  };
}

/** created_at が since 以降のメッセージを古い順に最大 limit 件取得する（差分取得） */
export async function fetchMessagesSince(
  supabase: TypedSupabaseClient,
  conversationId: string,
  since: string,
  limit: number,
  signal?: AbortSignal,
): Promise<MessageDTO[]> {
  let query = supabase
    .from("messages")
    .select(MESSAGE_COLUMNS)
    .eq("conversation_id", conversationId)
    .gte("created_at", since)
    .order("created_at", { ascending: true })
    .order("id", { ascending: true })
    .limit(limit);
  if (signal) query = query.abortSignal(signal);
  const { data, error } = await query;
  if (error) throw toAppError(error);
  return (data ?? []).map(toMessageDTO);
}

/**
 * useMessages のクエリ設定（プリフェッチと共通にするため切り出している）。
 *
 * - staleTime: Infinity … 新着は Realtime と差分取得（resyncMessages）で追従する。無限クエリの再取得は
 *   読み込み済みの全ページを順に取り直す（過去ログを遡った後だと N 往復）ため、自動の再取得はさせない。
 * - 画面復帰・オンライン復帰時の取りこぼし回収も useMessagesRealtime の差分取得で行う。
 */
export function messagesQueryOptions(conversationId: string) {
  return infiniteQueryOptions<
    MessagesPage,
    Error,
    MessagesData,
    ReturnType<typeof queryKeys.messages>,
    MessagesCursor | null
  >({
    queryKey: queryKeys.messages(conversationId),
    queryFn: ({ pageParam, signal }) =>
      fetchMessagesPage(getSupabaseBrowserClient(), conversationId, pageParam, signal),
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.nextCursor,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

/** 会話のメッセージ（過去ログは fetchNextPage で遡る） */
export function useMessages(conversationId: string | undefined) {
  return useInfiniteQuery({
    ...messagesQueryOptions(conversationId ?? ""),
    enabled: Boolean(conversationId),
  });
}

function hasPages(data: MessagesData | undefined): data is MessagesData {
  return data !== undefined && data.pages.length > 0;
}

/**
 * 進行中の取得（初回・過去ログの読み込み・取り直し）の Promise。無ければ undefined。
 * 無限クエリの取得は「取得開始時点のページ + 新しいページ」でキャッシュを上書きするため、その途中に
 * setQueryData で差し込んだメッセージは取得完了時に消える。終わるのを待って差し込み直すのに使う。
 */
function inFlightFetch(queryClient: QueryClient, queryKey: QueryKey): Promise<unknown> | undefined {
  const query = queryClient.getQueryCache().find({ queryKey, exact: true });
  return query && query.state.fetchStatus !== "idle" ? query.promise : undefined;
}

/** キャッシュ（読み込み済みのページ）にこのメッセージがあるか */
export function isMessageCached(
  queryClient: QueryClient,
  conversationId: string,
  messageId: string,
): boolean {
  const data = queryClient.getQueryData<MessagesData>(queryKeys.messages(conversationId));
  return Boolean(
    data?.pages.some((page) => page.messages.some((message) => message.id === messageId)),
  );
}

/**
 * 別の取得で手に入った最新ページ（会話の取得と同時に読んだ 1 ページ目・作成時の挨拶）でキャッシュを埋める。
 * 既にキャッシュがあれば何もしない（Realtime 等で追従している内容を古いスナップショットで戻さない）。
 */
export function seedMessagesCache(
  queryClient: QueryClient,
  conversationId: string,
  firstPage: MessagesPage,
): void {
  queryClient.setQueryData<MessagesData>(
    queryKeys.messages(conversationId),
    (data) => data ?? { pages: [firstPage], pageParams: [null] },
  );
}

/**
 * キャッシュにメッセージを追加する（送信 API の応答・Realtime 受信）。差し込めたら true。
 *
 * キャッシュが未作成（初回取得中・初回取得の失敗後）のときは差し込み先が無い。そのまま捨てると、
 * 進行中の初回取得が保存前のスナップショットだった場合に、送った発言やキャラの返答が次の再取得まで
 * 表示されない。そこで、進行中の取得（無ければ新しい取得）が終わるのを待ってから差し込み、false を返す。
 * （invalidate / refetch は data が無いと進行中の取得に合流するだけなので、それだけでは回収できない）
 */
export function addMessagesToCache(
  queryClient: QueryClient,
  conversationId: string,
  messages: readonly MessageDTO[],
): boolean {
  const queryKey = queryKeys.messages(conversationId);
  const upsert = () =>
    queryClient.setQueryData<MessagesData>(queryKey, (data) =>
      upsertMessagesInPages(data, messages),
    );
  if (hasPages(queryClient.getQueryData<MessagesData>(queryKey))) {
    upsert();
    // 過去ログの読み込み等の途中なら、その結果で上書きされて消えるため、終わったら差し込み直す
    // （id で重複排除されるので何度差し込んでも安全）
    const inFlight = inFlightFetch(queryClient, queryKey);
    if (inFlight) void inFlight.then(upsert, upsert);
    return true;
  }
  void queryClient.refetchQueries({ queryKey, exact: true }).then(() => {
    // 取得に失敗した場合は data が無いまま（何もしない）。再試行の取得で保存済みの発言も読み込まれる
    queryClient.setQueryData<MessagesData>(queryKey, (data) =>
      upsertMessagesInPages(data, messages),
    );
  });
  return false;
}

/** ISO 形式の時刻から ms を引いた ISO 文字列（ミリ秒精度に切り下げ。解釈できなければ null） */
export function shiftTimestamp(value: string, deltaMs: number): string | null {
  const micros = timestampToMicros(value);
  if (Number.isNaN(micros)) return null;
  return new Date(Math.floor(micros / 1000) + deltaMs).toISOString();
}

/** 差分取得の起点（キャッシュの最新メッセージから CATCH_UP_LOOKBACK_MS 遡った時刻。空なら先頭から） */
export function catchUpSince(data: MessagesData | undefined): string {
  const newest = latestMessage(data);
  return (
    (newest && shiftTimestamp(newest.created_at, -CATCH_UP_LOOKBACK_MS)) ??
    new Date(0).toISOString()
  );
}

export interface ResyncOptions {
  /** 画面を離れたときの中断 */
  signal?: AbortSignal;
  /** 差分の取得（テスト用に差し替え可能。既定は fetchMessagesSince） */
  fetchSince?: (since: string, limit: number, signal?: AbortSignal) => Promise<MessageDTO[]>;
}

/**
 * 取りこぼしの回収（Realtime の購読開始・再接続時、画面復帰・オンライン復帰時）。
 *
 * キャッシュの最新メッセージより新しいもの（少し遡って取り、id で重複排除）だけを取得して先頭ページへ差し込む。
 * ふつうは 0〜数件の小さな応答で、読み込み済みのページを取り直さない。
 * - 初回取得の途中なら、終わるのを待ってから差分を取る（その取得が購読開始より前のスナップショットでも、
 *   間に保存されたメッセージを回収できる）
 * - 取りこぼしが CATCH_UP_LIMIT 件以上なら差分では埋めず、最新の 1 ページだけを取り直す
 */
export async function resyncMessages(
  queryClient: QueryClient,
  conversationId: string,
  options: ResyncOptions = {},
): Promise<void> {
  const { signal } = options;
  const fetchSince =
    options.fetchSince ??
    ((since: string, limit: number, abortSignal?: AbortSignal) =>
      fetchMessagesSince(getSupabaseBrowserClient(), conversationId, since, limit, abortSignal));
  const queryKey = queryKeys.messages(conversationId);

  if (!hasPages(queryClient.getQueryData<MessagesData>(queryKey))) {
    // 進行中の初回取得に合流する（無ければ取得を始める）
    await queryClient.refetchQueries({ queryKey, exact: true });
    // 失敗して data が無いままならエラー表示（再試行）に任せる
    if (!hasPages(queryClient.getQueryData<MessagesData>(queryKey))) return;
  }
  if (signal?.aborted) return;

  const since = catchUpSince(queryClient.getQueryData<MessagesData>(queryKey));
  const missed = await fetchSince(since, CATCH_UP_LIMIT, signal);
  if (signal?.aborted) return;

  if (missed.length >= CATCH_UP_LIMIT) {
    // 取りこぼしが多すぎる: 最新の 1 ページだけを取り直す（全ページの順次再取得にしない）
    queryClient.setQueryData<MessagesData>(queryKey, (data) =>
      data && data.pages.length > 1
        ? { pages: data.pages.slice(0, 1), pageParams: data.pageParams.slice(0, 1) }
        : data,
    );
    await queryClient.invalidateQueries({ queryKey, exact: true });
    return;
  }
  if (missed.length > 0) addMessagesToCache(queryClient, conversationId, missed);
}

// ---------------------------------------------------------------------------
// Realtime
// ---------------------------------------------------------------------------

/** Realtime のチャンネル名の重複回避用 */
export function uniqueSuffix(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID().slice(0, 8)
    : Math.random().toString(36).slice(2, 10);
}

/**
 * 会話の新着メッセージを購読し、キャッシュへ差し込む。
 * - SUBSCRIBED になるたび（初回・再接続）、画面復帰（visibilitychange）・オンライン復帰（online）時に
 *   差分取得（resyncMessages）で購読開始前や切断中の取りこぼしを回収する。同時に何度呼ばれても
 *   実行中の 1 回が終わってからもう 1 回だけ実行する。
 * - 前回の表示のキャッシュが古い（MESSAGES_CATCH_UP_ON_MOUNT_MS 以上）ときは、開いた時点でも差分を取る。
 * - onInsert は新規に届いたメッセージごとに呼ばれる（既読化・「新しいメッセージ」表示用）。
 */
export function useMessagesRealtime(
  conversationId: string | undefined,
  onInsert?: (message: MessageDTO) => void,
): void {
  const queryClient = useQueryClient();
  const onInsertRef = useRef(onInsert);
  useEffect(() => {
    onInsertRef.current = onInsert;
  }, [onInsert]);

  useEffect(() => {
    if (!conversationId) return;
    const supabase = getSupabaseBrowserClient();
    const controller = new AbortController();
    let running = false;
    let again = false;
    const resync = () => {
      if (controller.signal.aborted) return;
      if (running) {
        again = true;
        return;
      }
      running = true;
      resyncMessages(queryClient, conversationId, { signal: controller.signal })
        .catch((error: unknown) => {
          // 次の復帰・再接続で再び回収する
          if (!controller.signal.aborted) console.warn("[dm] resync failed:", error);
        })
        .finally(() => {
          running = false;
          if (again) {
            again = false;
            resync();
          }
        });
    };

    const cached = queryClient.getQueryState(queryKeys.messages(conversationId));
    if (
      cached?.data !== undefined &&
      Date.now() - cached.dataUpdatedAt > MESSAGES_CATCH_UP_ON_MOUNT_MS
    ) {
      resync();
    }
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") resync();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("online", resync);

    const channel = supabase
      // realtime-js は同名トピックの既存チャンネルを返すため、StrictMode の再マウント等で
      // 片付け中のチャンネルを掴まないようマウントごとに一意な名前にする
      .channel(`dm-messages:${conversationId}:${uniqueSuffix()}`)
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "messages",
          filter: `conversation_id=eq.${conversationId}`,
        },
        (payload) => {
          const message = parseMessageRow(payload.new);
          if (!message || message.conversation_id !== conversationId) return;
          addMessagesToCache(queryClient, conversationId, [message]);
          onInsertRef.current?.(message);
        },
      )
      .subscribe((status, error) => {
        if (status === "SUBSCRIBED") {
          // 初回: 取得〜購読開始の間の取りこぼしを回収 / 再接続: 切断中の取りこぼしを回収
          resync();
        } else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT") {
          // 自動で再接続される。画面復帰・オンライン復帰時の差分取得でも回収される
          console.warn(`[dm] realtime ${status}`, error?.message ?? "");
        }
      });
    return () => {
      controller.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("online", resync);
      void supabase.removeChannel(channel);
    };
  }, [conversationId, queryClient]);
}
