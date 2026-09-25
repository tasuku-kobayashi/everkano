#!/usr/bin/env node
/**
 * PWA / ファビコン用アイコンを生成する（依存パッケージなし。Node 組み込みの zlib だけで PNG を書き出す）。
 *
 *   pnpm --filter @everkano/web icons
 *
 * 出力（public/ 配下。生成物はアプリアイコンなのでコミットする）:
 *   icons/icon-192.png, icons/icon-512.png      … 角丸（周囲は透明）。manifest purpose=any
 *   icons/icon-maskable-512.png                 … 全面塗り + セーフゾーン内にマーク。purpose=maskable
 *   icons/apple-touch-icon.png (180)            … 全面塗り（iOS が角丸マスクをかける）
 *   icons/favicon-32.png, favicon.ico (16/32/48)
 *
 * デザイン: ピーチ → ピンク → バイオレットの斜めグラデーションに白いハート。
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";

const PUBLIC_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "public");

// ---------------------------------------------------------------------------
// PNG エンコーダー（RGBA 8bit）
// ---------------------------------------------------------------------------
const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const typeAndData = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(typeAndData));
  return Buffer.concat([length, typeAndData, crc]);
}

function encodePng(width, height, rgba) {
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // color type RGBA
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0; // filter: none
    rgba.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride);
  }
  return Buffer.concat([
    signature,
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ---------------------------------------------------------------------------
// 描画
// ---------------------------------------------------------------------------
const STOPS = [
  [0, [0xff, 0xb3, 0x6b]], // peach
  [0.5, [0xff, 0x4f, 0x7b]], // pink
  [1, [0x9b, 0x4d, 0xff]], // violet
];

function gradientAt(t) {
  const clamped = Math.min(1, Math.max(0, t));
  for (let i = 1; i < STOPS.length; i++) {
    const [t1, c1] = STOPS[i];
    const [t0, c0] = STOPS[i - 1];
    if (clamped <= t1) {
      const f = (clamped - t0) / (t1 - t0);
      return c0.map((v, k) => v + (c1[k] - v) * f);
    }
  }
  return STOPS[STOPS.length - 1][1];
}

/** 角丸矩形の内側か（u, v は 0..1、r は半径の比率） */
function insideRoundedRect(u, v, r) {
  if (r <= 0) return u >= 0 && u <= 1 && v >= 0 && v <= 1;
  const cx = Math.min(Math.max(u, r), 1 - r);
  const cy = Math.min(Math.max(v, r), 1 - r);
  return (u - cx) ** 2 + (v - cy) ** 2 <= r * r && u >= 0 && u <= 1 && v >= 0 && v <= 1;
}

/** ハート曲線 (x²+y²−1)³ − x²y³ ≤ 0 の内側か（x, y はハート座標。y は上向き） */
function insideHeart(x, y) {
  const a = x * x + y * y - 1;
  return a * a * a - x * x * y * y * y <= 0;
}

/**
 * @param size 出力サイズ(px)
 * @param cornerRatio 角丸半径の比率（0 = 四角・全面塗り）
 * @param heartWidthRatio ハートの幅（アイコン幅に対する比率）
 */
function render(size, { cornerRatio, heartWidthRatio }) {
  const rgba = Buffer.alloc(size * size * 4);
  const SS = 4; // 4x4 スーパーサンプリングでアンチエイリアス
  // ハート曲線の幅は約 2.28、高さは約 2.25（y: -1.0 〜 1.25）。中心を合わせるため y を 0.12 ずらす
  const heartScale = 2.28 / (heartWidthRatio * size);
  for (let py = 0; py < size; py++) {
    for (let px = 0; px < size; px++) {
      let bgCoverage = 0;
      let heartCoverage = 0;
      for (let sy = 0; sy < SS; sy++) {
        for (let sx = 0; sx < SS; sx++) {
          const x = px + (sx + 0.5) / SS;
          const y = py + (sy + 0.5) / SS;
          const u = x / size;
          const v = y / size;
          if (!insideRoundedRect(u, v, cornerRatio)) continue;
          bgCoverage++;
          const hx = (x - size / 2) * heartScale;
          const hy = -(y - size / 2) * heartScale + 0.12;
          if (insideHeart(hx, hy)) heartCoverage++;
        }
      }
      const total = SS * SS;
      const alpha = bgCoverage / total;
      const heart = bgCoverage === 0 ? 0 : heartCoverage / bgCoverage;
      // 左下 → 右上 の斜めグラデーション
      const t = (px / size + (1 - py / size)) / 2;
      const [r, g, b] = gradientAt(t);
      const idx = (py * size + px) * 4;
      rgba[idx] = Math.round(r + (255 - r) * heart);
      rgba[idx + 1] = Math.round(g + (255 - g) * heart);
      rgba[idx + 2] = Math.round(b + (255 - b) * heart);
      rgba[idx + 3] = Math.round(alpha * 255);
    }
  }
  return encodePng(size, size, rgba);
}

/** PNG を格納した ICO（Vista 以降の全ブラウザが対応） */
function encodeIco(pngs) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(pngs.length, 4);
  const entries = [];
  let offset = 6 + 16 * pngs.length;
  for (const { size, data } of pngs) {
    const entry = Buffer.alloc(16);
    entry[0] = size >= 256 ? 0 : size;
    entry[1] = size >= 256 ? 0 : size;
    entry[2] = 0;
    entry[3] = 0;
    entry.writeUInt16LE(1, 4);
    entry.writeUInt16LE(32, 6);
    entry.writeUInt32LE(data.length, 8);
    entry.writeUInt32LE(offset, 12);
    offset += data.length;
    entries.push(entry);
  }
  return Buffer.concat([header, ...entries, ...pngs.map((p) => p.data)]);
}

function write(relativePath, data) {
  const path = join(PUBLIC_DIR, relativePath);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, data);
  console.log(`wrote ${relativePath} (${data.length} bytes)`);
}

const ROUNDED = { cornerRatio: 0.225, heartWidthRatio: 0.5 };
const FULL = { cornerRatio: 0, heartWidthRatio: 0.5 };
// maskable はセーフゾーン（中心から半径 40%）に収める
const MASKABLE = { cornerRatio: 0, heartWidthRatio: 0.4 };

write("icons/icon-192.png", render(192, ROUNDED));
write("icons/icon-512.png", render(512, ROUNDED));
write("icons/icon-maskable-512.png", render(512, MASKABLE));
write("icons/apple-touch-icon.png", render(180, FULL));
write("icons/favicon-32.png", render(32, ROUNDED));
write(
  "favicon.ico",
  encodeIco(
    [16, 32, 48].map((size) => ({
      size,
      data: render(size, { ...ROUNDED, heartWidthRatio: 0.56 }),
    })),
  ),
);
