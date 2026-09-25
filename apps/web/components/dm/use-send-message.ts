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

/** 送信してから「入力中…」を出すまで（既読 → 入力中、の自然な間） */
export const TYPING_DELAY_MS = 400;
/** 「入力中…」を最低限見せる時間（モック LLM のように即答でも一瞬で消えないように） */
export const MIN_TYPING_MS = 1100;

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
  /**
   * 返答待ち中は、この時刻より新しいキャラの発言を表示せずに待たせる（入力中の演出）。
   * null = 会話が空の状態で送信した。
   */
  holdAfter: string | null;
  /** 「入力中…」を表示するか */
  typing: boolean;
}

export interface UseSendMessageOptions {
  conversationId: string;
  characterId: string;
  /** 送信時点のキャッシュ内の最新メッセージの created_at */
  getLatestCreatedAt: () => string | null;
  /** キャラの返答が表示されたとき（既読化・メモリの更新など） */
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
 * DM の送信（POST /chat）。
 * 1. 楽観的に自分の吹き出しを出す（送信中は薄く）→ 返答が表示されるまで送信ボタンは無効
 * 2. 少し間をおいて「入力中…」→ 応答が来たら保存済みの自分の発言とキャラ返答をキャッシュへ
 *    （Realtime で先に届いていても id で重複排除される）
 * 3. 失敗したら吹き出しを「送信できませんでした・タップで再送」にしてトースト
 *
 * 返答待ちのまま画面を離れても送信は続く（/chat はユーザー発言と返答を同時に保存するため、その間サーバーには
 * まだ何も無い）。キャッシュへの反映とトーストはミューテーション側（unmount 後も実行される）で行い、
 * 開き直した画面は MutationCache から返答待ち・失敗の発言を引き継ぐ（吹き出し・「入力中…」・送信ボタンの無効化）。
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
    return first ? { holdAfter: first.local.afterCreatedAt, typing: true } : null;
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
    mutationFn: ({ body }) =>
      api.sendChat({ character_id: characterId, conversation_id: conversationId, message: body }),
    // ここ（useMutation の options）のコールバックは画面を離れた後も実行される
    onSuccess: (response) => {
      addMessagesToCache(queryClient, conversationId, [
        response.user_message,
        response.character_message,
      ]);
      void invalidateDmSummaries(queryClient);
    },
    onError: (error) => {
      console.warn("[dm] send failed:", error);
      toast.error(sendErrorMessage(error));
    },
  });
  const { mutateAsync } = chat;

  const run = useCallback(
    async (local: LocalMessage, request: () => Promise<ChatResponse>, startedAt = Date.now()) => {
      inFlightRef.current = true;
      const typingDelay = Math.max(0, TYPING_DELAY_MS - (Date.now() - startedAt));
      setPending({ holdAfter: local.afterCreatedAt, typing: typingDelay === 0 });
      const typingTimer = setTimeout(
        () => setPending((state) => (state ? { ...state, typing: true } : state)),
        typingDelay,
      );
      try {
        const response = await request();
        // 画面を離れた後なら何もしない（キャッシュへの反映はミューテーション側で済んでいる。
        // ここで既読にすると、別の画面にいる間に届いた返答が未読にならない）
        if (!mountedRef.current) return;
        setKeyAliases((previous) =>
          previous.get(response.user_message.id) === local.localId
            ? previous
            : new Map(previous).set(response.user_message.id, local.localId),
        );
        // キャッシュへ差し込めなかった（初回取得の完了待ち）場合は、保存済みの発言が表示されるまで
        // 楽観的な吹き出しを残す（届いたら mergeTimeline のエコー照合 → confirmLocals で片付く）
        if (isMessageCached(queryClient, conversationId, response.user_message.id)) {
          setLocals((previous) => previous.filter((item) => item.localId !== local.localId));
        }
        // 即答でも「入力中…」を少し見せてから返答を出す
        const remaining = TYPING_DELAY_MS + MIN_TYPING_MS - (Date.now() - startedAt);
        if (remaining > 0) await sleep(remaining);
        setPending(null);
        if (mountedRef.current) onRepliedRef.current?.(response);
      } catch (error) {
        // トーストはミューテーション側で出している（同じ失敗を二重に出さない）
        setPending(null);
        const message = sendErrorMessage(error);
        setLocals((previous) =>
          previous.map((item) =>
            item.localId === local.localId ? { ...item, status: "failed", error: message } : item,
          ),
        );
      } finally {
        clearTimeout(typingTimer);
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
