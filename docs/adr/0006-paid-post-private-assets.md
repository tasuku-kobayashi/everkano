# ADR-0006: 有料投稿アセットの分離（post_private_assets）

- ステータス: 採用
- 日付: 2026-09-25
- 関連: 仕様書 §5.2・§5.4・H3・H4 / [ADR-0011](0011-storage-adapter-and-bunny-token-auth.md) / 実装: マイグレーションの `post_private_assets`, `packages/personas/scripts/generate_seed.py`, `apps/web/components/post/paid-image.tsx`

## コンテキスト

- 仕様書は有料投稿を「ぼかし画像 + 鍵アイコン + 『有料コンテンツ』バッジ」で表示し、購入処理は一切実装しない（H3）と定めている。
- `posts` はクライアントから直接 SELECT される（[ADR-0002](0002-data-access-split.md)）。本体画像の URL を `posts.image_url` に置くと、
  CSS のぼかしを外す・通信を覗くだけで本体が見えてしまう。
- 将来の決済実装で「購入済みユーザーにだけ本体を見せる」経路を足せるようにしておきたい。

## 決定

- `posts.image_url` には、有料投稿の場合 **別に用意した低解像度のぼかしプレビュー** を置く。
- 本体は **`post_private_assets(post_id, image_url)`** に置く。RLS 有効・ポリシー無し・`anon` / `authenticated` への grant 無しで、
  クライアントからは一切到達できない（`service_role` / `postgres` のみ）。
- **プレビューの URL / オブジェクトキーから本体のキーを推測できないようにする**。「本体 URL + `?blur=10`」のようにクエリを外すだけで
  本体に届く形や、slug・連番を含むキーは不可。シードではプレビューと本体に別々のハッシュ値のキー（`opaque_image_key("preview" | "private", slug)`）を
  使い、`generate_seed.py` が推測できないことを検証する。本番の Bunny でも `previews/<uuid>.jpg` と `private/<別の uuid>.jpg` のように分ける。
- UI は念のためプレビューにも強い CSS ぼかし（8px 以上）と鍵・価格を重ね、タップでロックモーダル（「この投稿は有料コンテンツです」
  → 「購入する（準備中）」→ トースト「課金機能は現在準備中です」）を出す。購入処理・課金 API は存在しない。

## 結果・トレードオフ

- E2E（`apps/web/e2e/paid.spec.ts`）で、フィード・投稿詳細・プロフィールの有料タブのいずれでも、`post_private_assets` の URL が通信にも
  DOM にも出ないことを確認している。権限は pgTAP（`02_characters_posts` / `06_anon_and_private_tables`）で検査している。
- 有料投稿 1 件につき画像を 2 つ（プレビューと本体）用意する運用になる（[06-operations.md](../handover/06-operations.md#投稿を追加する)）。
- 決済を実装するときは、購入済みかを確認したうえで本体の署名付き URL（Bunny のトークン認証）を返す API を追加する想定。
  それまで `post_private_assets` を読むコードは存在しない。

## 代替案

- **`posts` に本体 URL の列を足して列 grant で隠す**: `posts` は列を列挙して読むが、取り違え（`select` への列追加）で公開されやすい。
  別テーブル + ポリシー無しにして、誤って公開される経路をなくした。
- **本体画像に CDN のぼかしパラメータを付けて表示**: パラメータを外せば本体が見える。不採用。
- **有料投稿は画像を出さない**: 仕様書の「ぼかし画像」の表現を満たさない。
