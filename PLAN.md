# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 4: Pilot 04 フィードバックに基づくクラッシュ耐性・トランザクション復旧基盤 (機能V, 機能W, 機能X)
  - Scenario 03: コミット直後クラッシュからのコミット再利用・JobState自動前進補正 (P0-1)
  - Scenario 04: Quarantine途中クラッシュからの安全復帰・隔離完了 (P0-2)
  - 診断・計画・適用の分離型 CLI (`auto-dev recover inspect|plan|apply`)

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- （現時点で既存テストの変更予定なし。新規リカバリテスト・モジュールを追加）

## ステップ
- [x] 1. `JobState` への `base_branch` 追跡と永続化の実装 (機能V)
- [x] 2. `RecoveryEngine.inspect()` によるクラッシュ不整合診断の実装 (機能W)
- [x] 3. `RecoveryEngine.plan()` & `apply()` による復旧計画生成と自動適用 (機能X)
- [x] 4. CLI への `auto-dev recover` (inspect / plan / apply) サブコマンド統合
- [ ] 5. 総合回帰検証 (Pilot 04 Golden Master を含む全単体テスト・型検査・リンター)

## 止まっていること・判断待ち
- なし (Step 5 総合回帰検証に着手)

