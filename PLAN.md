# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 55: Stage 7 — AST 因果解析の厳密化と独立監査受入ハーネス確定（SPEC.md 機能OOO）
  - AST 解析の文実行順序化（Program Order）による後続代入汚染の完全排除（機能OOO.1 / Sol氏 Phase 54 P1指摘）
  - 条件付き（`if` / `while`）`self.fail()` の因果依存ガード判定（機能OOO.2 / Sol氏 Phase 54 P1指摘）
  - 独立監査受入ハーネス・Level 7 E2E 完走確認と受入テスト拡充（機能OOO.3）
  - 報告書の正確化（受入ハーネス実証）・Evidence Bundle 生成と v0.7.0 リリース準備（機能OOO.4）
- スコープ外（Phase 56 以降）: 実際の異系統実外部LLM API（Anthropic/OpenAI）との本番接続による複数日にわたる無人自律開発パイプライン実稼働。

## 作業手順（Phase 55 から更新・LESSONS.md 2026-11-15 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能OOO/,/^## 3\./' SPEC.md > .dev/checker/spec_ooo.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- OOO.1: `tests/test_red_verifier.py` に後続代入（reassignment）を含む正常テストが TRIVIAL_RED として誤拒絶されない単体テストを追加。
- OOO.2: `tests/test_red_verifier.py` に条件分岐（`if`/`while`）内の `self.fail()` が製品コード呼出に依存する場合に正常受理される単体テストを追加。
- OOO.3: `tests/test_level7_real_loop_e2e.py` および `tests/test_independent_audit_runner.py` に上記ケースを含む受入テストを追加。
- OOO.4: `NIGHTLY_REPORT.md` を更新し、表現を「受入ハーネス実証」として正確化。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. AST 解析の文実行順序化（Program Order）による後続代入汚染の排除（Red-First）（OOO.1）
- [x] 2. 条件付き（`if`/`while`）`self.fail()` の因果依存ガード判定（Red-First）（OOO.2）
- [x] 3. 独立監査受入ハーネス・Level 7 E2E 完走確認と受入テスト拡充（Red-First）（OOO.3）
- [ ] 4. 報告書の正確化（受入ハーネス実証）・Evidence Bundle 生成と v0.7.0 リリース準備（OOO.4）

## 止まっていること・判断待ち
- なし。Sol 氏の指摘（AST 後続代入による汚染、条件付き self.fail の誤拒絶）に対応する Phase 55（機能OOO）に着手可能。
