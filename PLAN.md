# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 26: Stage 2 完全完了 (RC 復旧時の証拠欠落ガード: Sol氏レビュー対応 P1) & Stage 3 MultiTeamOrchestrator 始動
  - P1: `recover_interrupted_job` における証拠欠落（verified_commits未記録、rc_commit/統合ブランチ未存在）時の Fail-Closed 遮断（機能FF.1, FF.2）
  - 回帰実証: 決定論的受入・回帰テスト実証（機能FF.3）
  - Stage 3 始動: MultiTeamOrchestrator 基本データ構造と契約（機能GG.1, GG.2）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [ ] 1. `recover_interrupted_job` における証拠欠落時の Fail-Closed 遮断（FF.1, FF.2）の実装と単体テスト（Red-First）
- [ ] 2. Stage 3 MultiTeamOrchestrator の基本データ構造（`TeamWorkerSpec`, `MultiTeamConfig`, `MultiTeamResult`）と契約（GG.1, GG.2）の実装と単体テスト
- [ ] 3. 決定論的受入・回帰テスト実証（FF.3）、全体検証ゲート合格確認、および Evidence Bundle 生成・検証

## 止まっていること・判断待ち
- なし
