# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 14: Stage 1 Final Completion & Strict Fail-Closed Hardening (Verifier完全Fail-Closed化とQuarantineサニタイズ)
  - P0-1: `EvidenceBundler.verify_bundle()` の完全 Fail-Closed 化（Git Bundle展開失敗・空履歴での即座 INVALID、Manifest HEAD/Tree照合）
  - P1-1: Evidence Record の `commit` と `tree_sha` の 1対1 物理照合および Manifest タスク完全性検証
  - P1-2: `WorktreeManager` の未コミット退避時の機密ファイル除外（サニタイズ）および `git status` エラー時の Fail-Closed
  - P0/P1-3: Sol 氏再現の 4 つの異常系ストレステスト（Bundle偽造、Commit不正、Evidence欠落、HEAD偽造）および Quarantine 機密除外の決定論的受入テスト

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. `WorktreeManager` の未コミット退避時機密サニタイズおよび Git status Fail-Closed 実装（`src/auto_developer/worktree.py`）と単体テスト
- [x] 2. `EvidenceBundler.verify_bundle()` の完全 Fail-Closed 化（Git Bundleクローン失敗遮断、Manifest HEAD/Tree照合、Commit 1対1 物理照合、Manifestタスク完全性）（`src/auto_developer/bundler.py`）と単体テスト
- [x] 3. Sol 氏再現の異常系ストレステストおよび Quarantine サニタイズの決定論的受入テスト（`tests/test_bundler.py`, `tests/test_worktree.py`, `tests/test_multi_team_foundation.py`）

## 止まっていること・判断待ち
- なし（Phase 14 全ステップ完了。公式 Evidence Record 発行および Evidence Bundle 生成へ）


