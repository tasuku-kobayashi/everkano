# ADR-0029: Web の通信失敗の扱い（タイムアウト・再試行・オフライン・「ログイン切れ」の判定・エラー文言）

- ステータス: 採用
- 日付: 2026-09-26
- 関連: 仕様書 §14 D-3（圏外・通信エラー）・§15 / [ADR-0002](0002-data-access-split.md)・[ADR-0019](0019-chat-deadline.md)・[ADR-0025](0025-db-tls-and-api-entry-failures.md) /
  実装: `apps/web/lib/supabase/fetch-timeout.ts`, `apps/web/lib/supabase/client.ts`, `apps/web/lib/query-client.ts`, `apps/web/lib/query-retry.ts`,
  `apps/web/lib/api/client.ts`（`readAccessToken`）, `apps/web/lib/api/errors.ts`（`API_ERROR_MESSAGES` / `toAppError`）, `apps/web/lib/auth/account.ts`（`handleGlobalAuthError`）,
  `apps/web/e2e/pwa.spec.ts`（D-3）

## コンテキスト

納品前の検査で、通信が不安定なときの挙動に次の問題が見つかった（スマホ専用アプリなので、圏外・電波の弱い場所は日常的に起きる）。

- **読み取りが終わらない**: supabase-js のリクエストにタイムアウトが無く、通信が途中で止まる（キャプティブポータル・TCP の停止）とスケルトンが
  いつまでも出ていた（60 秒後もスケルトン）。Supabase に届かない場合も、postgrest-js 自身の自動再試行（GET を最大 3 回）と React Query の再試行が
  重なって 8 回リクエストし、エラー表示まで約 16 秒かかっていた。
- **オフラインで止まったままになる**: React Query の既定（`networkMode: "online"`）では、端末がオフラインの間はクエリ・ミューテーションが
  「一時停止」し、エラーにもならない。DM を送ると「送信中」と「入力中…」が出たまま、コメントの送信中表示も消えず、仕様の通信エラーの文言が出なかった。
- **一時的な失敗でログアウトさせる**: アクセストークンの更新（refresh）が通信失敗で失敗すると「ログインの有効期限が切れました」（401 扱い）になり、
  ログアウトさせていた。認証サーバーの一時的な障害でも同様だった（API 側は [ADR-0025](0025-db-tls-and-api-entry-failures.md) で 503 に変更）。
- **エラーの形がばらばら**: supabase-js はエラーをプレーンなオブジェクトで返すため、画面ごとに文言や再試行の判定が違っていた。
- BRIEF（開発時の取り決め）§2.10 の通信エラーの文言は「通信できませんでした。電波の良い場所で再度お試しください」だったが、fetch の失敗は
  **API・Supabase の停止や再起動でも起きる**。その場合にユーザーの電波のせいにする文言は誤り。

## 決定

- **タイムアウト**: Supabase（REST / Auth）へのすべてのリクエストを 15 秒で打ち切る（`createFetchWithTimeout` を supabase-js の `global.fetch` に渡す）。
  Python API は既定 15 秒、`/chat`・`/comments/generate` は 45 秒（`CHAT_TIMEOUT_MS`。API の締め切り 38 秒より長く。[ADR-0019](0019-chat-deadline.md)）。
- **再試行は React Query の 1 回だけ**: postgrest-js の自動再試行は無効にする（`db: { retry: false }`）。React Query は読み取りクエリを最大 1 回だけ、
  **再試行して結果が変わり得るエラー**（通信失敗・5xx・429・接続や資源不足の SQLSTATE）のときだけ再試行する。4xx・権限不足・タイムアウト（既に 15 秒
  待っている）・中断は再試行しない（`isRetryableQueryError`）。ミューテーション（DM 送信・コメント等）は二重送信を避けるため自動で再試行しない。
- **オフラインでも実行する**: クエリ・ミューテーションとも `networkMode: "always"`。オフラインなら fetch がすぐに失敗し、通常のエラー表示・トースト・
  再送ボタンに流れる（D-3）。オンライン復帰時の再取得（`refetchOnReconnect`）は有効のまま。常設のオフラインバナーは出さない。
- **ログアウトさせるのは 2 つだけ**: API の 401 `unauthorized` と 403 `account_deleted`（`handleGlobalAuthError`）。トークンの更新が通信失敗・Auth の 5xx で
  失敗した場合は `network_error` として扱い、ログアウトさせない（`readAccessToken`）。API の 503（認証サーバー障害）でもログアウトさせない。
- **エラーの形をそろえる**: supabase-js のエラーは `if (error) throw toAppError(error);` で Python API と同じ `ApiError { status, code, message }` にする
  （通信失敗は `network_error`、タイムアウトは `timeout`）。表示と再試行の判定は 1 か所（`lib/api/errors.ts`・`lib/query-retry.ts`）で行う。
- **エラー文言の決まり**: 文は句点「。」で終える。時間をおいた再試行の案内は「しばらくしてから再度お試しください。」にそろえる（Python API の
  `DEFAULT_MESSAGES` と同じ）。通信失敗の文言は **「通信できませんでした。接続を確認して、しばらくしてから再度お試しください。」**（BRIEF §2.10 の文言から変更。
  端末の電波とサーバーの停止のどちらでも正しい）。既定の文言は単体テスト（`lib/api/errors.test.ts`）で検査する。

## 結果・トレードオフ

- 圏外・通信停止のときも、スケルトンや「入力中…」のまま止まらず、エラーと再試行の手段が出る（E2E `pwa.spec.ts` の D-3 の 3 テスト。再検査では
  オフラインでの DM 送信・コメント投稿のトーストが 9〜14 ms で表示された）。
- 通信停止時の最悪の待ち時間は 15 秒 +（再試行する場合）15 秒。再試行を 1 回にしたので、一時的な瞬断で失敗表示になる場面は以前より増え得るが、
  各画面に再試行の手段がある。
- オフラインであることを常時表示するバナーは無い（固定のタブバー・入力欄と重ならない配置の検討が必要なため見送り。操作したときにエラーで分かる）。
- 通信失敗の文言は「電波」と書かないので、原因の切り分けはユーザーには分からない（サポートでは `X-Request-ID` とサーバーのログで確認する）。

## 代替案

- **`navigator.onLine` で文言を分ける**（オフラインなら「電波の良い場所で」）: 同じエラーコードに 2 つの文言ができ、画面・テストの扱いが分かれる。
  `navigator.onLine` は「ネットワークに繋がっている」だけでインターネットに届くことを保証しないため、判定も不確か。
- **postgrest-js の再試行を残し、React Query の再試行を切る**: postgrest-js は GET だけを最大 3 回・1 / 2 / 4 秒待ちで再試行し、判定を変えられない。
  React Query 側に一本化した方が、Python API と同じ判定にできる。
- **`networkMode: "offlineFirst"`**: 1 回目は実行されるが、再試行は復帰まで一時停止するため、再試行中の画面が止まったように見える。
