# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 45: Stage 4 — 異系統LLM独立監査ランナー & Before/After確定 (Stage 4 Cross-Model Independent Audit Runner & Evidence Confirmation)
  - 異系統LLM独立監査ポリシーとモデル強制: `AuditConfig` / `OrchestratorConfig` において `maker_model` と `checker_model` / `auditor_model` のモデルファミリを検証し、同一モデルの自己承認を Fail-Closed で拒絶して異系統LLM（Maker=Gemini 3.8 Flash, Reviewer=Claude Opus 5.5 等）を機械的強制（機能EEE.1）
  - 構造化監査プロンプトと独立監査役アダプタ: SPEC.md と git diff のみを厳格に入力とし、独立プロンプトから `list[AuditFinding]` を構造化抽出・パースする `IndependentAuditRunner` の実装（機能EEE.2）
  - Before Red / After Green 証跡バインディングと免疫テスト自動昇格: AUD タスク修正時に修正前の Red 実証および修正後の Green 実証を Evidence Record に拘束し、合格テストを `tests/immunity/` へ安全自動昇格（機能EEE.3）
  - LESSONS.md への自動教訓記録と全体検証ゲート合格確認: 修正完了タスクから Finding 情報を抽出し `LESSONS.md` に自動永続化、受入実証テスト追加、全体検証ゲート（252+ テスト）合格確認、Evidence Bundle 生成（機能EEE.4）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- 現時点で既存テストの変更予定なし。

## ステップ
- [x] 1. 異系統LLM独立監査ポリシーとモデル強制（同一モデル自己承認拒絶）の実装と単体テスト（Red-First）（EEE.1）
- [x] 2. 構造化監査プロンプトと独立監査役アダプタ（IndependentAuditRunner）の実装と単体テスト（Red-First）（EEE.2）
- [ ] 3. Before Red / After Green 証跡バインディングと免疫テスト自動昇格の実装と単体テスト（Red-First）（EEE.3）
- [ ] 4. LESSONS.md への自動教訓記録と全体検証ゲート合格確認、Evidence Bundle 生成（EEE.4）

## 止まっていること・判断待ち
- なし
