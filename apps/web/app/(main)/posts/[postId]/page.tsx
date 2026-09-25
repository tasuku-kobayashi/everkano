import type { Metadata } from "next";
import { PostDetailView } from "@/components/post/post-detail-view";

export const metadata: Metadata = { title: "投稿" };

/** 個別投稿（B-2 / B-3 / 仕様 §5.3）。データはブラウザから Supabase へ直接取得する（RLS 適用） */
export default async function PostPage({ params }: { params: Promise<{ postId: string }> }) {
  const { postId } = await params;
  return <PostDetailView postId={postId} />;
}
