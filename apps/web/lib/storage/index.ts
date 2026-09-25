import { getPublicEnv } from "@/lib/env";
import { createBunnyAdapter } from "./bunny";
import { createPassthroughAdapter } from "./passthrough";
import type { ImageTransformOptions, StorageAdapter } from "./types";

export type { ImageTransformOptions, StorageAdapter } from "./types";
export { isAbsoluteUrl } from "./types";
export { createBunnyAdapter } from "./bunny";
export { createPassthroughAdapter } from "./passthrough";

/** <CdnImage> の srcSet に使う幅（px） */
export const IMAGE_WIDTHS = [320, 640, 1080] as const;

let adapter: StorageAdapter | undefined;

/** NEXT_PUBLIC_STORAGE_DRIVER に応じた StorageAdapter（シングルトン） */
export function getStorageAdapter(): StorageAdapter {
  if (!adapter) {
    const env = getPublicEnv();
    adapter =
      env.storageDriver === "bunny" && env.cdnBaseUrl
        ? createBunnyAdapter({ cdnBaseUrl: env.cdnBaseUrl, mediaSigned: env.mediaSigned })
        : createPassthroughAdapter();
  }
  return adapter;
}

/** 画像URLを解決する（getStorageAdapter().resolveImageUrl のショートカット） */
export function resolveImageUrl(src: string, options?: ImageTransformOptions): string {
  return getStorageAdapter().resolveImageUrl(src, options);
}

/**
 * srcSet 文字列を作る。変換非対応のドライバー（passthrough）では undefined を返す
 * （同一URLを並べても意味がないため）。
 */
export function buildSrcSet(
  src: string,
  widths: readonly number[] = IMAGE_WIDTHS,
  options: Omit<ImageTransformOptions, "width"> = {},
  storage: StorageAdapter = getStorageAdapter(),
): string | undefined {
  if (!storage.supportsTransforms) return undefined;
  const entries = widths.map(
    (w) => `${storage.resolveImageUrl(src, { ...options, width: w })} ${w}w`,
  );
  const unique = new Set(entries.map((e) => e.split(" ")[0]));
  return unique.size > 1 ? entries.join(", ") : undefined;
}
