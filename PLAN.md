# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 20: Stage 2 Two-Team Integration Gate (独立自律チーム統合ゲート IntegrationGate)
  - P0: 複数チームの Evidence Bundle 事前監査と統合用ブランチ（`integration/{job_id}`）での安全な順次マージ
  - P0: マージ競合検知時の Fail-Closed 隔離（推測解決の完全排除、main 不変）
  - P0: 統合ブランチ上での全体決定論的検証ゲート実行と Release Candidate 確定
  - P0: 2チーム統合（Core & UI）の決定論的受入テスト完備

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. IntegrationGate 基本構造（`TeamIntegrationInput`, `IntegrationResult`）および Bundle 事前監査ロジックの実装（`src/auto_developer/integration_gate.py`）と単体テスト（Red-First）
- [x] 2. 統合用ブランチの作成と順次マージ処理、および競合検知時の Fail-Closed 隔離ロジックの実装と単体テスト
- [x] 3. 統合後全体検証ゲート実行と合否判定・Release Candidate 発行処理の実装と単体テスト
- [x] 4. 2チーム統合（Core & UI）の決定論的受入テスト追加（`tests/test_integration_gate.py`）および全体検証ゲート合格確認

## 止まっていること・判断待ち
- なし


