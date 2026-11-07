# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 10: Multi-Team Foundation (Stage 1: Worktree & Boundary)
  - P1: チーム分担・スコープ定義 `TeamManifest`（`owns`, `depends_on`, `contracts`）のデータモデルと検証
  - P2: 独立作業空間 `WorktreeManager`（`.dev/worktrees/<team_id>/` の安全な作成・一覧・衝突検知・クリーンアップ）
  - P3: チーム領空侵犯を防ぐ `BoundaryGuard`（`owns` 逸脱変更の即時検知とQuarantine発動）
  - P4: 複数Worktreeの共存とBoundary違反隔離の決定論的受入テスト

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. `TeamManifest` のデータモデルとパーサー実装（`src/auto_developer/manifest.py`）および単体テスト
- [x] 2. `WorktreeManager` の作成・一覧・衝突検知・削除処理の実装（`src/auto_developer/worktree.py`）および単体テスト
- [x] 3. `BoundaryGuard` の変更許可スコープ検査と隔離連携の実装（`src/auto_developer/boundary.py`）および単体テスト
- [ ] 4. 複数Worktreeの共存と領空侵犯（Boundary違反）即時ロールバックの決定論的受入テスト（`tests/test_multi_team_foundation.py`）

## 止まっていること・判断待ち
- なし（`/nightly-run` 実行準備完了）


