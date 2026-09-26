"use client";

import type { MessageDTO, PublicCharacter } from "@everkano/shared";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MemoryPanel } from "@/components/memory/memory-panel";
import { buttonClassName } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { BookmarkIcon, ChevronDownIcon, UserIcon } from "@/components/ui/icons";
import { InfiniteScrollSentinel } from "@/components/ui/infinite-scroll-sentinel";
import { Skeleton } from "@/components/ui/skeleton";
import { useBottomBarHeight } from "@/components/ui/toast";
import { getErrorMessage, isApiError } from "@/lib/api/errors";
import { cn } from "@/lib/cn";
import { useConversation, useDmCharacter, useMarkConversationRead } from "@/lib/queries/dm";
import { useMemoryCreatedRealtime } from "@/lib/queries/memories";
import {
  flattenMessagesAsc,
  mergeTimeline,
  useMessages,
  useMessagesRealtime,
} from "@/lib/queries/messages";
import { useSafetyResources } from "@/lib/queries/safety";
import { ConversationIntro } from "./conversation-intro";
import { DmHeader } from "./dm-header";
import { MessageBubble } from "./message-bubble";
import { MessageComposer } from "./message-composer";
import { SafetyResourceCard } from "./safety-resource-card";
import {
  chatStreamStore,
  markDeliveredWhileStreaming,
  streamingTimelineItem,
  useChatStream,
} from "./stream-state";
import { buildTimelineRows, holdCharacterReplies, isUuid } from "./timeline";
import { TypingIndicator } from "./typing-indicator";
import { useChatScroll } from "./use-chat-scroll";
import { useSendMessage } from "./use-send-message";

/** 入力欄の高さの初期値（safe-area を除く） */
const DEFAULT_FOOTER_PX = 56;

export interface DmConversationProps {
  characterId: string;
}

/**
 * DM 会話画面（/dm/[characterId]、仕様 §5.6 / C-2 / エンジン v1.0）。
 * 開いたら会話を取得（既存の会話は DM 一覧のキャッシュ・直接参照で開き、無ければ POST /conversations で作成。
 * 初回はキャラの挨拶が届く）し、メッセージを表示・送信する。ヘッダー右の「i」でメモリパネル。
 * - 返答は POST /chat/stream で届いた分から順に表示する（use-send-message.ts）
 * - キャラからの自発メッセージ（is_proactive）は通常の吹き出しと同じに表示する（P6。特別な演出で返信を促さない）
 * - 安全対応をした返答（messages.safety_triggered）の下には相談窓口のカード（E6。履歴・別の端末でも出る）
 * - 会話から新しく覚えたこと（返答の後に非同期で作られる）は Realtime で届き、「〇〇があなたのことを覚えました」を出す
 */
export function DmConversation({ characterId }: DmConversationProps) {
  const valid = isUuid(characterId);
  const characterQuery = useDmCharacter(characterId, valid);
  const conversationQuery = useConversation(characterId, valid);
  const [memoryOpen, setMemoryOpen] = useState(false);

  const notFound =
    !valid ||
    (isApiError(conversationQuery.error) && conversationQuery.error.status === 404) ||
    (characterQuery.isSuccess && !characterQuery.isPlaceholderData && characterQuery.data === null);

  const character: PublicCharacter | null | undefined = notFound
    ? null
    : (characterQuery.data ?? undefined);
  const conversation = conversationQuery.data;

  return (
    <>
      <DmHeader
        character={character}
        onOpenInfo={conversation && character ? () => setMemoryOpen(true) : undefined}
      />
      {notFound ? (
        <EmptyState
          icon={<UserIcon size={32} strokeWidth={1.6} />}
          title="アカウントが見つかりません"
          description="リンクが正しくないか、キャラクターが公開を終了した可能性があります。"
          action={
            <Link href="/dm" className={buttonClassName({ variant: "primary" })}>
              メッセージ一覧へ
            </Link>
          }
          className="flex-1 justify-center"
        />
      ) : conversationQuery.isError && !conversation ? (
        <ErrorState
          message={getErrorMessage(conversationQuery.error)}
          onRetry={() => void conversationQuery.refetch()}
          retrying={conversationQuery.isRefetching}
          className="flex-1 justify-center"
        />
      ) : !conversation ? (
        <ConversationSkeleton />
      ) : (
        <ConversationBody
          conversationId={conversation.id}
          characterId={characterId}
          character={character ?? null}
          onOpenMemory={() => setMemoryOpen(true)}
        />
      )}
      {conversation && character ? (
        <MemoryPanel
          open={memoryOpen}
          onClose={() => setMemoryOpen(false)}
          characterId={characterId}
          characterName={character.name}
        />
      ) : null}
    </>
  );
}

