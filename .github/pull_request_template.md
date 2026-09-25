## 何を

<!-- この PR で変更した内容を箇条書きで。関連 Issue / タスク番号（例: B-3, C-5）があれば記載 -->

-

## なぜ

<!-- 変更の目的・背景。設計判断を伴う場合は docs/adr/ の ADR へのリンクも -->

## スクリーンショット

<!-- UI 変更時は必須（スマホ幅 / ライト・ダーク両方）。UI 変更が無ければ「なし」 -->

| ライト | ダーク |
| ------ | ------ |
|        |        |

## 確認手順

<!-- レビュアーが手元で再現できる手順。例:
1. `pnpm db:start && scripts/setup-env.sh`
2. `pnpm dev` と `pnpm --filter @everkano/api dev`
3. http://localhost:3000/dm/... で 〜 を送信し、〜 が表示されること
-->

1.

## チェックリスト

- [ ] コミットメッセージは Conventional Commits（`feat:` / `fix:` / `chore:` ...）
- [ ] `pnpm lint` / `pnpm typecheck` / `pnpm test` が通る（API: `ruff check` / `ruff format --check` / `mypy app` / `pytest`）
- [ ] **RLS**: テーブル・列・関数を追加/変更した場合、RLS 有効化・最小権限の grant・ポリシーをセットで書き、`scripts/test-db.sh` の pgTAP テスト（`infra/supabase/tests/database/`）を追加/更新した
- [ ] **RLS**: Python API の DB アクセスは `user_id` で所有者チェックしている（API は postgres ロールで RLS をバイパスするため）
- [ ] **スキーマ変更時**: `pnpm db:types` で `packages/shared/src/database.types.ts` を再生成した
- [ ] **シークレット**: キー・トークン・パスワードをコミットしていない（`scripts/check-secrets.sh` が通る / 新しい環境変数は `.env.example` にキー名だけ追加）
- [ ] **スコープ外機能なし**: 決済・画像生成・TTS/音声・ユーザー投稿・フォロー・通知・管理画面・多言語対応を含まない（`scripts/check-scope.sh` が通る）
- [ ] ユーザー由来のテキスト（DM・コメント・メモリ）の書き込みは Python API 経由（Gate #1 モデレーション + 監査ログ）
- [ ] スマホ幅（〜480px）で表示崩れが無い（UI 変更時）
- [ ] エラーを握りつぶしていない（ログ出力 + ユーザー向けメッセージ）
- [ ] 必要に応じて README / ADR / 引き継ぎ資料（`docs/`）を更新した
