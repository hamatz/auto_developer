# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 3: Pilot 03 フィードバックに基づく失敗回復・監査履歴・クリーン復元強化 (機能S, 機能T, 機能U)

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- `test_feature_h_maker_no_diff_fails_and_quarantines` (line 854) および `test_feature_p_task_failure_preserves_uncompleted_and_stops_safely` (line 1572) は、本線の `PLAN.md` に `[FAILED]` の未コミット差分が残ることをアサートしていた。
  - しかし、Pilot 03 P1 課題（機能S）にて、本線 Git 作業ツリーが dirty だと次回起動時のクリーンツリー検査 `assert_clean_working_tree()` と衝突することが判明した。
  - そのため、本線ツリーは checkpoint に完全クリーン復元（差分ゼロ）し、タスク失敗の正本は `job_state.json` / `NIGHTLY_REPORT.md` / Quarantine ブランチ側に保全する仕様へ進化させる。これに伴いアサートを本線クリーン検証および `job_state.json` 照合へ更新。
- `test_cli_morning_brief_subcommand` (line 648) および `test_feature_f_morning_report_generation` (line 609) は、隔離時 Morning Brief に `--resume` が含まれることをアサートしていた。
  - しかし、Pilot 03 P1 課題（機能T）にて、FAILED ジョブに対して `--resume` を実行すると安全策により起動拒否されるため、実行不可能な案内となっていた。
  - そのため、隔離時は実行不可能な `--resume` を排除し、実行可能な手動引き継ぎ手順（`git checkout {branch}`）を案内する仕様へ更新。アサート対象を `git checkout` に更新する。

## ステップ
- [x] 1. 失敗隔離時の本線完全クリーン復元の実装 (機能S)
- [x] 2. Morning Brief 復旧案内の刷新（実行不可能な --resume 排除）(機能T)
- [ ] 3. 試行単位 Attempts 履歴の完全監査記録の実装 (機能U)
- [ ] 4. 総合回帰検証 (全単体テスト・静的型検査・統合テスト)

## 止まっていること・判断待ち
- なし (Step 1 に着手)

