# ADR-0011: StorageAdapter と Bunny.net トークン認証

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §2「画像・音声ストレージ」・H4・H8・§11 / [ADR-0006](0006-paid-post-private-assets.md) / 実装: `apps/web/lib/storage/`, `apps/web/app/media/[...key]/route.ts`, `apps/web/next.config.ts`

## コンテキスト

- H4: NSFW 画像を Vercel / Supabase Storage に置かない。実体は Backblaze B2 等の外部に置き、ストレージ抽象化レイヤー経由で扱う。
- 仕様書 §2: CDN は Bunny.net（トークン認証 URL）、`next/image` は使わず CDN の変換パラメータを使う。H8: Cloudflare は使わない。
- 開発中・シードはプレースホルダ URL（`api.dicebear.com` / `picsum.photos`）で動き、本番ではオブジェクトキーに切り替えられる必要がある（§11）。
- トークン認証の鍵はクライアントに渡せない。

## 決定

- Web に `StorageAdapter` インターフェースを置く: `resolveImageUrl(src, { width?, quality?, blur? })`。UI は必ず `<CdnImage>` / `<Avatar>`
  （内部でアダプタを使う）経由で `<img src>` を得る。`next/image` は使わず `<img loading="lazy" decoding="async">`。
- ドライバーを `NEXT_PUBLIC_STORAGE_DRIVER` で選ぶ:
  - `passthrough`（既定）: DB の値をそのまま使う（シードのプレースホルダ URL 用）。
  - `bunny`: DB の値を **オブジェクトキー**（例: `characters/misaki/avatar.jpg`）として `${NEXT_PUBLIC_CDN_BASE_URL}/<キー>?blur=&quality=&width=`
    に変換する（Bunny Optimizer のパラメータ。キー昇順）。`srcSet` は 320 / 640 / 1080 px。
  - どちらのドライバーでも、**絶対 URL（http(s) / data / blob）はそのまま返す**。移行途中はキーと URL が混在してよい。
- **トークン認証**: サーバー専用の `BUNNY_TOKEN_AUTH_KEY` が設定されていると、`next.config.ts` が `NEXT_PUBLIC_MEDIA_SIGNED=1`（有無だけ）を
  クライアントに渡し、アダプタは同一オリジンの `/media/<キー>?width=...` を返す。Route Handler `/media/[...key]` が:
  - 未ログインなら 401（middleware も `/media` はリダイレクトでなく 401）。
  - Bunny の Token Authentication（SHA256）で `token` / `expires` 付きの URL を作り **302 でリダイレクト** する。変換パラメータも署名対象。
    失効は `BUNNY_TOKEN_TTL_SECONDS`（既定 3600）以上で 5 分単位に切り上げ（CDN / ブラウザのキャッシュヒット率のため）、
    リダイレクト自体は `Cache-Control: private, max-age=min(300, TTL/2)`。
  - 画像のバイト列は Vercel を通らない（リダイレクトだけ）。
- 画像の実体は Backblaze B2 に置き、Bunny.net の Pull Zone のオリジンにする。Supabase Storage は無効（`infra/supabase/config.toml` の `[storage] enabled = false`）。
- Service Worker は他オリジン（CDN）と `/media/*` をキャッシュしない。

## 結果・トレードオフ

- DB の値を書き換えずに、環境変数だけでプレースホルダ ↔ 本番 CDN を切り替えられる。`NEXT_PUBLIC_*` はビルド時に埋め込まれるので、変えたら再デプロイする。
- トークン認証を有効にすると、画像ごとに Vercel の関数呼び出しとリダイレクトが 1 回入る（遅延・実行回数）。署名 URL はユーザーに紐付かないので、
  有効期限内なら URL を知っている人は取得できる（IP 制限は未使用）。有料投稿の本体はそもそもクライアントに渡さない（[ADR-0006](0006-paid-post-private-assets.md)）。
- 変換パラメータ（width / quality / blur）は Pull Zone で Bunny Optimizer が有効な場合に効く。無効なら原寸のまま配信される。
- 本番の画像に切り替える手順は [06-operations.md](../handover/06-operations.md#画像を本番の-cdn-に移す)。

## 代替案

- **`next/image`**: 仕様書で禁止。画像最適化が Vercel 経由になり H4 の趣旨にも反する。
- **Supabase Storage**: H4 で禁止。
- **Cloudflare（R2 / Images）**: H8 で禁止。
- **クライアントで署名**: 鍵が漏れる。不採用。
- **サーバーコンポーネントで一覧の全画像を事前署名**: 画面ごとに署名処理が必要になり、クライアント側の無限スクロールと相性が悪い。
  キー単位のリダイレクトの方が単純なので採用しなかった。
