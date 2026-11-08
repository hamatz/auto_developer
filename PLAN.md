# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 23: Stage 2 統合クラッシュ耐性パック (Integration Crash Consistency Pack: Sol氏レビュー対応 P1-A, P1-B, P1-C, P1-D)
  - P1-A: Worktree クリーンアップ成否の `IntegrationResult.details` への明示的記録
  - P1-B: 隔離ブランチリネーム失敗時の Fail-Closed `RECOVERY_REQUIRED` 遮断
  - P1-C: `IntegrationJobState` の原子的永続化と Git 実態照合による再開・復旧
  - P1-D: 強制終了（SIGKILL 模擬）後の自律復旧・再開の決定論的受入テスト実証

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. Worktree クリーンアップ成否の `IntegrationResult.details` 記録・明示化、および隔離ブランチリネーム失敗時の `RECOVERY_REQUIRED` 遮断の実装（CC.1）と単体テスト（Red-First）
- [x] 2. `IntegrationJobState` の原子的永続化と Git 実態照合による再開・復旧（`recover_or_resume`）の実装（CC.2, CC.3）と単体テスト
- [x] 3. マージ途中・検証途中の強制終了（クラッシュ模擬）からの自律復旧・再開決定論的受入テストの実証（CC.4）および全体検証ゲート合格確認

## 止まっていること・判断待ち
- なし
