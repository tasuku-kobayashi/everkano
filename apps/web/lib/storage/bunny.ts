import { clampInt, isAbsoluteUrl, type ImageTransformOptions, type StorageAdapter } from "./types";

/**
 * bunny ドライバー: オブジェクトキーを Bunny.net CDN（オリジン = Backblaze B2）のURLに変換する。
 *
 * - Bunny Optimizer のクエリパラメータ（width / quality / blur）でリサイズ・圧縮・ぼかしを行う。
 *   https://docs.bunny.net/docs/stream-image-processing （Bunny Optimizer の Dynamic Image API）
 * - mediaSigned=true（サーバーに BUNNY_TOKEN_AUTH_KEY がある）の場合は、同一オリジンの
 *   `/media/<key>?width=..` を返す。Route Handler（app/media/[...key]/route.ts）がトークン署名付き
 *   Bunny URL へ 302 リダイレクトする。署名キーはクライアントに一切渡らない。
 * - 絶対URL（http/https/data/blob）は常にそのまま返す。
 */

export interface BunnyAdapterConfig {
  /** 例: https://everkano.b-cdn.net（末尾スラッシュ不要） */
  cdnBaseUrl: string;
  /** true なら /media 経由（トークン認証） */
  mediaSigned: boolean;
  /** 署名ルートのパス（既定 /media） */
  mediaRoutePath?: string;
}

/** Bunny Optimizer の変換パラメータ（キー昇順。署名にも同じ順で使う） */
export function buildTransformParams(options: ImageTransformOptions = {}): Array<[string, string]> {
  const params: Array<[string, string]> = [];
  if (options.blur !== undefined && options.blur > 0) {
    params.push(["blur", String(clampInt(options.blur, 0, 100))]);
  }
  if (options.quality !== undefined) {
    params.push(["quality", String(clampInt(options.quality, 1, 100))]);
  }
  if (options.width !== undefined && options.width > 0) {
    params.push(["width", String(clampInt(options.width, 1, 4096))]);
  }
  return params.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
}

/** オブジェクトキーを正規化して、各セグメントを URL エンコードしたパス（先頭 / 付き）にする */
export function encodeObjectKey(key: string): string {
  const segments = key
    .replace(/^\/+/, "")
    .split("/")
    .filter((segment) => segment.length > 0);
  if (segments.some((segment) => segment === "." || segment === "..")) {
    throw new Error(`不正なオブジェクトキーです: ${key}`);
  }
  return `/${segments.map((segment) => encodeURIComponent(segment)).join("/")}`;
}

function toQuery(params: Array<[string, string]>): string {
  if (params.length === 0) return "";
  return `?${params.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&")}`;
}

export function createBunnyAdapter(config: BunnyAdapterConfig): StorageAdapter {
  const cdnBaseUrl = config.cdnBaseUrl.replace(/\/+$/, "");
  const mediaRoutePath = (config.mediaRoutePath ?? "/media").replace(/\/+$/, "");

  return {
    driver: "bunny",
    supportsTransforms: true,
    resolveImageUrl(src, options) {
      if (!src || isAbsoluteUrl(src)) return src;
      const path = encodeObjectKey(src);
      const query = toQuery(buildTransformParams(options));
      return config.mediaSigned
        ? `${mediaRoutePath}${path}${query}`
        : `${cdnBaseUrl}${path}${query}`;
    },
  };
}
