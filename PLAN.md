# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 12: Stage 1 Finalization & Strict Audit (Worktree成果保護とEvidence Bundle厳格監査)
  - P1: `WorktreeManager` の非破壊的一括クリーンアップと未マージブランチ保全（`force=False` デフォルト化、未マージブランチ削除拒否）
  - P2: `EvidenceBundler` の物理 Evidence Record 同梱強化（`.git/nightly_evidence/archive/` と `.dev/evidence/` の完全集約）
  - P3: `verify-bundle` の 3値監査判定（`VERIFIED` / `INTEGRITY_ONLY` / `INVALID`）実装と未承認統合の物理遮断
  - P4: 未マージ成果保護と 3値監査判定の決定論的受入テスト

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. `WorktreeManager` の非破壊的クリーンアップと未マージブランチ保全（`src/auto_developer/worktree.py`）および単体テスト
- [x] 2. `EvidenceBundler` の物理 Evidence Record 同梱強化（`src/auto_developer/bundler.py`）
- [x] 3. `auto-dev verify-bundle` の 3値監査判定（`VERIFIED` / `INTEGRITY_ONLY` / `INVALID`）実装（`src/auto_developer/bundler.py`, `cli.py`）および単体テスト
- [ ] 4. 未マージブランチ保全と 3値監査判定の決定論的受入テスト（`tests/test_bundler.py`, `tests/test_worktree.py`）

## 止まっていること・判断待ち
- なし（`/nightly-run` 実行準備完了）


