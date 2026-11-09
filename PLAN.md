# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 46: Stage 5 — 免疫保護・厳格なBefore/After証明・教訓注入と自己改善ループの真の完成 (Stage 5 Hardening Immunity, Strict Proof, and Next-Maker Lesson Injection)
  - 免疫テスト非破壊的確定と衝突拒絶: `AuditResolutionManager.resolve_task()` において、既存免疫テストの存在事前検査を行い、同一ハッシュなら冪等成功、異なるハッシュなら `IMMUNITY_COLLISION` で即座に拒絶して既存免疫テストの改変・破壊を完全防止（機能FFF.1）
  - 厳格な Before Red 実証と改変不能な Proof 拘束: `AuditDispatcher` で Red 検証時の証跡（Before Commit SHA, 再現テスト SHA-256, GENUINE_RED 証跡）を `AuditStore` に永続化し、`resolve_task()` で厳格照合の上、`red_green_proof` に暗号学的に拘束（機能FFF.2）
  - LESSONSフェイルクローズ & モデル独立性検証厳格化: `record_lesson()` 失敗時の `LEARNING_RECORD_FAILED` 停止、および `verify_model_independence()` における空モデル・unknownファミリの Fail-Closed 拒絶（機能FFF.3）
  - 次回Makerへの教訓自動検索・注入機構と完全閉ループE2E受入試験: `LessonInjector` による関連 Trap / Rule の Maker コンテキスト自動注入、および監査から教訓注入まで繋がる完全閉ループE2E受入試験、全体検証ゲート（266+ テスト）合格確認、Evidence Bundle 生成（機能FFF.4）

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- 現時点で既存テストの変更予定なし。

## ステップ
- [x] 1. 免疫テスト非破壊的確定（ハッシュ一致で冪等、不一致で IMMUNITY_COLLISION 拒絶）の実装と単体テスト（Red-First）（FFF.1）
- [x] 2. 厳格な Before Red 実証・検証記録の AuditStore 永続化と red_green_proof 暗号拘束の実装と単体テスト（Red-First）（FFF.2）
- [x] 3. LESSONS記録のフェイルクローズ化 (LEARNING_RECORD_FAILED) と異系統モデル判定厳格化の実装と単体テスト（Red-First）（FFF.3）
- [ ] 4. 次回Makerへの教訓検索・注入機構 (LessonInjector) の実装と、自己改善ループ全通しE2E受入試験・全体検証ゲート合格（FFF.4）

## 止まっていること・判断待ち
- なし
