# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 1 ドキュメント刷新 & チュートリアル（試しに使ってみる手順）の整備

## ステップ
- [x] 1. README.md の刷新: 新アーキテクチャ解説と「試しに使ってみるクイックスタート（Hands-on Walkthrough）」の追加
- [x] 2. docs/designs/autonomous_pipeline_orchestrator.md の最新化（JobState, RunnerAdapter, Quarantine, EvidenceGate）
- [x] 3. skills/nightly-run/SKILL.md と PROMPTS.md の手順整備
- [x] 4. verify_local.sh による整合性確認とコミット

## 止まっていること・判断待ち
- なし

