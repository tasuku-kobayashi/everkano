# ADR-0048: Web の補修（確認画面の送信を fetch と `location.replace()` に・端末の「戻る」の履歴の書き込みの保留・スケルトンの高さ・`RouteContent` によるハイドレーションエラー #418 の解消）

- ステータス: 採用
- 日付: 2026-09-26
- 関連: 仕様書 §4・H1・D-3 / [ADR-0026](0026-magic-link-confirm-page.md)（本 ADR で追補）・[ADR-0031](0031-web-ui-accessibility-and-dev-only-pages.md)（本 ADR で追補）・[ADR-0012](0012-pwa-and-login-magic-link-otp.md) /
  実装: `apps/web/lib/auth/confirm-submit.ts`・`route-helpers.ts`（`redirectTo`）, `apps/web/components/auth/confirm-login-form.tsx`, `apps/web/app/auth/confirm/verify/route.ts`,
  `apps/web/lib/navigation.ts`（`installHistoryGuard`）, `apps/web/components/ui/main-shell.tsx`（`RouteContent`）, `apps/web/components/ui/skeleton.tsx`, `apps/web/components/feed/stories-row.tsx`,
  `apps/web/README.md`「実装ルール」, `apps/web/e2e/`（`hydration.spec.ts`・`layout.spec.ts`・`auth.spec.ts`）, `apps/web/lib/navigation.test.ts`

## コンテキスト

エンジンの統合後の E2E で、Web に次の 3 つの問題が残っていた（いずれもエンジンとは独立の既存の不具合）。

- **「戻る」でマジックリンクの確認画面に戻る**（`layout.spec` のシートのテストが負荷の高いときだけ失敗）:
  - シートを UI で閉じると、自分で積んだ履歴のエントリを `history.go(-1)` で取り除く（[ADR-0031](0031-web-ui-accessibility-and-dev-only-pages.md)）。`go()` は非同期で、
    Chromium は戻る先を **呼んだ時点の** エントリから決める。「プロフィールを見る」はシートを閉じてから `router.push` するため、着地の前に Next.js が遷移のエントリを積むと、
    着地先はその下（元の画面のエントリ）になり、Next.js の次の `replaceState` が元の画面のエントリを遷移先の URL に書き換えていた。「戻る」で元の画面を飛ばし、
    その前（ログイン直後ならマジックリンクの確認画面）まで戻っていた。
  - 実際の利用者でも起きていた: 確認画面の「ログインする」は通常のフォームの送信（POST → 303）で、確認画面の URL（`/auth/confirm?token_hash=…`。使用済み）が履歴に残り、
    ログイン後の「戻る」で使用済みのリンクの確認画面に戻っていた。
- **ホームのスクロール位置の復元が 4px ずれる**（`layout.spec`）: ストーリーズの行の高さが、読み込み中（スケルトン）と読み込み後で 4px 違った。`Skeleton shape="text"` が
  常に `h-3` を付け、指定した `h-2.5` を黙って上書きしていた（`cn` はクラスの衝突を解決しない）。スクロールした後にストーリーズが読み込まれると、Chrome のスクロール
  アンカリングで `scrollY` が 4px 動いていた。
- **本番ビルドでまれに React のハイドレーションエラー #418**: これまで「動的ルートだけ・原因不明」としていた（[06-operations.md](../handover/06-operations.md) の既知の制約）。
  調べ直すと、不一致の要素は `MainShell` の `<main id="main">` で、React のハイドレーションの位置は既に `<main>` の最初の子（`<!--$-->`）にあった。`<main>` の直下に、
  まだ届いていない RSC の `children`（遅延の参照）を置いていたため、ハイドレーション中に中断（suspend）→ 再開（replay）すると、Next.js 15.5.26 に同梱の
  React 19.2 canary は **ホスト要素を作り直すときにハイドレーションの位置を戻さず**、`<main>` を自分の最初の子に対応付けようとして不一致になる（その場合 React はルート全体を
  クライアントで描画し直す）。静的なルート（`/dm` が最も多い）でも起きていた。

## 決定

### 確認画面の送信（ADR-0026 の追補）

- JavaScript が動いていれば、「ログインする」は `fetch`（`Accept: application/json`）で `POST /auth/confirm/verify` に送る。verify のルートは 303 の代わりに
  `200 + { location }` を返し（Cookie の発行は同じ）、画面は **`location.replace()`** で遷移する（確認画面のエントリを遷移先で置き換える = 履歴に残さない）。
  遷移先は同一オリジンの相対パスだけを受け付ける（`isSameOriginPath`）。
