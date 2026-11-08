# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 43: Stage 4 — 自己改善パイプライン接続 & 監査自動タスク登録 (Stage 4 Self-Improvement Pipeline & Audit Task Dispatching)
  - 防壁の穴封鎖: 新規免疫テスト追加時の既存テスト不干渉検証（Mutual Non-Interference Guard）、トップレベルAST検査（動的モンキーパッチ・実行文の遮断）、root実行時のFail-Closed（機能CCC.1）
  - 独立監査役ランナーと構造化 Finding モデル: `AuditRunner` および `AuditFinding` モデルの実装（機能CCC.2）
  - 監査指摘から Red 検証を経て `PLAN.md` への修正タスク自動登録: `RedTestVerifier` の結果に基づく `TaskDispatcher` への自動タスクディスパッチ（機能CCC.3）
  - 決定論的受入実証 & 全自動検証ゲート完全合格（227+ テスト）（機能CCC.4）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- `tests/test_red_verifier.py`: `test_rejects_empty_or_no_test_methods` において `x = 10` のトップレベル代入文が含まれていたため、機能CCC.1のトップレベルAST検査導入に伴い `TOP_LEVEL_EXEC_FORBIDDEN` となっていた。純粋にテストが存在しないケースを検証するためコメントのみのコードへ更新。

## ステップ
- [x] 1. 防壁の穴封鎖（既存テスト不干渉検証、トップレベルAST検査、root実行Fail-Closed）の実装と単体テスト（Red-First）（CCC.1）
- [x] 2. 独立監査役ランナー `AuditRunner` と `AuditFinding` モデルの実装と単体テスト（Red-First）（CCC.2）
- [x] 3. 監査指摘からRed検証を経て `PLAN.md` への修正タスク自動登録（パイプライン接続）の実装と単体テスト（Red-First）（CCC.3）
- [x] 4. 決定論的受入実証（シードバグの監査指摘 → 隔離Red検証 → PLAN.md自動登録）と全体検証ゲート合格確認、Evidence Bundle 生成（CCC.4）

## 止まっていること・判断待ち
- なし
