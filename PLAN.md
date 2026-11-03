# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 1 Hardening: 4つのP0問題の解消と安全な自律運転の確立

## ステップ
- [x] 1. Gate & Runner の Fail-Closed 化（DEFAULT_VERIFY_SH の || true 削除、Runner未設定時の拒否、Maker差分なし時の安全停止）(Red-First)
- [x] 2. Evidence Binding（Git Tree SHA, Spec SHA, Checker判定を証跡に拘束し、確定時に再照合）(Red-First)
- [x] 3. Safe Quarantine（無視ファイルや未追跡ファイルを完全保護し、退避確認前にリセットしない）(Red-First)
- [x] 4. Checker の構造化（JSON判定）と CI 検査範囲拡大 (Red-First)
- [x] 5. JobState 状態遷移と安全なクラッシュ復旧 (Red-First)

## 止まっていること・判断待ち
- なし（全ステップ完了）

