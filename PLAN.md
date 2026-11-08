# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 44: Stage 4 — 物理クローン隔離 & パイプライン接続修正 (Stage 4 Physical Clone Isolation & Pipeline Interoperability)
  - 候補テストの物理的クローン隔離実行: 元リポジトリへの直接実行およびパーミッション剥奪を廃止し、独立した検証用一時クローン（clean temporary clone）上で実行して元リポジトリの物理的不変性を 100% 保証（機能DDD.1）
  - タスク書式統一と TaskDispatcher 連携: `AuditDispatcher` が追記するタスクに `AUD-<finding_id>.` プレフィックスを付与し、`TaskDispatcher.parse_plan()` / `get_next_pending_task()` との連携を保証（機能DDD.2）
  - 監査Findingと再現テストの永続化管理: `.dev/audit/findings/` および `.dev/audit/repro_tests/` への構造化保存と参照性確保（機能DDD.3）
  - Level 7 E2E受入テストの強化 & 検証ゲート完全合格: `TaskDispatcher.get_next_pending_task()` 経由で Maker へタスクが届く実運用E2E、`os.chmod` 改変阻止テスト、全体検証ゲート（246+ テスト）合格確認、Evidence Bundle 生成（機能DDD.4）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし（既存テストの破壊的変更は行わず、新規テスト追加と既存合格の維持を徹底）

## ステップ
- [ ] 1. 候補テストの物理的クローン隔離実行（元リポジトリ汚染の完全遮断）の実装と単体テスト（Red-First）（DDD.1）
- [ ] 2. タスク書式統一（AUD-<finding_id>. プレフィックス）と TaskDispatcher 連携の実装と単体テスト（Red-First）（DDD.2）
- [ ] 3. 監査Findingと再現テストの永続化管理（.dev/audit/）の実装と単体テスト（Red-First）（DDD.3）
- [ ] 4. Level 7 E2E受入テストの強化（Makerタスク取得・修正実証 & chmod改変阻止）と全体検証ゲート合格確認、Evidence Bundle 生成（DDD.4）

## 止まっていること・判断待ち
- なし
