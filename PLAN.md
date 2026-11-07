# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 13: Stage 1 Complete Hardening & Git Tree Binding (物理Git Tree SHA拘束とWorktree成果完全保全)
  - P0-1: `EvidenceBundler.verify_bundle()` による物理 Git Tree SHA 照合と架空 Evidence 遮断
  - P0-2: `WorktreeManager.remove_worktree()` による未コミット変更の自動 Quarantine 退避保全と `purge_unmerged` ガード
  - P0-3: Sol 氏再現シナリオ（架空 Evidence 注入・未コミット削除・未統合ブランチ保護）の決定論的受入テスト

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [ ] 1. `WorktreeManager` の未コミット変更自動 Quarantine 退避および `purge_unmerged` ガード実装（`src/auto_developer/worktree.py`）と単体テスト
- [ ] 2. `EvidenceBundler.verify_bundle()` の物理 `git_bundle.bundle` 照合と Git Tree SHA / SPEC SHA 厳格拘束（`src/auto_developer/bundler.py`）と単体テスト
- [ ] 3. 架空 Evidence 注入遮断および成果保全の決定論的受入テスト（`tests/test_bundler.py`, `tests/test_worktree.py`, `tests/test_multi_team_foundation.py`）

## 止まっていること・判断待ち
- なし（`/nightly-run` 実行準備完了）


