import type { Metadata } from "next";
import { Suspense } from "react";
import { SearchView } from "@/components/search/search-view";
import SearchLoading from "./loading";

export const metadata: Metadata = { title: "検索" };

/** 検索（B-6 / 仕様 §4.2）。useSearchParams を使うため Suspense で包む */
export default function SearchPage() {
  return (
    <Suspense fallback={<SearchLoading />}>
      <SearchView />
    </Suspense>
  );
}
