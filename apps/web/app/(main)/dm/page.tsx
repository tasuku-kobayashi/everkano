import type { Metadata } from "next";
import { DmInbox } from "@/components/dm/dm-inbox";

export const metadata: Metadata = { title: "メッセージ" };

/** DM 一覧（仕様 §5.5 / C-1） */
export default function DmListPage() {
  return <DmInbox />;
}
