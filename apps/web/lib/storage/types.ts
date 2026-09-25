/**
 * StorageAdapter — 画像URLの解決を抽象化するインターフェース（仕様 §2 / H4）。
 *
 * DB（characters.avatar_url / posts.image_url）には「絶対URL」または「オブジェクトキー」
 * （例: `posts/misaki/2026-09-25.jpg`）が入る。UI は必ずこのアダプタ経由で <img src> を得ること。
 * 実装は NEXT_PUBLIC_STORAGE_DRIVER で切り替える（lib/storage/index.ts）。
 */

export interface ImageTransformOptions {
  /** 目標の表示幅（px, 物理ピクセル）。CDN がリサイズする */
  width?: number;
  /** 画質 1〜100 */
  quality?: number;
  /** ぼかし強度 0〜100（有料投稿のプレビュー等）。CDN が対応する場合のみ効く */
  blur?: number;
}

export interface StorageAdapter {
  /** ドライバー名（デバッグ用） */
  readonly driver: "passthrough" | "bunny";
  /** 変換パラメータ（width 等）が実際に効くか。false なら srcSet を作っても同一URLになる */
  readonly supportsTransforms: boolean;
  /** 表示用の画像URLを返す。絶対URL（http/https/data/blob）は常にそのまま返す */
  resolveImageUrl(src: string, options?: ImageTransformOptions): string;
}

/** http(s):, data:, blob: の絶対URLか */
export function isAbsoluteUrl(src: string): boolean {
  return /^(?:https?:)?\/\//i.test(src) || /^(?:data|blob):/i.test(src);
}

/** 0 以上の整数に丸める。範囲外は clamp */
export function clampInt(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)));
}
