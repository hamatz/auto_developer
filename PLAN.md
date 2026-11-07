# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 9: Antigravity プラグインとしての Evidence Bundle 統合・スキル拡充
  - P1: `skills/nightly-run/SKILL.md` の Morning Handoff における Evidence Bundle 出力と提示の明文化
  - P2: 新規スキル `skills/nightly-bundle/SKILL.md` の作成（チャットからワンタップでバンドル生成）
  - P3: 新規スキル `skills/nightly-verify/SKILL.md` の作成（チャットでバンドルZIPを監査・レポート）
  - P4: `plugin.json` の `suggestedPrompts` 拡充と総合検証

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. `skills/nightly-run/SKILL.md` に Evidence Bundle 生成とチャット提示ステップを追加
- [x] 2. `skills/nightly-bundle/SKILL.md` の新規作成（成果提出パッケージ生成スキル）
- [x] 3. `skills/nightly-verify/SKILL.md` の新規作成（提出バンドルの独立検証・監査スキル）
- [x] 4. `plugin.json` の更新（`suggestedPrompts` に `/nightly-bundle`, `/nightly-verify` を追加）
- [x] 5. 総合検証（`scripts/verify_local.sh`, `ruff`, `mypy`）とコミット

## 止まっていること・判断待ち
- なし (全ステップ完了)


