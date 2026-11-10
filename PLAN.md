# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 48: Stage 7 — Learning Proof のGitコミット拘束とMakerインターフェース正式化 (SPEC.md 機能HHH)
  - 修正前後の実コミットに対して同一再現テストを Red/Green 実行し、その結果を Proof に固定する。未コミット状態・コミット未前進では解決させない（機能HHH.1）
  - `AUTO_DEV_CONTEXT_FILE` / `AUTO_DEV_LESSONS_FILE` を正式な環境変数インターフェースとして公開し、監査で実際に渡したモデル名を Finding に記録する（機能HHH.2）
  - Maker が修正をコミットする形に Level 7 E2E を更新し、コミット拘束付き Proof と未コミット拒絶を実証する（機能HHH.3）
- スコープ外（Phase 49 以降）: 実LLM（異系統）による監査・修正の受入試験、候補テストのOSレベル隔離

## 作業手順（Phase 48 から適用・LESSONS.md 2026-11-10 の教訓に基づく）
- Checker への入力は、SPEC の該当節を要約せず原文のまま切り出して渡す:
  `awk '/^- \[ \] 機能HHH/,/^## 3\./' SPEC.md` の出力と、`git diff <checkpoint>`（未追跡ファイルは `git add -N` 後）の出力をそのまま貼る。自分の言葉で書き直さない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- HHH.1 により `resolve_task()` は Git リポジトリ・クリーンな作業ツリー・前進したコミットを必須とする。以下の既存テストは Git 管理外の一時ディレクトリで修正をコミットせずに `resolve_task()` を呼んでおり、新しい仕様では正しく拒絶されるため、テスト用の Git 初期化と「Red 時点のコミット → 修正コミット」の手順を追加する。アサーション（期待する成功・拒絶理由・免疫テスト配置・LESSONS 記録）は弱めない。
  - `tests/test_audit_resolution.py`（`resolve_task` 呼び出しを含む全テスト）
  - `tests/test_audit_lesson_recording.py::test_full_self_improvement_e2e_loop`
  - `tests/test_lesson_injection.py::test_e2e_closed_self_improvement_loop`
  - `tests/test_level7_real_loop_e2e.py`（HHH.3 で更新）
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [ ] 1. 修正前後コミットへの Red/Green 証明拘束（未コミット拒絶・コミット未前進拒絶・両コミットでの隔離再実行・Proof 固定）の実装と単体テスト、既存テストのGit化（Red-First）（HHH.1）
- [ ] 2. `AUTO_DEV_CONTEXT_FILE` / `AUTO_DEV_LESSONS_FILE` 環境変数の正式公開と、監査モデル名 `auditor_model` の Finding 記録の実装と単体テスト（Red-First）（HHH.2）
- [ ] 3. コミット拘束付き Level 7 E2E 受入への更新・全体検証ゲート合格・正規 Evidence による Bundle 生成（HHH.3）

## 止まっていること・判断待ち
- なし
