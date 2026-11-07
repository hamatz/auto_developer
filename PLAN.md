# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 11: Multi-Team Stage 1 Hardening & Pipeline Integration (P0/P1 レビュー指摘の解消)
  - P1: `WorktreeManager` のパストラバーサル遮断と安全な削除（`shutil.rmtree` 廃止と成果保全）
  - P2: `TeamManifest` のスキーマ厳格化（`team_id` 正規表現、正の並列数、厳格な真偽値）
  - P3: `BoundaryGuard` の Fail-Closed 化・リポジトリ相対先頭一致 glob・rename 追跡
  - P4: `BoundaryGuard` のパイプライン接続と自動 Quarantine 連携（領空侵犯時の自動退避・本線復元）
  - P5: 決定論的受入テスト（セキュリティ境界と自動隔離の実証）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. `WorktreeManager` のパストラバーサル遮断と安全な削除（`src/auto_developer/worktree.py`）および単体テスト
- [x] 2. `TeamManifest` のスキーマ厳格化と型安全バリデーション（`src/auto_developer/manifest.py`）および単体テスト
- [x] 3. `BoundaryGuard` の Fail-Closed 化・glob 厳格化・rename 追跡（`src/auto_developer/boundary.py`）および単体テスト
- [x] 4. `BoundaryGuard` のオーケストレーター実行経路接続と自動 Quarantine 連携（`src/auto_developer/orchestrator.py`）
- [x] 5. 領域侵犯時の自動 Quarantine と成果保全の実証受入テスト（`tests/test_multi_team_foundation.py`）

## 止まっていること・判断待ち
- なし（`/nightly-run` 実行準備完了）


