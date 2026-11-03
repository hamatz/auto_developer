# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- ChatGPT 6.1-sol レビュー対応（Phase 1: Trustworthy Runner & 防壁強化）

## ステップ
- [x] 1. 足元の整合性修正: .github/workflows/verify.yml の Python 化、scripts/verify_local.sh の静的検査拡充、--dry-run の副作用排除
- [x] 2. 防壁強化 (Red-First): GitGuard で未追跡ファイル（??）を含む保護ファイル検査のテスト作成と実装
- [x] 3. 隔離退避の実装: ロールバック時に作業成果を抹消せず quarantine ブランチに退避保存する機構の実装
- [x] 4. 証跡ゲートの実装: verify-step による証跡（Evidence Token）発行と commit-step での検証強制
- [ ] 5. 受入テスト拡充とローカル検証・CI整合確認

## 止まっていること・判断待ち
- なし

