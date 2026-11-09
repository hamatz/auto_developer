# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 47: Stage 6 — Level 7 実運用接続・証拠イミュータブル化・完全閉ループ受入 (Stage 6 Level 7 Production Wiring, Immutable Proofs, and Closed-Loop Acceptance)
  - CommandRunnerAdapter による教訓の安全な注入と実プロセス受入検証: `CommandRunnerAdapter.run_maker()` において、標準入力（stdin）へのパイプ供給やコンテキストファイルを通じて教訓を Maker サブプロセスへ安全に渡し、シェル展開破壊を防止しつつ実プロセス受入テストで検証（機能GGG.1）
  - AuditStore の初回登録確定とコードハッシュ再計算・改ざん上書き拒絶: `AuditStore.save_finding()` において、初回登録時に再現コードの SHA-256（`code_hash`）を不変値として確定し、再保存時に既存ハッシュと異なる場合は `CODE_HASH_TAMPERED` 例外で拒絶して Before Red 証跡の改変耐性を保証（機能GGG.2）
  - 独立監査ランナーにおける設定モデルと実起動モデルの完全一致強制: `IndependentAuditRunner.audit()` において、設定上の `config.auditor_model` と実際に起動される `self.model_name` が完全一致することを機械的に検証し、偽装・不一致を即座に拒絶（機能GGG.3）
  - シードバグを用いた本番経路 Level 7 E2E 全通し実証と全体検証ゲート合格: モックを排除した実 `CommandRunnerAdapter` による本番経路 E2E（独立監査 → 隔離Red実証 → PLAN登録 → Maker教訓実注入・修正 → After Green → 免疫非破壊昇格 → LESSONS追記 → 次回Maker教訓自動注入）の一気通貫実証、全体検証ゲート（278+ テスト）合格確認、Evidence Bundle 生成（機能GGG.4）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- 現時点で既存テストの変更予定なし。

## ステップ
- [x] 1. CommandRunnerAdapter による教訓の安全な注入（stdin / context ファイル経由）の実装と実プロセス受入テスト（Red-First）（GGG.1）
- [x] 2. AuditStore の初回登録確定とコードハッシュ再計算・改ざん上書き拒絶の実装と単体テスト（Red-First）（GGG.2）
- [x] 3. IndependentAuditRunner の設定モデルと実起動モデルの完全一致強制の実装と単体テスト（Red-First）（GGG.3）
- [x] 4. シードバグを用いた本番経路 Level 7 E2E 全通し実証・全体検証ゲート合格・Evidence Bundle 生成（GGG.4）

## 止まっていること・判断待ち
- なし
