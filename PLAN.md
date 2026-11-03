# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- SPEC.md の「Antigravity Autonomous Pipeline Orchestrator (夜間自律稼働エンジン)」の実装と検証

## ステップ
- [ ] 1. pyproject.toml の作成とパッケージ構成設定
- [ ] 2. scripts/nightly_orchestrator.py のデータクラスと TaskDispatcher の実装
- [ ] 3. scripts/nightly_orchestrator.py の GitGuard（クリーン検査・外部差分検査・完全ロールバック）の実装
- [ ] 4. scripts/nightly_orchestrator.py の DeterministicVerifier と MorningReporter の実装
- [ ] 5. scripts/nightly_orchestrator.py の CLI 引数処理とメイン実行ループの実装
- [ ] 6. 受入テスト（tests/test_nightly_orchestrator.py）の全通過（Green）と verify_local.sh の検証
- [ ] 7. skills/nightly-run/SKILL.md の作成（Antigravity ワークスペーススキル定義）
- [ ] 8. PROMPTS.md への Maker / Checker 無記憶プロンプトテンプレートの追記
- [ ] 9. Checker による独立査読（SPEC.md と git diff のみでの検証）

## 止まっていること・判断待ち
- なし
