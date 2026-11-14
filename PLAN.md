# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 53: Stage 7 — 独立監査受入条件の固定と偽陽性Red排除・OS隔離強化 (SPEC.md 機能MMM)
  - Linux 仮想環境マウント包含判定（`is_relative_to`）とサブディレクトリ限定マウント（機能MMM.1 / Sol氏 Phase 52 P1指摘）
  - macOS `sandbox-exec` 読み取り隔離の強化（機能MMM.2 / Sol氏 Phase 52 P1指摘）
  - 偽陽性 Red（Trivial / Fake Red）の自動排除機構（AST静的検証・スタックフレーム実行検証・`TRIVIAL_RED` 隔離）（機能MMM.3 / Sol氏 Phase 52 重要指摘）
  - 独立監査受入条件の固定と Level 7 E2E 完走・Evidence Bundle 生成（機能MMM.4）
- スコープ外（Phase 54 以降）: 実際の異系統実外部LLM API（Anthropic/OpenAI）との本番接続による無人自律開発パイプライン実稼働。

## 作業手順（Phase 53 から更新・LESSONS.md 2026-11-14 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能MMM/,/^## 3\./' SPEC.md > .dev/checker/spec_mmm.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- MMM.1: `tests/test_red_verifier.py` に `~/.venvs/...` や `~/projects/.../.venv` 等のサブディレクトリ配置仮想環境における最小マウント検証テストを追加。
- MMM.2: `tests/test_red_verifier.py` に macOS `sandbox-exec` 読み取り隔離強化の単体テストを追加。
- MMM.3: `tests/test_audit_dispatch.py`, `tests/test_red_verifier.py`, `tests/test_immunity_and_red_acceptance.py`, `tests/test_audit_persistence.py` に、`self.assertEqual(1, 2)` や `self.fail()` 等の偽陽性テストが `TRIVIAL_RED` として `REJECTED` されることを検証するテストを追加。既存テストの Red テストコードが固定値比較を行っていた場合は `src/` の関数を呼び出して正しくプロダクト起因で失敗するテストに整合させる。
- MMM.4: `tests/test_level7_real_loop_e2e.py` に Trivial Red 排除の E2E 検証を追加。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. Linux 仮想環境マウント包含判定（`is_relative_to`）とサブディレクトリ限定マウント（Red-First）（MMM.1）
- [x] 2. macOS `sandbox-exec` 読み取り隔離の強化（Red-First）（MMM.2）
- [x] 3. 偽陽性 Red（Trivial / Fake Red）の自動排除機構（AST静的検証・スタックフレーム実行検証・`TRIVIAL_RED` 隔離）（Red-First）（MMM.3）
- [x] 4. 独立監査受入条件の固定と Level 7 E2E 完走・Evidence Bundle 生成（MMM.4）

## 止まっていること・判断待ち
- なし。Phase 52 がマージされ、P1（Linux 仮想環境の包含関係対応、macOS 読み取り隔離強化、偽陽性 Red 排除）の修正方針が確定済み。
