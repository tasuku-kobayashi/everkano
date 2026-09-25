"use client";

import { LockIcon } from "@/components/ui/icons";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";

export interface PaidLockModalProps {
  open: boolean;
  onClose: () => void;
  /** 価格（トークン） */
  priceTokens: number;
  /** 投稿者キャラの名前（説明文に使う） */
  characterName?: string;
}

/** 価格表示（例: 「120 tokens」「1,200 tokens」） */
export function formatPriceTokens(priceTokens: number): string {
  return `${Math.max(0, Math.floor(priceTokens)).toLocaleString("ja-JP")} tokens`;
}

/**
 * 有料投稿のロックモーダル（仕様 §5.4）。
 * H3: 決済機能は実装しない。「購入する（準備中）」はトーストを出すだけで、購入処理・遷移は一切行わない。
 */
export function PaidLockModal({ open, onClose, priceTokens, characterName }: PaidLockModalProps) {
  const toast = useToast();
  return (
    <Modal
      open={open}
      onClose={onClose}
      icon={
        <span className="border-ig-text flex size-[62px] items-center justify-center rounded-full border-2">
          <LockIcon size={28} strokeWidth={1.7} />
        </span>
      }
      title="この投稿は有料コンテンツです"
      description={
        characterName
          ? `${characterName}さんの限定公開の投稿です。購入すると全体を見られるようになります。`
          : "購入すると全体を見られるようになります。"
      }
      actions={[
        {
          label: "購入する（準備中）",
          variant: "primary",
          onClick: () => toast.show("課金機能は現在準備中です"),
        },
        { label: "閉じる", onClick: onClose },
      ]}
    >
      <p className="text-[22px] leading-7 font-bold tracking-tight" data-testid="paid-price">
        {formatPriceTokens(priceTokens)}
      </p>
    </Modal>
  );
}
