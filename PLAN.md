# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 18: Stage 1 Absolute Zero-Loss (Git無視通常ファイルの包括的Quarantine保全 & レポート鮮度保証)
  - P0: Git無視通常ファイル（`local-notes.txt`等）の存在を検知し、`force=False` では削除拒否、`force=True` では Quarantine スナップショットへ確実に退避保全してから削除する（成果消失の完全排除）
  - P1: Morning Brief (NIGHTLY_REPORT.md) の最新鮮度保証
  - P0/P1: 決定論的受入テスト（`local-notes.txt` 退避保全）および全体検証ゲート合格確認

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. Git無視通常ファイル（`local-notes.txt`等）の検出と Quarantine 退避保全（`src/auto_developer/worktree.py`）および単体テスト（Red-First）
- [x] 2. 決定論的受入テストおよび Morning Brief 鮮度保証（`tests/test_multi_team_foundation.py`, `src/auto_developer/bundler.py`）と全検証ゲート合格確認

## 止まっていること・判断待ち
- なし（Phase 18 全ステップ完了。公式 Evidence Record 発行および Evidence Bundle 生成へ）


