import { Avatar } from "@/components/ui/avatar";
import { AlertIcon } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import type { TimelineMessage } from "@/lib/queries/messages";
import { bubbleRadiusClass, isEmojiOnly } from "./timeline";

/**
 * 自分の吹き出しの背景。background-attachment: fixed でグラデーションを画面に固定し、
 * 画面の上にある吹き出しほど紫・下ほど青になる（Instagram と同じ見え方）。
 * iOS Safari は fixed を無視するため、吹き出しごとに紫 → 青のグラデーションになる。
 */
export const SENT_BUBBLE_CLASS =
  "bg-fixed bg-[linear-gradient(180deg,#a033ff_0%,#7a4bff_40%,#4a6cff_70%,#1f8cff_100%)] text-white";

export interface MessageBubbleProps {
  message: TimelineMessage;
  isFirstInGroup: boolean;
  isLastInGroup: boolean;
  /** キャラのアバター（グループ最後の吹き出しの横に出す） */
  characterAvatarUrl: string | null | undefined;
  characterName: string;
  /** 送信失敗した吹き出しをタップしたとき（再送） */
  onRetry?: (localId: string) => void;
  /** 再送できない状態（別の送信中） */
  retryDisabled?: boolean;
}

/**
 * DM の吹き出し（Instagram 準拠）。
 * - 自分: 右寄せ・青紫のグラデーション・白文字（グラデーションは画面に固定され、上ほど紫・下ほど青）
 * - キャラ: 左寄せ・グレー（ダーク #262626）。グループ最後の吹き出しの横にアバター
 * - 絵文字だけの短いメッセージは吹き出し無しで大きく
 * - 送信中は薄く、送信失敗は赤い「!」と「送信できませんでした・タップで再送」
 * - 受信中（ストリーミング）のキャラの返答は通常の吹き出しに順に追記される（aria-busy）
 * - キャラからの自発メッセージも通常の吹き出し（返信を急かすような特別な表示はしない。P4 / P6）
 * - スクリーンリーダー向けに、吹き出しごとに送り手（「あなた: 」/「<キャラ名>: 」）を読み上げる
 *   （会話ログ role="log" の中でユーザーとキャラの発言が区別できるように。アバターは装飾扱い）
 */
export function MessageBubble({
  message,
  isFirstInGroup,
  isLastInGroup,
  characterAvatarUrl,
  characterName,
  onRetry,
  retryDisabled = false,
}: MessageBubbleProps) {
  const own = message.senderType === "user";
  const emojiOnly = isEmojiOnly(message.body);
  const failed = message.status === "failed";
  const sending = message.status === "sending";
  const streaming = message.status === "streaming";

  const bubble = (
    <div
      className={cn(
        "max-w-[75%] text-[15px] leading-5 text-wrap-anywhere whitespace-pre-wrap",
        emojiOnly
          ? "px-1 py-0.5 text-[40px] leading-[48px]"
          : cn(
              "px-3 py-2",
              bubbleRadiusClass(message.senderType, isFirstInGroup, isLastInGroup),
              own ? SENT_BUBBLE_CLASS : "bg-ig-elevated text-ig-text",
            ),
        sending && "opacity-60",
        failed && "opacity-70",
      )}
    >
      <span className="sr-only">{`${own ? "あなた" : characterName || "相手"}: `}</span>
      <span>{message.body}</span>
    </div>
  );

  if (own) {
    const content = (
      <div className="flex items-center justify-end gap-2">
        {failed ? (
          <span className="shrink-0 text-ig-red" aria-hidden="true">
            <AlertIcon size={20} />
          </span>
        ) : null}
        {bubble}
      </div>
    );
    return (
      <div
        className={cn("pr-3 pl-16", isFirstInGroup ? "mt-2" : "mt-0.5")}
        data-status={message.status}
      >
        {failed && message.localId ? (
          <button
            type="button"
            disabled={retryDisabled}
            onClick={() => onRetry?.(message.localId!)}
            aria-label={`送信できませんでした。タップで再送: ${message.body}`}
            className="block w-full text-left pressable disabled:cursor-default"
          >
            {content}
            <p className="mt-1 text-right text-[12px] leading-4 text-ig-red-text">
              送信できませんでした・タップで再送
            </p>
          </button>
        ) : (
          <>
            {content}
            {sending ? <span className="sr-only">送信中</span> : null}
          </>
        )}
      </div>
    );
  }

  return (
    <div
      className={cn("flex items-end gap-2 pr-16 pl-3", isFirstInGroup ? "mt-2" : "mt-0.5")}
      data-status={message.status}
      // 受信中（/chat/stream の途中）の吹き出しは、届くたびに読み上げが繰り返されないよう完了まで busy にする
      aria-busy={streaming || undefined}
    >
      <div className="w-7 shrink-0" aria-hidden="true">
        {isLastInGroup ? <Avatar src={characterAvatarUrl} alt={characterName} size={28} /> : null}
      </div>
      {bubble}
    </div>
  );
}