interface ConversationBodyProps {
  conversationId: string;
  characterId: string;
  character: PublicCharacter | null;
  onOpenMemory: () => void;
}

function ConversationBody({
  conversationId,
  characterId,
  character,
  onOpenMemory,
}: ConversationBodyProps) {
  const messagesQuery = useMessages(conversationId);
  // 分割代入すると判別共用体の絞り込みで fetchNextPage が never になるため、必要な値だけ取り出す
  const data = messagesQuery.data;
  // isSuccess は再取得の失敗で false になる（data は残る）ため、表示可否は data の有無で判断する
  const hasData = data !== undefined;
  const hasNextPage = messagesQuery.hasNextPage;
  const isFetchingNextPage = messagesQuery.isFetchingNextPage;
  const isFetchNextPageError = messagesQuery.isFetchNextPageError;
  const { fetchNextPage, refetch } = messagesQuery;
  const loadOlder = useCallback(() => void fetchNextPage(), [fetchNextPage]);
  const serverAsc = useMemo(() => flattenMessagesAsc(data), [data]);
  const characterName = character?.name ?? "";

  const latestCreatedAtRef = useRef<string | null>(null);
  useEffect(() => {
    latestCreatedAtRef.current = serverAsc[serverAsc.length - 1]?.created_at ?? null;
  }, [serverAsc]);
  const getLatestCreatedAt = useCallback(() => latestCreatedAtRef.current, []);

  // ---- 既読（開いたとき・表示中にキャラの発言が届いたとき・画面に戻ったとき）
  const markRead = useMarkConversationRead(conversationId);
  const markReadIfVisible = useCallback(() => {
    if (document.visibilityState === "visible") markRead();
  }, [markRead]);
  useEffect(() => {
    if (hasData) markReadIfVisible();
  }, [hasData, markReadIfVisible]);
  useEffect(() => {
    document.addEventListener("visibilitychange", markReadIfVisible);
    return () => document.removeEventListener("visibilitychange", markReadIfVisible);
  }, [markReadIfVisible]);

  // 返答待ちの間に届いたキャラの発言（= その返答）は、返答を表示し終えたときに 1 回だけ既読にする
  // （Realtime の到着時と返答の表示時の両方で既読にすると、1 往復で mark_conversation_read が 2 回走る）
  const sendingRef = useRef(false);
  const readAfterSendRef = useRef(false);
  const onRealtimeInsert = useCallback(
    (message: MessageDTO) => {
      if (message.sender_type !== "character") return;
      if (sendingRef.current) readAfterSendRef.current = true;
      else markReadIfVisible();
    },
    [markReadIfVisible],
  );
  useMessagesRealtime(conversationId, onRealtimeInsert);

  // ---- 送信
  const onReplied = useCallback(() => {
    // 既読化は返答待ちが終わったとき（下の effect）。DM 一覧・未読バッジの更新は送信側
    // （useSendMessage のミューテーション）で行う
    readAfterSendRef.current = true;
  }, []);
  const sender = useSendMessage({
    conversationId,
    characterId,
    getLatestCreatedAt,
    onReplied,
  });
  const { confirmLocals } = sender;
  const sending = sender.pending !== null;
  useEffect(() => {
    sendingRef.current = sending;
    if (!sending && readAfterSendRef.current) {
      readAfterSendRef.current = false;
      markReadIfVisible();
    }
  }, [sending, markReadIfVisible]);

  const { items, confirmed } = useMemo(
    () => mergeTimeline(serverAsc, sender.locals, sender.keyAliases),
    [serverAsc, sender.locals, sender.keyAliases],
  );
  useEffect(() => {
    confirmLocals(confirmed);
  }, [confirmed, confirmLocals]);

  // ---- 受信中のキャラの返答（/chat/stream の delta を順に表示する吹き出し）
  const stream = useChatStream(conversationId);
  const savedIds = useMemo(() => new Set(serverAsc.map((message) => message.id)), [serverAsc]);
  const activeLocalId = sender.pending?.localId ?? null;
  const streamItem = useMemo(
    () =>
      streamingTimelineItem(stream, {
        activeLocalId,
        revealed: Boolean(sender.pending?.revealed),
        savedIds,
      }),
    [stream, activeLocalId, sender.pending?.revealed, savedIds],
  );
  // 表示を終えて保存済みの返答に置き換わった受信中の状態は片付ける
  useEffect(() => {
    if (
      stream &&
      stream.localId !== activeLocalId &&
      stream.messageId &&
      savedIds.has(stream.messageId)
    ) {
      chatStreamStore.clear(conversationId, stream.localId);
    }
  }, [stream, activeLocalId, savedIds, conversationId]);

  const visibleItems = useMemo(() => {
    // 返答待ちの間は、送信時点より新しいキャラの発言（Realtime で先に届いた返答）を隠す
    const held = sender.pending ? holdCharacterReplies(items, sender.pending.holdAfter) : items;
    if (!streamItem) return held;
    // 返答が届き始めたら、自分の発言は（まだ保存前でも）送信済みの見た目にする
    return [...markDeliveredWhileStreaming(held, activeLocalId), streamItem];
  }, [items, sender.pending, streamItem, activeLocalId]);
  const rows = useMemo(() => buildTimelineRows(visibleItems), [visibleItems]);
  // 「入力中…」は最初の文字が表示されるまで
  const typing = Boolean(sender.pending?.typing) && streamItem === null;

  // ---- E6: 安全対応をした返答（messages.safety_triggered）があれば相談窓口の一覧を用意する
  const hasSafetyReply = useMemo(
    () => visibleItems.some((item) => item.senderType === "character" && item.safetyTriggered),
    [visibleItems],
  );
  const safetyResources = useSafetyResources(hasSafetyReply);

  // ---- 「〇〇があなたのことを覚えました」（記憶は返答の後に非同期で作られ、Realtime で届く）。
  // 届いた時点の最新の吹き出しの下に出す
  const [memoryNotices, setMemoryNotices] = useState<ReadonlyMap<string, number>>(() => new Map());
  const newestKeyRef = useRef<string | null>(null);
  useEffect(() => {
    newestKeyRef.current = visibleItems[visibleItems.length - 1]?.key ?? null;
  }, [visibleItems]);
  const onMemoriesCreated = useCallback((count: number) => {
    const anchor = newestKeyRef.current;
    if (!anchor) return;
    setMemoryNotices((previous) =>
      new Map(previous).set(anchor, (previous.get(anchor) ?? 0) + count),
    );
  }, []);
  useMemoryCreatedRealtime(characterId, onMemoriesCreated);
  // 返答待ちの間と、履歴の初回取得が終わるまで（読み込み中・読み込み失敗）は送信できない。
  // 履歴が無いうちに送ると、送った発言と返答を差し込むキャッシュが無く、表示から消えてしまう
  const sendDisabled = sending || !hasData;

  // ---- 入力欄の高さ（本文の下余白・トースト位置）
  const [footerHeight, setFooterHeight] = useState(DEFAULT_FOOTER_PX);
  useBottomBarHeight(footerHeight);

  // ---- スクロール
  const newest = visibleItems[visibleItems.length - 1];
  // 自分の最後の発言が末尾にあり保存済みなら「既読」（キャラは届いた瞬間に読む）
  const seenKey =
    newest && newest.senderType === "user" && newest.status === "sent" ? newest.key : null;
  const scroll = useChatScroll({
    ready: hasData,
    newestKey: newest?.key ?? null,
    newestIsOwn: newest?.senderType === "user",
    typing,
    bottomInset: footerHeight,
    // 送信失敗の表示・「既読」・受信中の本文・相談窓口・メモリ通知で末尾の高さが変わったときも最下部を保つ
    tailSignature: `${newest?.status ?? ""}|${seenKey ?? ""}|${streamItem?.body.length ?? 0}|${
      newest?.safetyTriggered ? (safetyResources.resources?.length ?? -1) : 0
    }|${memoryNotices.size}`,
  });

  return (
    <>
      <div
        className="flex flex-1 flex-col justify-end"
        style={{ paddingBottom: `calc(${footerHeight}px + env(safe-area-inset-bottom) + 8px)` }}
      >
        {messagesQuery.isError && !hasData ? (
          <ErrorState
            message={getErrorMessage(messagesQuery.error)}
            onRetry={() => void refetch()}
            retrying={messagesQuery.isRefetching}
            className="my-auto"
          />
        ) : !hasData ? (
          <MessagesSkeleton />
        ) : (
          <div role="log" aria-label={`${characterName}とのメッセージ`}>
            {hasNextPage ? (
              isFetchNextPageError ? (
                <ErrorState
                  compact
                  message="過去のメッセージを読み込めませんでした"
                  onRetry={loadOlder}
                />
              ) : (
                <InfiniteScrollSentinel
                  onLoadMore={loadOlder}
                  hasMore={Boolean(hasNextPage)}
                  loading={isFetchingNextPage}
                  disabled={!scroll.initialScrollDone}
                  rootMargin="400px 0px 0px 0px"
                />
              )
            ) : (
              <ConversationIntro character={character} />
            )}

            {rows.map((row) =>
              row.kind === "separator" ? (
                <div
                  key={row.key}
                  data-row-key={row.key}
                  className="pt-4 pb-2 text-center text-[12px] leading-4 font-semibold text-ig-secondary"
                >
                  {row.label}
                </div>
              ) : (
                <div key={row.key} data-row-key={row.key}>
                  <MessageBubble
                    message={row.message}
                    isFirstInGroup={row.isFirstInGroup}
                    isLastInGroup={row.isLastInGroup}
                    characterAvatarUrl={character?.avatar_url}
                    characterName={characterName}
                    onRetry={sender.retry}
                    retryDisabled={sendDisabled}
                  />
                  {row.key === seenKey ? (
                    <p className="mt-1 pr-4 text-right text-[12px] leading-4 text-ig-secondary">
                      既読
                    </p>
                  ) : null}
                  {row.message.senderType === "character" && row.message.safetyTriggered ? (
                    <SafetyResourceCard
                      resources={safetyResources.resources}
                      loading={safetyResources.loading}
                      error={safetyResources.error}
                      onRetry={safetyResources.retry}
                    />
                  ) : null}
                  {memoryNotices.has(row.key) ? (
                    <MemoryNotice name={characterName} onOpen={onOpenMemory} />
                  ) : null}
                </div>
              ),
            )}

            {typing ? (
              <TypingIndicator avatarUrl={character?.avatar_url} name={characterName} />
            ) : null}
          </div>
        )}
      </div>

      {scroll.hasUnseenNew ? (
        <div
          className="pointer-events-none fixed inset-x-0 z-20 mx-auto flex max-w-[480px] justify-center"
          style={{ bottom: `calc(${footerHeight}px + env(safe-area-inset-bottom) + 10px)` }}
        >
          <button
            type="button"
            onClick={() => scroll.scrollToBottom("smooth")}
            className="pointer-events-auto flex h-9 animate-fade-in items-center gap-1 rounded-full bg-ig-sheet px-4 text-[14px] font-semibold text-ig-blue-text shadow-[0_2px_12px_rgb(0_0_0/0.18)]"
          >
            新しいメッセージ
            <ChevronDownIcon size={16} strokeWidth={2.4} />
          </button>
        </div>
      ) : null}

      <MessageComposer
        onSend={sender.send}
        sendDisabled={sendDisabled}
        onHeightChange={setFooterHeight}
      />
    </>
  );
}

