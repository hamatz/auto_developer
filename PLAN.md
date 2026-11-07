# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 15: Stage 1 Ultimate Defense & Deep Secret Scanning (Worktree秘密情報内容検査・Manifest必須項目厳格化・ZIP未知ファイル排除・Manifestタスク内容照合)
  - P0: Worktree 隔離における秘密情報内容スキャン（`BoundaryGuard` 連携）と SHA-256 Manifest 読み戻し検証（破損・漏洩時の削除停止 Fail-Closed）
  - P1-1: Bundle Manifest の必須項目厳格化（`head_commit`, `head_tree_sha`, `job_id` の欠落・空文字を即座に INVALID 遮断）
  - P1-2: ZIP 内の全ファイルエントリと Manifest 記載ファイルの完全一致検証（`mystery.txt` 等の未知ファイル追加やパストラバーサルの即座拒否）
  - P1-3: Manifest タスク情報と Evidence Record 内容の完全照合（タスク重複排除、commit/tree_sha/spec_sha/checker/status の改変検知・遮断）
  - P0/P1-4: Sol 氏再現シナリオ（APIキー混入Worktree削除停止、HEAD空文字、ZIP内未知ファイル、Manifestタスク改変）の決定論的受入テスト

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. Worktree 隔離における秘密情報内容スキャン（APIキー等）と SHA-256 Manifest 読み戻し検証（破損・漏洩時の削除停止 Fail-Closed）（`src/auto_developer/worktree.py`）および単体テスト
- [ ] 2. Bundle Manifest の必須項目厳格化（HEAD 欠落・空文字拒否）、ZIP 未知ファイル排除（`mystery.txt` 等の拒否）（`src/auto_developer/bundler.py`）および単体テスト
- [ ] 3. Manifest タスク情報と Evidence Record 内容の完全照合（重複排除・改ざん拒否）および Sol 氏再現シナリオの決定論的受入テスト（`tests/test_bundler.py`, `tests/test_worktree.py`, `tests/test_multi_team_foundation.py`）

## 止まっていること・判断待ち
- なし（ステップ 1 完了。ステップ 2 着手）


