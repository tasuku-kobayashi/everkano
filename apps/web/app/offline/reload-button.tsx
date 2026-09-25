"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";

const OFFLINE_PATH = "/offline";
/** サーバーに届くかの確認先（小さな静的ファイル。Service Worker は GET 以外を扱わないので必ずネットワークへ出る） */
const PROBE_URL = "/manifest.json";

function isOfflinePath(): boolean {
  return window.location.pathname.replace(/\/+$/, "") === OFFLINE_PATH;
}

/**
 * 開こうとしていた画面を読み込み直す。
 * Service Worker はオフライン時、開こうとした画面の URL のまま /offline の内容を返す（public/sw.js）。
 * そのため URL（例: /dm/<キャラID>）をそのまま読み込み直す。/offline を直接開いている場合だけホームへ。
 */
function retry(): void {
  if (isOfflinePath()) {
    window.location.replace("/");
  } else {
    window.location.reload();
  }
}

/** サーバーに届くか（HEAD。キャッシュを使わない）。届かなければ false */
async function serverReachable(): Promise<boolean> {
  try {
    await fetch(PROBE_URL, { method: "HEAD", cache: "no-store" });
    return true;
  } catch {
    return false;
  }
}

/**
 * オフラインページの「再読み込み」。通信が戻ったとき（online イベント）も自動で読み込み直す。
 *
 * online イベントは、このページが表示されてから JavaScript が動き出すまでの間に来ると取りこぼす。
 * そこで表示の直後にも（端末がオンラインなら）サーバーに届くかを確かめ、届けば読み込み直す。
 * 届かない（サーバーの停止など）なら何もしない（オフラインページが出たまま読み込みを繰り返さない）。
 * /offline を直接開いている場合は自動では移動しない（ボタンで移動する）。
 */
export function ReloadButton() {
  useEffect(() => {
    let disposed = false;
    const recoverIfReachable = () => {
      void serverReachable().then((reachable) => {
        if (reachable && !disposed) retry();
      });
    };
    if (navigator.onLine && !isOfflinePath()) recoverIfReachable();
    window.addEventListener("online", recoverIfReachable);
    return () => {
      disposed = true;
      window.removeEventListener("online", recoverIfReachable);
    };
  }, []);

  return (
    <Button className="mt-6" onClick={retry}>
      再読み込み
    </Button>
  );
}
