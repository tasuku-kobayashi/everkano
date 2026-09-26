/**
 * 受信中のキャラの返答（POST /chat/stream）の状態。
 *
 * 送信（ミューテーション）は DM 画面を離れても続くため、受信した本文は画面の state ではなく
 * 会話ごとの小さなストアに置く。開き直した画面は、返答待ちの送信を引き継ぐときにここから途中の本文を表示する。
 * - reduceChatStream: イベント（delta / replace / done）を本文に反映する純粋関数
 * - streamingTimelineItem: 画面に出す「受信中の吹き出し」を決める純粋関数
 * どちらも stream-state.test.ts で単体テストしている。
 */

import type { ChatStreamEvent, SafetyInfo } from "@everkano/shared";
import { useSyncExternalStore } from "react";
import type { TimelineMessage } from "@/lib/queries/messages";

export interface StreamingReply {
  /** どの送信（ユーザー発言の localId）への返答か */
  localId: string;
  /** 送信した時刻（端末時刻・ISO）。受信中の吹き出しの時刻として使う */
  startedAt: string;
  /** これまでに届いた本文（replace で置き換わる。done で保存済みの本文になる） */
  text: string;
  /** 出力検査・安全対応で本文が置き換えられた理由 */
  replacedReason: "moderated" | "safety" | null;
  /** 保存済みのキャラの返答の id（done を受け取るまで null） */
  messageId: string | null;
  /** E6: 安全対応（相談窓口）。done で分かる */
  safety: SafetyInfo | null;
}

export function initialStreamingReply(localId: string, startedAt: string): StreamingReply {
  return { localId, startedAt, text: "", replacedReason: null, messageId: null, safety: null };
}

/** イベントを 1 件反映する（error は呼び出し側で状態ごと消すので何もしない） */
export function reduceChatStream(state: StreamingReply, event: ChatStreamEvent): StreamingReply {
  switch (event.type) {
    case "delta":
      // done の後に遅れて届いた delta は無視する（保存済みの本文が正）
      if (state.messageId !== null || event.data.text === "") return state;
      return { ...state, text: state.text + event.data.text };
    case "replace":
      if (state.messageId !== null) return state;
      return { ...state, text: event.data.text, replacedReason: event.data.reason };
    case "done":
      return {
        ...state,
        text: event.data.character_message.body,
        messageId: event.data.character_message.id,
        safety: event.data.safety?.triggered ? event.data.safety : null,
      };
    case "error":
      return state;
  }
}

/** 受信中の吹き出しの key（保存済みのメッセージに置き換わっても同じ key を引き継ぐ） */
export function streamKey(localId: string): string {
  return `stream-${localId}`;
}

export interface StreamingItemOptions {
  /** 返答待ちの送信の localId（無ければ null） */
  activeLocalId: string | null;
  /** 最初の文字を表示してよいか（送信直後の「入力中…」を最低限見せる間は false） */
  revealed: boolean;
  /** キャッシュにある保存済みメッセージの id */
  savedIds: ReadonlySet<string>;
}

/**
 * 画面に出す受信中の吹き出し（無ければ null）。
 * - 返答待ちの送信への返答: 表示してよくなってから、本文が届いていれば出す（それまでは「入力中…」）
 * - 返答の表示を終えた後も、保存済みのメッセージがまだキャッシュに無い間は出し続ける
 *   （保存済みのメッセージが現れたら同じ key で置き換わる。一瞬消えて見えるのを防ぐ）
 */
export function streamingTimelineItem(
  stream: StreamingReply | null,
  { activeLocalId, revealed, savedIds }: StreamingItemOptions,
): TimelineMessage | null {
  if (!stream || stream.text === "") return null;
  const active = stream.localId === activeLocalId;
  if (active ? !revealed : stream.messageId === null || savedIds.has(stream.messageId)) {
    return null;
  }
  const key = streamKey(stream.localId);
  // E6: 安全対応の返答は replace（reason: safety）の時点で分かる。保存を待たずに相談窓口のカードを出す
  const safety = stream.replacedReason === "safety" || stream.safety !== null;
  return {
    key,
    id: stream.messageId ?? key,
    senderType: "character",
    body: stream.text,
    createdAt: stream.startedAt,
    status: stream.messageId === null ? "streaming" : "sent",
    ...(safety ? { safetyTriggered: true } : {}),
  };
}

/**
 * 返答が届き始めた送信の自分の吹き出しを「送信済み」の見た目にする（サーバーが受け取ったことは確か。
 * 保存は done の直前なので、そのままだと返答の表示中も「送信中」の薄い吹き出しのままになる）。
 * error で終わった場合は、呼び出し側が「送信できませんでした」に変える。
 */
export function markDeliveredWhileStreaming(
  items: readonly TimelineMessage[],
  localId: string | null,
): TimelineMessage[] {
  if (localId === null) return [...items];
  let changed = false;
  const next = items.map((item) => {
    if (item.localId !== localId || item.status !== "sending") return item;
    changed = true;
    return { ...item, status: "sent" as const };
  });
  return changed ? next : [...items];
}

// ---------------------------------------------------------------------------
// ストア（会話 id → 受信中の返答）
// ---------------------------------------------------------------------------

export interface ChatStreamStore {
  get(conversationId: string): StreamingReply | null;
  /** 送信を始めた（前の返答の状態は捨てる） */
  start(conversationId: string, localId: string, startedAt?: string): void;
  /** イベントを反映する（別の送信の状態になっていれば何もしない） */
  apply(conversationId: string, localId: string, event: ChatStreamEvent): void;
  /** 状態を消す（localId を指定したら、その送信の状態のときだけ） */
  clear(conversationId: string, localId?: string): void;
  subscribe(listener: () => void): () => void;
}

export function createChatStreamStore(): ChatStreamStore {
  const states = new Map<string, StreamingReply>();
  const listeners = new Set<() => void>();
  const emit = () => {
    for (const listener of listeners) listener();
  };
  return {
    get: (conversationId) => states.get(conversationId) ?? null,
    start(conversationId, localId, startedAt = new Date().toISOString()) {
      states.set(conversationId, initialStreamingReply(localId, startedAt));
      emit();
    },
    apply(conversationId, localId, event) {
      const current = states.get(conversationId);
      if (!current || current.localId !== localId) return;
      const next = reduceChatStream(current, event);
      if (next === current) return;
      states.set(conversationId, next);
      emit();
    },
    clear(conversationId, localId) {
      const current = states.get(conversationId);
      if (!current || (localId !== undefined && current.localId !== localId)) return;
      states.delete(conversationId);
      emit();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

/** アプリ全体で 1 つのストア（送信のミューテーションと画面が共有する） */
export const chatStreamStore = createChatStreamStore();

const getServerSnapshot = () => null;

/** その会話の受信中の返答（無ければ null） */
export function useChatStream(
  conversationId: string,
  store: ChatStreamStore = chatStreamStore,
): StreamingReply | null {
  return useSyncExternalStore(store.subscribe, () => store.get(conversationId), getServerSnapshot);
}
