import { NextResponse, type NextRequest } from "next/server";
import { getPublicEnv } from "@/lib/env";
import { getServerEnv } from "@/lib/env.server";
import { buildTransformParams, encodeObjectKey } from "@/lib/storage/bunny";
import { computeExpires, signBunnyUrl } from "@/lib/storage/bunny-token";
import { createSupabaseServerClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

/**
 * GET /media/<object key>?width=&quality=&blur=
 *
 * Bunny.net Token Authentication 用の署名付き URL へ 302 リダイレクトする（サーバー専用キーで署名）。
 * - BUNNY_TOKEN_AUTH_KEY が未設定なら 404（その場合 StorageAdapter は CDN の URL を直接使う）
 * - ログインユーザーのみ（未ログインは 401）
 * - 変換パラメータは width / quality / blur のみ受け付け、署名対象に含める
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ key: string[] }> },
) {
  const serverEnv = getServerEnv();
  if (!serverEnv.bunnyTokenAuthKey) {
    return new NextResponse(null, { status: 404 });
  }
  const cdnBaseUrl = getPublicEnv().cdnBaseUrl;
  if (!cdnBaseUrl) {
    console.error("[media] BUNNY_TOKEN_AUTH_KEY is set but NEXT_PUBLIC_CDN_BASE_URL is empty");
    return new NextResponse(null, { status: 500 });
  }

  const supabase = await createSupabaseServerClient();
  const { data: claims } = await supabase.auth.getClaims();
  if (!claims?.claims?.sub) {
    return new NextResponse(null, { status: 401 });
  }

  const { key } = await params;
  let path: string;
  try {
    // Next.js はセグメントをデコード済みで渡すため、再エンコードして署名する
    path = encodeObjectKey(key.join("/"));
  } catch {
    return new NextResponse(null, { status: 400 });
  }

  const query = request.nextUrl.searchParams;
  const toNumber = (name: string) => {
    const raw = query.get(name);
    if (raw === null || !/^\d+$/.test(raw)) return undefined;
    return Number.parseInt(raw, 10);
  };
  const transform = buildTransformParams({
    width: toNumber("width"),
    quality: toNumber("quality"),
    blur: toNumber("blur"),
  });

  const ttl = serverEnv.bunnyTokenTtlSeconds;
  const expires = computeExpires(Date.now(), ttl);
  const signed = signBunnyUrl({
    cdnBaseUrl,
    path,
    securityKey: serverEnv.bunnyTokenAuthKey,
    expires,
    params: transform,
  });

  const response = NextResponse.redirect(signed, 302);
  // 署名 URL は失効より前に使い終わるよう、ブラウザには短めにキャッシュさせる
  response.headers.set("Cache-Control", `private, max-age=${Math.min(300, Math.floor(ttl / 2))}`);
  return response;
}