/** 「〇〇があなたのことを覚えました」（返答で新しい記憶が作られたとき。タップでメモリパネル） */
function MemoryNotice({ name, onOpen }: { name: string; onOpen: () => void }) {
  return (
    <div className="flex animate-fade-in justify-center px-6 pt-2 pb-1">
      <button
        type="button"
        onClick={onOpen}
        className="flex items-center gap-1 text-[12px] leading-4 text-ig-secondary pressable"
      >
        <BookmarkIcon size={13} strokeWidth={2.2} />
        <span>{name}があなたのことを覚えました</span>
        <span className="font-semibold text-ig-blue-text">・見る</span>
      </button>
    </div>
  );
}

function MessagesSkeleton() {
  const widths = ["w-40", "w-56", "w-32", "w-48", "w-28", "w-52"];
  return (
    <div className="space-y-3 px-3 pb-2" aria-busy="true" aria-label="読み込み中">
      {widths.map((width, index) => {
        const own = index % 3 === 1;
        return (
          <div key={width} className={cn("flex items-end gap-2", own && "justify-end")}>
            {own ? null : <Skeleton shape="circle" className="size-7" />}
            <Skeleton className={cn("h-9 rounded-[22px]", width)} />
          </div>
        );
      })}
    </div>
  );
}

function ConversationSkeleton() {
  return (
    <div className="flex flex-1 flex-col justify-end pb-[72px]">
      <div className="flex flex-col items-center gap-2 pt-8 pb-6" aria-hidden="true">
        <Skeleton shape="circle" className="size-24" />
        <Skeleton shape="text" className="mt-2 h-4 w-24" />
        <Skeleton shape="text" className="w-32" />
      </div>
      <MessagesSkeleton />
    </div>
  );
}
