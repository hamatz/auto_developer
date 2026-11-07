# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 8: Morning Brief の実証跡連動・フェイルセーフ化
  - P1: 実 Evidence Record の直接照合（レコード不在時は APPROVE を表示せず UNVERIFIED (No Evidence Record) と表示）
  - P2: Tree SHA 改ざんや Checker REJECT 時の明示的フラグ付け
  - P3: 全体サマリー（Executive Summary）の証跡ガード（未検証タスク混入時の警告）
  - P4: Git リモート（origin）設定有無に応じた操作案内（未設定時の git push 抑止）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. MorningReporter の Evidence Record 照合テスト（レコードなしで APPROVE 詐称不可、改ざん検知、リモート未設定時の案内）の作成（Red）
- [x] 2. `MorningReporter`（`src/auto_developer/reporter.py`）の実装（実レコード照合、フェイルセーフ判定、リモート検出）
- [x] 3. `NightlyOrchestrator`（`src/auto_developer/orchestrator.py`）との結合確認
- [x] 4. 総合検証（`scripts/verify_local.sh`）とコミット

## 止まっていること・判断待ち
- なし (全ステップ完了)


