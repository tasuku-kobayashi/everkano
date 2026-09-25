# ADR-0032: Service Worker のデプロイごとの更新とキャッシュの上限・オフラインページの復帰

- ステータス: 採用
- 日付: 2026-09-26
- 関連: 仕様書 §2「PWA 対応」・§14 D-3 / [ADR-0012](0012-pwa-and-login-magic-link-otp.md)（本 ADR で追補） /
  実装: `apps/web/public/sw.js`（`CACHE_POLICY` / `STATIC_MAX_ENTRIES`）, `apps/web/components/pwa/service-worker-register.tsx`, `apps/web/next.config.ts`（`resolveBuildId`）,
  `apps/web/app/offline/reload-button.tsx`, `apps/web/e2e/pwa.spec.ts`

## コンテキスト

ADR-0012 の手書きの Service Worker（SW）には、運用上の問題が 3 つあった。

- キャッシュの版（`VERSION`）が手で書く定数だったため、上げ忘れるとデプロイ後も古い SW が動き続け、オフラインページ（とその CSS / JS）が
  最初にインストールした時点のまま固定されていた。
- `/_next/static` のキャッシュを消さないため、デプロイのたびに古いビルドのチャンクが端末に溜まり続けた（iOS のホーム画面の PWA はストレージの
  上限が小さく、溢れるとオリジンのデータがまとめて消されることがある）。アイコンはキャッシュ優先のままで、差し替えが届かなかった。
- オフラインページの「再読み込み」は常に `/` へ移動し、開こうとしていた画面に戻れなかった。通信が戻ったときの自動の読み込み直しも、
  ページの JavaScript が動き出す前に `online` イベントが来ると取りこぼしていた（統合検証の E2E で 1 回失敗）。

## 決定

- **デプロイごとの ID で SW を登録する**: `/sw.js?v=<ビルド ID>`。ビルド ID は `next.config.ts` が `NEXT_PUBLIC_BUILD_ID`（明示）→ `VERCEL_DEPLOYMENT_ID` →
  `VERCEL_GIT_COMMIT_SHA` → `GITHUB_SHA` → ビルド時刻 の順に決める（英数・`_`・`-` の 64 文字まで）。登録 URL が変わるのでデプロイのたびに新しい SW が入り、
  オフラインページ（とその CSS / JS）を取り直し、前のビルドのオフライン用キャッシュは activate 時に消える。
- **`CACHE_POLICY`**（旧 `VERSION`）は、キャッシュの構成（名前・方針）を変えたときだけ上げる。
- **`/_next/static` はビルドをまたいで使い回し、200 件（`STATIC_MAX_ENTRIES`）を超えたら追加した順に古いものから消す**（デプロイ直後もまだ開いている古い画面が
  遅延読み込みする古いチャンクを取れるように）。`/icons/*` と `/manifest.json` はキャッシュを返しつつ裏で取り直す（stale-while-revalidate）。
- 引き続き、ページ（HTML）・Supabase・Python API・CDN（他オリジン）・`/auth/*`・`/media/*` はキャッシュしない（ログイン中のユーザーのデータを端末に残さない）。
- **オフラインページ**: SW は開こうとした画面の URL のまま `/offline` の内容を返す。「再読み込み」と通信の復帰（`online` イベント）はその URL を読み込み直す
  （`/offline` を直接開いた場合だけホームへ）。表示の直後にも、端末がオンラインなら `HEAD /manifest.json` でサーバーに届くかを確かめ、届けば読み込み直す
  （サーバーが止まっている間は届かないので、読み込みを繰り返さない）。

## 結果・トレードオフ

- デプロイの手順に SW の作業は無い（Vercel ではデプロイ ID が自動で使われる）。ビルド ID を固定したい場合だけ `NEXT_PUBLIC_BUILD_ID` を設定する。
- `NEXT_PUBLIC_BUILD_ID` を同じ値に固定したまま再デプロイすると、SW は更新されない（オフラインページが古いままになる）。
- 端末のキャッシュは最大で「200 件の静的ファイル + 1 ビルド分のオフラインページ」に収まる。
- E2E（`pwa.spec.ts`）で、ビルドごとのオフライン用キャッシュ・200 件の上限・「再読み込み」が元の URL を読み込み直すこと・通信の復帰で自動で読み込み直すことを検査している。

## 代替案

- **next-pwa / Workbox を導入する**: 依存とビルドの設定が増え、キャッシュの方針（HTML・API を扱わない）を細かく守らせるのが難しくなる。
  手書きの SW は 200 行程度で、方針をコメントで確認できる。
- **`/_next/static` もビルドごとにキャッシュを分けて古いものを全部消す**: デプロイ直後に開いたままの画面が古いチャンクを読めずに壊れる。
- **SW の更新時に自動で再読み込みする（`controllerchange` で `location.reload()`）**: 入力中の DM・コメントが消える。更新は次のページ遷移で反映されればよい。
