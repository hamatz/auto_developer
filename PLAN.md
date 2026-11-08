# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 24: Stage 2 クラッシュ復旧堅牢化 & 実プロセス SIGKILL 障害注入実証 (Sol氏レビュー対応 P0, P1, P2)
  - P0: `recover_interrupted_job` の完全 Fail-Closed 化（Worktree 実態照合・削除失敗時の `RECOVERY_REQUIRED` 遮断）
  - P1: `IntegrationJobState` の識別子正規表現検証 & `os.fsync` 原子的永続化の堅牢化
  - P1: 障害注入フック（`fault_hook`）の導入および実プロセスへの `SIGKILL` 障害注入（マージ途中・RC発行直後）受入テスト
  - P2: Morning Brief (NIGHTLY_REPORT.md) と Manifest の Tree SHA 完全整合

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. `recover_interrupted_job` の Fail-Closed 化（Worktree 実態照合・削除失敗時の `RECOVERY_REQUIRED` 遮断）および `job_id` 検証・`os.fsync` 原子的永続化の実装（DD.1, DD.2）と単体テスト（Red-First）
- [x] 2. 障害注入フック（`fault_hook`）の導入、および実プロセスへの `SIGKILL` 障害注入（マージ途中・RC発行直後）からの `resume=True` 自律復旧・完遂受入テストの実証（DD.3）
- [x] 3. `MorningReporter` の Tree SHA 厳密整合（DD.4）、全体検証ゲート合格確認、および Evidence Bundle 生成・検証

## 止まっていること・判断待ち
- なし
