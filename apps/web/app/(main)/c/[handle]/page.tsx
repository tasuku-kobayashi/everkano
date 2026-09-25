import type { Metadata } from "next";
import { CharacterProfileView } from "@/components/profile/character-profile-view";

function decodeHandle(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ handle: string }>;
}): Promise<Metadata> {
  const { handle } = await params;
  return { title: `@${decodeHandle(handle)}` };
}

/** キャラクタープロフィール（B-4 / 仕様 §5.4） */
export default async function CharacterPage({ params }: { params: Promise<{ handle: string }> }) {
  const { handle } = await params;
  return <CharacterProfileView handle={handle} />;
}
