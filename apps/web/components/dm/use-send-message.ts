"use client";

import type { ChatResponse } from "@everkano/shared";
import {
  useMutation,
  useQueryClient,
  type Mutation,
  type MutationCache,
  type MutationState,
  type QueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/client";
import { getErrorMessage, isApiError } from "@/lib/api/errors";
import { invalidateDmSummaries } from "@/lib/queries/dm";
import {
  addMessagesToCache,
  isMessageCached,
  type ConfirmedLocal,
  type LocalMessage,
} from "@/lib/queries/messages";
import { seedSafetyResources } from "@/lib/queries/safety";
import { chatStreamStore, streamKey } from "./stream-state";

/** 送信してから「入力中…」を出すまで（既読 → 入力中、の自然な間） */
export const TYPING_DELAY_MS = 400;
/**
 * 送信してから返答の最初の文字を表示してよくなるまでの最短時間。
 * 「入力中…」を最低 600ms は見せる（モック LLM のように即答でも一瞬で消えないように）。
 * 実際の LLM の最初の文字はふつうこれより遅いので、E8（送信から最初の文字まで中央値 2.5 秒）の妨げにはならない。
 */
export const MIN_REPLY_DELAY_MS = 1000;

const RATE_LIMITED_MESSAGE = "送信が集中しています。しばらくしてから再度お試しください。";

/**
 * DM 送信（POST /chat）のミューテーションキー。送信は画面を離れても（unmount 後も）続くため、
 * 開き直した画面が MutationCache から「返答待ちの発言」「送信に失敗した発言」を引き継ぐのに使う。
 */
export const chatMutationKey = (conversationId: string) => ["dm", "chat", conversationId] as const;

/** 送信 1 回分の変数（楽観的メッセージのうち状態以外。開き直した画面で吹き出しを復元できるよう全部持つ） */
export type ChatVariables = Omit<LocalMessage, "status" | "error">;

type ChatMutation = Mutation<ChatResponse, unknown, ChatVariables, unknown>;
type ChatMutationState = Pick<
  MutationState<ChatResponse, unknown, ChatVariables, unknown>,
  "status" | "variables" | "error" | "submittedAt"
>;

export interface RestoredSend<T> {
  local: LocalMessage;
  /** 返答待ちなら、その送信（完了を待って表示を仕上げる） */
  pending?: T;
}

/**
 * 前回の表示（同じ会話）から引き継ぐ送信を選ぶ純粋関数。
 * - 同じ localId の送信（再送）は最新の 1 回だけを見る
 * - 返答待ち → 「送信中」の吹き出し、失敗 → 「送信できませんでした・タップで再送」の吹き出し
 * - 成功したものは保存済みのメッセージとして表示されるので引き継がない
 * 送信した順（submittedAt）に並べる。
 */
export function restoreSends<T extends { state: ChatMutationState }>(
  mutations: readonly T[],
): RestoredSend<T>[] {
  const latest = new Map<string, T>();
  for (const mutation of mutations) {
    const variables = mutation.state.variables;
    if (!variables) continue;
    const current = latest.get(variables.localId);
    if (!current || current.state.submittedAt <= mutation.state.submittedAt) {
      latest.set(variables.localId, mutation);
    }
  }
  return [...latest.values()]
    .filter((m) => m.state.status === "pending" || m.state.status === "error")
    .sort((a, b) => a.state.submittedAt - b.state.submittedAt)
    .map((mutation) => {
      const variables = mutation.state.variables!;
      return mutation.state.status === "pending"
        ? { local: { ...variables, status: "sending" }, pending: mutation }
        : {
            local: {
              ...variables,
              status: "failed",
              error: sendErrorMessage(mutation.state.error),
            },
          };
    });
}

function findChatMutations(queryClient: QueryClient, conversationId: string): ChatMutation[] {
  return queryClient
    .getMutationCache()
    .findAll({ mutationKey: chatMutationKey(conversationId), exact: true }) as ChatMutation[];
}

/** 進行中の送信の完了を待つ（別の画面インスタンスが始めた送信を引き継ぐとき） */
export function waitForMutation<TData, TError, TVariables, TContext>(
  mutationCache: MutationCache,
  mutation: Mutation<TData, TError, TVariables, TContext>,
): Promise<TData> {
  return new Promise<TData>((resolve, reject) => {
    /** 完了していれば結果を返して true */
    const settle = (): boolean => {
      const { status, data, error } = mutation.state;
      if (status === "success") resolve(data as TData);
      else if (status === "error")
        reject(error instanceof Error ? error : new Error(String(error)));
      else return false;
      return true;
    };
    if (settle()) return;
    const unsubscribe = mutationCache.subscribe((event) => {
      if (event.mutation === mutation && settle()) unsubscribe();
    });
  });
}

export interface SendState {
  /** 返答待ちの送信（ユーザー発言の localId）。受信中の吹き出しとの対応付けに使う */
  localId: string;
  /**
   * 返答待ち中は、この時刻より新しいキャラの発言を表示せずに待たせる（入力中の演出・受信中の吹き出しとの重複防止）。
   * null = 会話が空の状態で送信した。
   */
  holdAfter: string | null;
  /** 「入力中…」を出してよい時間になった（最初の文字が表示されるまで出す） */
  typing: boolean;
  /** 返答の最初の文字を表示してよい時間になった（MIN_REPLY_DELAY_MS） */
  revealed: boolean;
}

export interface UseSendMessageOptions {
  conversationId: string;
  characterId: string;
  /** 送信時点のキャッシュ内の最新メッセージの created_at */
  getLatestCreatedAt: () => string | null;
  /** キャラの返答を表示し終えたとき（既読化など） */
  onReplied?: (response: ChatResponse) => void;
}

export interface UseSendMessage {
  locals: LocalMessage[];
  /** 保存済みメッセージ id → 楽観的メッセージの localId（吹き出しの key を保つ） */
  keyAliases: ReadonlyMap<string, string>;
  /** 返答待ち（null = 待っていない） */
  pending: SendState | null;
  send: (body: string) => void;
  retry: (localId: string) => void;
  /** サーバー側で保存が確認できた（エコーが届いた）ローカルメッセージを片付ける */
  confirmLocals: (confirmed: readonly ConfirmedLocal[]) => void;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function toVariables({ localId, body, createdAt, afterCreatedAt }: LocalMessage): ChatVariables {
  return { localId, body, createdAt, afterCreatedAt };
}

let localSeq = 0;
function nextLocalId(): string {
  localSeq += 1;
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : Math.random().toString(16).slice(2);
  return `local-${localSeq}-${random}`;
}

/** 送信失敗時に表示するメッセージ（429 は仕様どおりの文言） */
export function sendErrorMessage(error: unknown): string {
  if (isApiError(error) && error.code === "rate_limited") return RATE_LIMITED_MESSAGE;
  return getErrorMessage(error, "送信できませんでした。再度お試しください。");
}

/**
 * DM の送信（POST /chat/stream。使えなければ POST /chat）。
 * 1. 楽観的に自分の吹き出しを出す（送信中は薄く）→ 返答を表示し終えるまで送信ボタンは無効
 * 2. 少し間をおいて「入力中…」→ 最初の delta が届いたら受信中の吹き出しに切り替え、届くたびに追記する
 *    （replace は本文の置き換え。最初の文字は送信から MIN_REPLY_DELAY_MS 以降に出す）
 * 3. done で保存済みの自分の発言とキャラ返答をキャッシュへ（Realtime で先に届いていても id で重複排除され、
 *    返答待ちの間は holdCharacterReplies で隠れている）。受信中の吹き出しは同じ key のまま保存済みの返答に置き換わる
 * 4. error・通信失敗は、受信中の吹き出しを消して自分の吹き出しを「送信できませんでした・タップで再送」にしてトースト
 *    （error は「何も保存していない」。通信が途中で切れて実は保存されていた場合は、Realtime・差分取得で届いた
 *    保存済みの発言が失敗の吹き出しを置き換える（mergeTimeline のエコー照合））
 * 5. E6: 安全対応をした返答（character_message.safety_triggered）の下には相談窓口のカードが出る。
 *    返答に入っていた窓口の一覧はキャッシュに入れておく（seedSafetyResources。GET /safety/resources を待たない）
 *
 * 返答待ちのまま画面を離れても送信は続く（ユーザー発言と返答は done の直前にまとめて保存されるため、その間
 * サーバーにはまだ何も無い）。受信中の本文は chatStreamStore、キャッシュへの反映とトーストはミューテーション側
 * （unmount 後も実行される）で扱い、開き直した画面は MutationCache から返答待ち・失敗の発言を引き継ぐ
 * （吹き出し・受信中の本文・「入力中…」・送信ボタンの無効化）。
 */
export function useSendMessage({
  conversationId,
  characterId,
  getLatestCreatedAt,
  onReplied,
}: UseSendMessageOptions): UseSendMessage {
  const queryClient = useQueryClient();
  const toast = useToast();
  // 前回の表示から引き継ぐ送信（マウント時に 1 回だけ読む）
  const [restored] = useState(() => restoreSends(findChatMutations(queryClient, conversationId)));
  const [locals, setLocals] = useState<LocalMessage[]>(() => restored.map((r) => r.local));
  const [keyAliases, setKeyAliases] = useState<ReadonlyMap<string, string>>(() => new Map());
  const [pending, setPending] = useState<SendState | null>(() => {
    const first = restored.find((r) => r.pending);
    return first
      ? {
          localId: first.local.localId,
          holdAfter: first.local.afterCreatedAt,
          typing: true,
          revealed: Date.now() - (first.pending?.state.submittedAt ?? 0) >= MIN_REPLY_DELAY_MS,
        }
      : null;
  });
  const inFlightRef = useRef(restored.some((r) => r.pending));
  const localsRef = useRef(locals);
  useEffect(() => {
    localsRef.current = locals;
  }, [locals]);
  const onRepliedRef = useRef(onReplied);
  useEffect(() => {
    onRepliedRef.current = onReplied;
  }, [onReplied]);
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const chat = useMutation<ChatResponse, unknown, ChatVariables>({
    mutationKey: chatMutationKey(conversationId),
    mutationFn: ({ body, localId }) => {
      chatStreamStore.start(conversationId, localId);
      return api.streamChat(
        { character_id: characterId, conversation_id: conversationId, message: body },
        { onEvent: (event) => chatStreamStore.apply(conversationId, localId, event) },
      );
    },
    // ここ（useMutation の options）のコールバックは画面を離れた後も実行される
    onSuccess: (response) => {
      addMessagesToCache(queryClient, conversationId, [
        response.user_message,
        response.character_message,
      ]);
      if (response.safety?.triggered) {
        seedSafetyResources(queryClient, response.safety.resources);
      }
      void invalidateDmSummaries(queryClient);
    },
    onError: (error, { localId }) => {
      console.warn("[dm] send failed:", error);
      chatStreamStore.clear(conversationId, localId);
      toast.error(sendErrorMessage(error));
    },
  });
  const { mutateAsync } = chat;

  const run = useCallback(
    async (local: LocalMessage, request: () => Promise<ChatResponse>, startedAt = Date.now()) => {
      inFlightRef.current = true;
      const elapsed = Date.now() - startedAt;
      const typingDelay = Math.max(0, TYPING_DELAY_MS - elapsed);
      const revealDelay = Math.max(0, MIN_REPLY_DELAY_MS - elapsed);
      setPending({
        localId: local.localId,
        holdAfter: local.afterCreatedAt,
        typing: typingDelay === 0,
        revealed: revealDelay === 0,
      });
      const typingTimer = setTimeout(
        () => setPending((state) => (state ? { ...state, typing: true } : state)),
        typingDelay,
      );
      const revealTimer = setTimeout(
        () => setPending((state) => (state ? { ...state, revealed: true } : state)),
        revealDelay,
      );
      try {
        const response = await request();
        // 画面を離れた後なら何もしない（キャッシュへの反映はミューテーション側で済んでいる。
        // ここで既読にすると、別の画面にいる間に届いた返答が未読にならない）
        if (!mountedRef.current) return;
        // 即答でも「入力中…」を少し見せてから返答を出す
        const remaining = MIN_REPLY_DELAY_MS - (Date.now() - startedAt);
        if (remaining > 0) await sleep(remaining);
        if (!mountedRef.current) return;
        // 保存済みの自分の発言は楽観的な吹き出しの key、キャラの返答は受信中の吹き出しの key を引き継ぐ
        // （置き換わるときに再マウントされてチラつかないように）
        setKeyAliases((previous) => {
          const userKey = local.localId;
          const replyKey = streamKey(local.localId);
          if (
            previous.get(response.user_message.id) === userKey &&
            previous.get(response.character_message.id) === replyKey
          ) {
            return previous;
          }
          return new Map(previous)
            .set(response.user_message.id, userKey)
            .set(response.character_message.id, replyKey);
        });
        // キャッシュへ差し込めなかった（初回取得の完了待ち）場合は、保存済みの発言が表示されるまで
        // 楽観的な吹き出しを残す（届いたら mergeTimeline のエコー照合 → confirmLocals で片付く。
        // 受信中の吹き出しも、保存済みの返答が現れるまで streamingTimelineItem が出し続ける）
        if (isMessageCached(queryClient, conversationId, response.user_message.id)) {
          setLocals((previous) => previous.filter((item) => item.localId !== local.localId));
        }
        setPending(null);
        onRepliedRef.current?.(response);
      } catch (error) {
        // トーストはミューテーション側で出している（同じ失敗を二重に出さない）
        chatStreamStore.clear(conversationId, local.localId);
        setPending(null);
        const message = sendErrorMessage(error);
        setLocals((previous) =>
          previous.map((item) =>
            item.localId === local.localId ? { ...item, status: "failed", error: message } : item,
          ),
        );
      } finally {
        clearTimeout(typingTimer);
        clearTimeout(revealTimer);
        inFlightRef.current = false;
      }
    },
    [conversationId, queryClient],
  );

  // 前回の表示で返答待ちだった送信を引き継ぎ、完了したら通常の送信と同じように表示を仕上げる
  // （StrictMode の再実行でも二重に引き継がない）
  const adoptedRef = useRef(new Set<string>());
  useEffect(() => {
    const targets = restored.filter(
      (r): r is Required<RestoredSend<ChatMutation>> =>
        r.pending !== undefined && !adoptedRef.current.has(r.local.localId),
    );
    if (targets.length === 0) return;
    for (const target of targets) adoptedRef.current.add(target.local.localId);
    const mutationCache = queryClient.getMutationCache();
    void (async () => {
      for (const { local, pending: mutation } of targets) {
        await run(
          local,
          () => waitForMutation(mutationCache, mutation),
          mutation.state.submittedAt,
        );
      }
    })();
  }, [queryClient, restored, run]);

  const send = useCallback(
    (body: string) => {
      const text = body.trim();
      if (!text || inFlightRef.current) return;
      const local: LocalMessage = {
        localId: nextLocalId(),
        body: text,
        createdAt: new Date().toISOString(),
        afterCreatedAt: getLatestCreatedAt(),
        status: "sending",
      };
      setLocals((previous) => [...previous, local]);
      void run(local, () => mutateAsync(toVariables(local)));
    },
    [getLatestCreatedAt, mutateAsync, run],
  );

  const retry = useCallback(
    (localId: string) => {
      if (inFlightRef.current) return;
      const target = localsRef.current.find((item) => item.localId === localId);
      if (!target || target.status !== "failed") return;
      // 再送は「今」送る発言として末尾に移す
      const local: LocalMessage = {
        ...target,
        createdAt: new Date().toISOString(),
        afterCreatedAt: getLatestCreatedAt(),
        status: "sending",
        error: undefined,
      };
      setLocals((previous) => [...previous.filter((item) => item.localId !== localId), local]);
      void run(local, () => mutateAsync(toVariables(local)));
    },
    [getLatestCreatedAt, mutateAsync, run],
  );

  const confirmLocals = useCallback((confirmed: readonly ConfirmedLocal[]) => {
    if (confirmed.length === 0) return;
    // key を保ったまま保存済みの吹き出しに引き継ぐ（再マウントによるチラつき防止）
    setKeyAliases((previous) => {
      if (confirmed.every((c) => previous.get(c.messageId) === c.localId)) return previous;
      const next = new Map(previous);
      for (const c of confirmed) next.set(c.messageId, c.localId);
      return next;
    });
    const drop = new Set(confirmed.map((c) => c.localId));
    setLocals((previous) =>
      previous.some((item) => drop.has(item.localId))
        ? previous.filter((item) => !drop.has(item.localId))
        : previous,
    );
  }, []);

  return { locals, keyAliases, pending, send, retry, confirmLocals };
}
