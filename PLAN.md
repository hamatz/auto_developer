# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 28: Stage 3 完全実証 (CLIワーカー起動修正・Evidence保全・実サブプロセス2チームE2E統合: Sol氏レビュー対応 P0, P1, P2)
  - P0: 標準CLIワーカー起動コマンドの引数順序修正 (`--work-dir <wt> run`) & 引数・環境変数拡張 (機能KK.1, KK.2)
  - P1: 統合前 Evidence Bundle の Worktree 外永続保全 (`.dev/integration_jobs/<job_id>/evidence/<team_id>.zip`) & SHA-256 照合・返却パス更新 (機能LL.1, LL.2)
  - P2 / Stage 3 本番: 実サブプロセス 2チーム並行稼働 & Core/UI 意味的機能結合 E2E 受入実証 (機能MM.1, MM.2)

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし

## ステップ
- [x] 1. 標準CLIワーカー起動コマンド修正 & パラメータ拡張（KK.1, KK.2: `--work-dir` 順序修正、`extra_args`/`env` 設定）の実装と単体テスト（Red-First）
- [ ] 2. 統合前 Evidence Bundle の Worktree 外保全 & ハッシュ照合・返却パス更新（LL.1, LL.2）の実装と単体テスト（Red-First）
- [ ] 3. 実サブプロセス 2チーム並行実行 & 意味的機能結合 E2E 受入実証（MM.1, MM.2）、全体検証ゲート合格確認、および Evidence Bundle 生成・検証

## 止まっていること・判断待ち
- なし
