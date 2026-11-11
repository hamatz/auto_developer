# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 49: Stage 7 — コミット指定検証のFail-Closed・Proof詳細化・実行隔離強化 (SPEC.md 機能III)
  - `RedTestVerifier` で commit 指定時の checkout 失敗に対するフォールバック（`copytree`）を物理遮断し、`COMMIT_CHECKOUT_FAILED` で即座に検証拒絶（Fail-Closed）する（機能III.1 / Sol氏 Phase 48 P1指摘）
  - `red_green_proof` にテスト数・出力ハッシュ等の監査詳細を記録し、`LESSONS.md` に Before/After コミット SHA・テストハッシュの参照を記録する（機能III.2 / Sol氏 Phase 48 P2指摘）
  - `RedTestVerifier._execute_test()` のリソース境界・環境制限を強化し、Level 7 クローズドループ E2E 受入試験を更新・実証する（機能III.3）
- スコープ外（Phase 50 以降）: 異系統LLM（実API）によるクロスプロバイダー自律監査・修正の実稼働

## 作業手順（Phase 48 から継続・LESSONS.md 2026-11-10 の教訓に基づく）
- Checker への入力は、SPEC の該当節を要約せず原文のまま切り出して渡す:
  `awk '/^- \[ \] 機能III/,/^## 3\./' SPEC.md` の出力と、`git diff <checkpoint>`（未追跡ファイルは `git add -N` 後）の出力をそのまま貼る。自分の言葉で書き直さない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- III.1: 不正なコミット SHA を指定した検証が、現行ツリーフォールバックによる Green ではなく `COMMIT_CHECKOUT_FAILED`（is_reproduced=False）で拒絶されるテストを追加する。
- III.2: `red_green_proof` に詳細フィールドが追加されるため、既存の Proof 構造アサーションに破壊的変更がないことを確認しつつ新フィールドを検証する。
- III.3: `tests/test_level7_real_loop_e2e.py` において、Proof の詳細値および LESSONS.md に追記されたコミット SHA 参照を検証する。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [ ] 1. コミット指定検証の Fail-Closed 徹底（checkout 失敗時のフォールバック遮断と `COMMIT_CHECKOUT_FAILED` 返却）の実装と単体テスト（Red-First）（III.1）
- [ ] 2. Learning Proof 監査詳細化（テスト数・出力ハッシュ記録）と LESSONS.md コミット拘束参照の実装と単体テスト（Red-First）（III.2）
- [ ] 3. 候補テストのOSレベル実行隔離・リソース境界強化と Level 7 E2E 通し試験・全体検証ゲート合格・正規 Evidence による Bundle 生成（III.3）

## 止まっていること・判断待ち
- なし
