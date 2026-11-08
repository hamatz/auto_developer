# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 37: Stage 3 復旧時プロセス同一性照合・Worktree安全境界復元・Evidence実証厳格化 (Sol氏レビュー残課題)
  - P0: プロセス同一性照合（`process_start_time`）による無関係プロセス誤停止の防止（機能WW.1）
  - P0: Worktree 削除安全境界の復元と直接 `shutil.rmtree` の完全撤廃（機能WW.2）
  - P1: Evidence 実検証（`verify_bundle`）の義務化、状態厳格化、全体検証ゲート合格、および Evidence Bundle 生成・検証（機能WW.3）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [ ] 1. プロセス同一性照合（起動時 OS 開始識別子記録 & 復旧時照合不一致での停止保留）の実装と単体テスト（Red-First）（WW.1）
- [ ] 2. Worktree 削除安全境界の復元（直接 `shutil.rmtree` 撤廃 & `WorktreeManager` 経由安全削除への一本化）の実装と単体テスト（Red-First）（WW.2）
- [ ] 3. Evidence 実検証（`verify_bundle`）の義務化・復旧状態厳格化、全体検証ゲート合格確認（182+ テスト）、および Evidence Bundle 生成・検証（WW.3）

## 止まっていること・判断待ち
- なし
