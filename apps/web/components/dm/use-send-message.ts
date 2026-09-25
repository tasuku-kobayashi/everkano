"use client";

import type { ChatResponse } from "@everkano/shared";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api/client";
import { getErrorMessage, isApiError } from "@/lib/api/errors";
import { addMessagesToCache, type ConfirmedLocal, type LocalMessage } from "@/lib/queries/messages";

/** 送信してから「入力中…」を出すまで（既読 → 入力中、の自然な間） */
export const TYPING_DELAY_MS = 400;
/** 「入力中…」を最低限見せる時間（モック LLM のように即答でも一瞬で消えないように） */
export const MIN_TYPING_MS = 1100;

const RATE_LIMITED_MESSAGE = "少し時間をおいてから送信してください";

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
  return getErrorMessage(error, "送信できませんでした。もう一度お試しください");
}

/**
 * DM の送信（POST /chat）。
 * 1. 楽観的に自分の吹き出しを出す（送信中は薄く）→ 返答が表示されるまで送信ボタンは無効
 * 2. 少し間をおいて「入力中…」→ 応答が来たら保存済みの自分の発言とキャラ返答をキャッシュへ
 *    （Realtime で先に届いていても id で重複排除される）
 * 3. 失敗したら吹き出しを「送信できませんでした・タップで再送」にしてトースト
 */
export function useSendMessage({
  conversationId,
  characterId,
  getLatestCreatedAt,
  onReplied,
}: UseSendMessageOptions): UseSendMessage {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [locals, setLocals] = useState<LocalMessage[]>([]);
  const [keyAliases, setKeyAliases] = useState<ReadonlyMap<string, string>>(() => new Map());
  const [pending, setPending] = useState<SendState | null>(null);
  const inFlightRef = useRef(false);
  const localsRef = useRef(locals);
  useEffect(() => {
    localsRef.current = locals;
  }, [locals]);
  const onRepliedRef = useRef(onReplied);
  useEffect(() => {
    onRepliedRef.current = onReplied;
  }, [onReplied]);

  const chat = useMutation({
    mutationFn: (message: string) =>
      api.sendChat({ character_id: characterId, conversation_id: conversationId, message }),
  });
  const { mutateAsync } = chat;

  const run = useCallback(
    async (local: LocalMessage) => {
      inFlightRef.current = true;
      setPending({ holdAfter: local.afterCreatedAt, typing: false });
      const startedAt = Date.now();
      const typingTimer = setTimeout(
        () => setPending((state) => (state ? { ...state, typing: true } : state)),
        TYPING_DELAY_MS,
      );
      try {
        const response = await mutateAsync(local.body);
        const cached = addMessagesToCache(queryClient, conversationId, [
          response.user_message,
          response.character_message,
        ]);
        setKeyAliases((previous) =>
          previous.get(response.user_message.id) === local.localId
            ? previous
            : new Map(previous).set(response.user_message.id, local.localId),
        );
        // キャッシュへ差し込めなかった（初回取得の完了待ち）場合は、保存済みの発言が表示されるまで
        // 楽観的な吹き出しを残す（届いたら mergeTimeline のエコー照合 → confirmLocals で片付く）
        if (cached) {
          setLocals((previous) => previous.filter((item) => item.localId !== local.localId));
        }
        // 即答でも「入力中…」を少し見せてから返答を出す
        const remaining = TYPING_DELAY_MS + MIN_TYPING_MS - (Date.now() - startedAt);
        if (remaining > 0) await sleep(remaining);
        clearTimeout(typingTimer);
        setPending(null);
        onRepliedRef.current?.(response);
      } catch (error) {
        clearTimeout(typingTimer);
        setPending(null);
        const message = sendErrorMessage(error);
        console.warn("[dm] send failed:", error);
        setLocals((previous) =>
          previous.map((item) =>
            item.localId === local.localId ? { ...item, status: "failed", error: message } : item,
          ),
        );
        toast.error(message);
      } finally {
        inFlightRef.current = false;
      }
    },
    [conversationId, mutateAsync, queryClient, toast],
  );

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
      void run(local);
    },
    [getLatestCreatedAt, run],
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
      void run(local);
    },
    [getLatestCreatedAt, run],
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
