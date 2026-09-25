import { notFound } from "next/navigation";
import { UiCatalog } from "./ui-catalog";

/**
 * 開発用 UI カタログ（/dev/ui）。
 * ファイル名が page.dev.tsx なので `next dev` のときだけページになる（next.config.ts の pageExtensions）。
 * 本番ビルドにはルートもチャンク（Python API の疎通確認を含む）も出力されない。
 * NODE_ENV=production で dev サーバーを動かした場合に備えて、念のため 404 にもする。
 */
export default function UiCatalogPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <UiCatalog />;
}
