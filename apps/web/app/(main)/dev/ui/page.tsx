import { notFound } from "next/navigation";
import { UiCatalog } from "./ui-catalog";

/** 開発用 UI カタログ（/dev/ui）。本番ビルドでは 404 */
export default function UiCatalogPage() {
  if (process.env.NODE_ENV === "production") notFound();
  return <UiCatalog />;
}
