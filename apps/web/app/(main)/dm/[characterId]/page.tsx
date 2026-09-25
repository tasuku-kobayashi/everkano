import type { Metadata } from "next";
import { DmConversation } from "@/components/dm/dm-conversation";

export const metadata: Metadata = { title: "メッセージ" };

/** DM 会話（仕様 §5.6 / C-2。ヘッダーの「i」でメモリパネル = C-6） */
export default async function DmConversationPage({
  params,
}: {
  params: Promise<{ characterId: string }>;
}) {
  const { characterId } = await params;
  // key: 別のキャラの DM へ直接遷移したときに状態（送信中・スクロール位置）を持ち越さない
  return <DmConversation key={characterId} characterId={characterId} />;
}
