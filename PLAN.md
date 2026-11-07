# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 16: Stage 1 Perfection & Complete Hardening (Worktree未保存秘密ファイル消失阻止・Manifestタスク全必須フィールド完全照合)
  - P0: Worktree 削除時に Quarantine 除外となる未コミット秘密情報（`.env`, 秘密鍵等）が存在する場合、成果消失を防ぐため削除を拒否（Fail-Closed 保全）
  - P1-1: Bundle Manifest タスク全必須フィールド（`commit`, `tree_sha`, `spec_sha`, `status`, `checker_decision`）の欠落不可（空文字・None 禁止）および Evidence Record との完全一致照合
  - P0/P1-2: Sol 氏再現シナリオ（.env 存在時の削除拒否、Manifest task commit 空文字、spec_sha 不一致）の決定論的受入テスト

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. Worktree 削除時における未保存秘密情報（`.env`, 秘密鍵等）存在時の削除拒否（Fail-Closed 保全）（`src/auto_developer/worktree.py`）および単体テスト
- [x] 2. Bundle Manifest タスク全必須フィールド（`commit`, `tree_sha`, `spec_sha`, `status`, `checker_decision`）の欠落不可・完全一致照合（`src/auto_developer/bundler.py`）および単体テスト
- [ ] 3. Sol 氏再現シナリオ（`.env` 存在時Worktree削除拒否、Manifestの `commit` 空文字、`spec_sha` 不一致の遮断）の決定論的受入テスト（`tests/test_bundler.py`, `tests/test_worktree.py`, `tests/test_multi_team_foundation.py`）

## 止まっていること・判断待ち
- なし（ステップ 2 完了。ステップ 3 着手）


