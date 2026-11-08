# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 42: Stage 4 — OS権限ロックダウン & TestResult構造化判定 (OS-level Permission Lockdown & Structured TestResult Verification)
  - TestResult 構造化判定: 出力文字列マッチを廃止し、Python `unittest.TestResult` の構造化情報（`testsRun >= 1`, `len(failures) >= 1`, `len(errors) == 0`）により厳密判定。例外偽装（`OSError("AssertionError: ...")`）を確実に排除（機能BBB.1）
  - OS権限レベルの本体作業ツリー読取専用ロックダウン: 候補テスト実行中、対象リポジトリから書き込み権限を一時剥奪（`a-w`, `0o555`/`0o444`）し、絶対パス等による本体改変を `PermissionError` で物理遮断（機能BBB.2）
  - 決定論的受入実証 & 全自動検証ゲート完全合格（222+ テスト）（機能BBB.3）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- なし（既存テストの破壊的変更は行わず、新規テスト追加と既存合格の維持を徹底）

## ステップ
- [x] 1. TestResult 構造化判定（`testsRun >= 1`, `len(failures) >= 1`, `len(errors) == 0`、例外偽装排除）の実装と単体テスト（Red-First）（BBB.1）
- [ ] 2. OS権限レベルのリポジトリ読取専用ロックダウン（`0o555`/`0o444`、例外安全な権限復元）の実装と単体テスト（Red-First）（BBB.2）
- [ ] 3. 統合受入実証（例外偽装排除 & 絶対パス書き換え阻止）と全体検証ゲート合格確認、Evidence Bundle 生成（BBB.3）

## 止まっていること・判断待ち
- なし