- JavaScript が無い・読み込み前は、通常のフォームの送信（303）のまま動く（その場合だけ確認画面が履歴に残る）。同一オリジンの POST だけを受け付ける守り
  （[ADR-0026](0026-magic-link-confirm-page.md)）は変えない。

### 端末の「戻る」の履歴の書き込みの保留（ADR-0031 の追補）

- `lib/navigation.ts` の `installHistoryGuard()` が `history.pushState` / `replaceState` を包み、**自分の `history.go(-n)` が着地するまでの履歴の書き込み**
  （Next.js の遷移・オーバーレイの追加）を保留して、着地の後に順に適用する（`popstate` が届かない場合は 3 秒で適用する）。履歴の操作が直列になり、
  取り除いたエントリの直下に必ず着地してから遷移先のエントリが積まれる。
- ほかに: `go(-n)` は今のエントリが本当に自分のオーバーレイのエントリのときだけ呼ぶ（アプリの外・ログインの画面まで戻らない）。オーバーレイを開いたまま画面遷移したら、
  先にオーバーレイのエントリを取り除いてから遷移のエントリを積む（「戻る」が 1 回空振りしない）。Next.js の同じ URL への `replaceState` ではオーバーレイの印を保つ。
- `window.history.pushState` / `replaceState` を直接呼ぶコードは書かない（呼ぶ場合もこのガードを通るので、保留されることを前提にする。`apps/web/README.md`）。

### スケルトンの高さ

- `Skeleton` は、`className` で `h-*` / `size-*` を指定したら既定の高さを付けない（コメントのとおりの挙動に直した）。ストーリーズのスケルトンは読み込み後と同じ 16px の行にした。
  スケルトンは読み込み後の要素と同じ高さにする（違うと、読み込みの完了でその下がずれ、スクロール中はスクロール位置も変わる）。

### `RouteContent`（#418 の解消）

- `MainShell` の `<main>` の中身を、`children` をそのまま返すだけの関数コンポーネント `RouteContent` で包む。中断・再開はこのコンポーネントで起き、DOM の対応付けの位置はずれない。
- 規則: **クライアントコンポーネントで、レイアウトから渡された RSC の `children` を `<main>` などのホスト要素の直下に置かない**（関数コンポーネントで挟む。`apps/web/README.md`）。
  Next.js / React の上流で直ったら `RouteContent` は外してよい。
- 回帰テスト: `e2e/hydration.spec.ts`（CPU を 8 倍遅くして主要な画面（`/dm`・`/dm/<id>`・`/me`・`/c/<handle>`・`/`）を繰り返しフルロードし、#418 が出ないこと。iPhone のみ）。
  修正前は 576 回のロードのうち 20 回で #418（`/dm` が 96 回中 12 回）、修正後は 576 回・8 倍の遅延での 256 回とも 0 回（修正時の計測）。

## 結果・トレードオフ

- ログイン後・シートの操作の後の「戻る」が、期待どおり直前の画面に戻る（`layout.spec` のシートのテストはブラウザの履歴そのものも検査する）。確認画面の最初の JS は約 2 kB 増えた。
- JavaScript が無い環境では、確認画面が履歴に残る（通常のフォームの送信では避けられない）。
- 履歴の書き込みを保留するので、シートを閉じる処理の直後の遷移は、`go()` の着地まで遅れる（着地しない場合も 3 秒で進む）。
- 既存の 2 つの端のケースは残る: 上に重なったダイアログを開いたまま下のシートを閉じると同じ URL のエントリが残る、「進む」も「戻る」として数える。
- #418 の原因は React の canary の挙動なので、同じ形（ホスト要素の直下の RSC の `children`）を新しく書くと再発する。レビューで確認する。

## 代替案

- **確認画面をやめて GET でログインする**: メールのスキャナーの先読みでトークンが消費され、ログイン CSRF を防げない（[ADR-0026](0026-magic-link-confirm-page.md) で退けた）。
- **ログイン後の画面で `history.replaceState` して確認画面を消す**: 直前のエントリ（確認画面）は後から書き換えられない。
- **シートを閉じた後の遷移を一定時間遅らせる**: 端末の速さに依存し、確実でない。履歴の操作を直列にする方が確実。
- **シートで履歴を積まない**: Android の「戻る」でシートが閉じなくなる（[ADR-0031](0031-web-ui-accessibility-and-dev-only-pages.md) の決定に反する）。
- **`suppressHydrationWarning` を付ける**: 文字列の不一致の警告を消すだけで、要素の不一致とルート全体の再描画は直らない。
- **`<main>` のランドマークをやめる**: アクセシビリティの約束（[ADR-0031](0031-web-ui-accessibility-and-dev-only-pages.md)）を失う。`loading.tsx` を外す・`htmlLimitedBots` を外す、は以前に試して原因でないと分かっている。
