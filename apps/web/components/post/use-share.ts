"use client";

import { useCallback } from "react";
import { useToast } from "@/components/ui/toast";

/** 絶対 URL（例: /posts/xxx → https://example.com/posts/xxx） */
export function absoluteUrl(path: string): string {
  return new URL(path, window.location.origin).toString();
}

/** クリップボードへコピー（Clipboard API が使えない環境では execCommand にフォールバック） */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (error) {
    console.warn("[share] clipboard API failed, falling back:", error);
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    return ok;
  } catch (error) {
    console.error("[share] copy failed:", error);
    return false;
  }
}

/** リンクをコピーしてトーストで知らせる */
export function useCopyLink() {
  const toast = useToast();
  return useCallback(
    async (path: string) => {
      const ok = await copyText(absoluteUrl(path));
      if (ok) toast.show("リンクをコピーしました");
      else toast.error("リンクをコピーできませんでした");
    },
    [toast],
  );
}

/**
 * シェア: Web Share API（スマホの共有シート）が使えればそれを、無ければリンクをコピーしてトースト。
 * ユーザーが共有シートを閉じた（AbortError）場合は何もしない。
 */
export function useShareLink() {
  const copyLink = useCopyLink();
  return useCallback(
    async (path: string, title?: string) => {
      const url = absoluteUrl(path);
      if (typeof navigator.share === "function") {
        try {
          await navigator.share({ url, title });
          return;
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") return;
          console.warn("[share] navigator.share failed, falling back to copy:", error);
        }
      }
      await copyLink(path);
    },
    [copyLink],
  );
}
