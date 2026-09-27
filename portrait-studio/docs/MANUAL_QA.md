# 手動 QA チェックリスト（人間が確認する項目）

自動化できた項目は `web/e2e/app.spec.ts`（Playwright。スクリーンショットは `docs/screenshots/`）と `api/tests/`（pytest）にあります。
ここには **GPU 実機でしか確認できないこと** と **目視の品質判断** をまとめます。チェックした日付と担当を末尾に残してください。

## A. 環境（G0 / G1）— RTX 5070 の実機で

- [ ] `scripts/verify_env.sh` が `ALL CHECKS PASSED` で終わる（生の出力を `docs/acceptance/` に貼る）
- [ ] `torch.__version__` が `+cu128`、`torch.version.cuda` が `12.8`、`sm_120 is not compatible` の警告が出ない
- [ ] `GET /object_info` に `IPAdapterFaceID` `ApplyPulid` `ApplyInstantID` `FaceDetailer` `UltralyticsDetectorProvider` がある
- [ ] `GET /api/health` の `workflows[].issues` に `error:` が無い（`warn:` は内容を確認）
- [ ] ComfyUI と API が `127.0.0.1` にしか公開されていない（`docker compose ps` / `ss -ltnp`）

## B. 品質（G2 / G3）— 目視

- [ ] 候補チェックポイントを 2 本以上、同一 seed・同一プロンプトで比較し、フォトリアルな日本人として自然な方を採用した（docs/MODELS.md に比較結果）
- [ ] 生成画像がアニメ調・CG 調・プラスチック肌になっていない
- [ ] FaceDetailer（denoise 0.3〜0.4）で品質が上がり、同一性が壊れていない（ON/OFF の類似度を比較）
- [ ] 同一の参照顔・同一 seed で PuLID / FaceID / InstantID を比較し、採用手法を 1 つ決めた（類似度の実測値を docs/MODELS.md に記録）
- [ ] `scripts/face_similarity.py calibrate` で同一キャラ同士 / 別キャラ同士の分布を測り、しきい値（.env の `SIMILARITY_*`）を較正した
- [ ] `scripts/measure_vram.py` の結果が `api/app/data/vram_table.json` に入り、設定画面の VRAM テーブルに表示される
- [ ] 受け入れ 15: 「カフェ」と「ビーチ」で生成した画像が同一人物と識別できる（類似度 ≥ 較正後の許容値、かつ目視）
- [ ] 生成時間（1 枚あたり・4 枚あたり）を記録した

## C. UI（自動化済みだが実機でも一度）

- [ ] 5 画面が表示される（キャラ一覧 / 作成ウィザード / ワークスペース / ギャラリー / 設定）
- [ ] ウィザード 5 ステップを最後まで通せる（実 GPU の種顔生成 → 選定 → 検証 9 枚 → 登録 → 完了）
- [ ] NSFW ぼかしが既定で ON（画面共有のつもりで確認）
- [ ] ヘッダの VRAM メーターが実 GPU の値で動く
- [ ] locked 変更で警告が出て、既定では保存されない（新版として保存を選んだときだけ版が増える）
- [ ] 危険な解像度を選んだ瞬間に OOM 事前警告が出る（実測テーブルに基づく推定値）
- [ ] 失敗時に「解像度を 1 段下げて再試行 / upscale 1.0 / face_detailer OFF」のボタンで復旧できる
- [ ] ワンクリック再生成の `params_snapshot` が元と一致する
- [ ] ギャラリーが 500 枚超でもスクロールが重くない（実データで）
- [ ] キーボードのみで主要操作ができる（Tab / Enter / ← → / S / C / Esc）
- [ ] 設定画面から COMPLIANCE.md が読める

## D. 法務・運用

- [ ] 参照顔が架空のキャラクターであることを確認した（実在人物の写真を使っていない）
- [ ] 全キャラクターが成人であることを確認した
- [ ] 採用モデルのライセンスと商用可否を docs/MODELS.md と COMPLIANCE.md に記録した
- [ ] `data/` と `.env` がコミットされていない

| 日付 | 担当 | 範囲 | 結果 |
| --- | --- | --- | --- |
| | | | |
