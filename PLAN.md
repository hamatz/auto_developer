# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 36: Stage 3 親プロセス強制終了耐性 & ワーカークラッシュ自律復旧 (Sol氏提案マイルストーン)
  - P1: 永続 Worker Registry 機構の実装（機能VV.1）
  - P1: 3状態自律復旧エンジン `recover_multi_team_job()` の実装（機能VV.2）
  - 受入実証: 親プロセスSIGKILL疑似クラッシュ障害注入テスト、全体検証ゲート通過、および Evidence Bundle 生成・検証（機能VV.3）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. 永続 Worker Registry 機構（VV.1: `.dev/integration_jobs/<job_id>/workers.json` への原子的一時保存 & 状態追跡）の実装と単体テスト（Red-First）
- [x] 2. 3状態自律復旧エンジン `recover_multi_team_job()`（VV.2: Case 1 孤児回収, Case 2 成果救出, Case 3 前進完了）の実装と単体テスト（Red-First）
- [x] 3. 親プロセスSIGKILL疑似クラッシュからの完全自律復旧テスト実証、全体検証ゲート合格確認（181+ テスト）、および Evidence Bundle 生成・検証（VV.3）

## 止まっていること・判断待ち
- なし
