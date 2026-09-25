import { PostDetailHeader, PostDetailSkeleton } from "@/components/post/post-detail-view";

export default function PostLoading() {
  return (
    <>
      <PostDetailHeader />
      <PostDetailSkeleton />
    </>
  );
}
