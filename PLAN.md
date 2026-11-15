# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 56: Stage 7 — 制御フロー変数合流の不確定性保護・安全側フォールスルーと受入ハーネス確定（SPEC.md 機能PPP）
  - 条件分岐（`if`/`while`）における変数合流（Merge）と不確定性保護（機能PPP.1 / Sol氏 Phase 55 P1指摘）
  - 静的到達不能コード（`if False:` 等）の判定抑止と不確実性の安全側判定（機能PPP.2 / Sol氏 Phase 55 提言）
  - 判定の不確実性保護・受入ハーネスと Level 7 E2E 完走確認（機能PPP.3）
  - 最終 Evidence Bundle 生成・受入ハーネス確定と正式独立第三者監査移行準備（機能PPP.4）
- スコープ外（Phase 57 以降）: 実際の異系統実外部LLM API（Anthropic Claude Opus / OpenAI GPT）との本番接続による未知の欠陥探索・自律修正サイクル。

## 作業手順（Phase 56 から更新・LESSONS.md 2026-11-15 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能PPP/,/^## 3\./' SPEC.md > .dev/checker/spec_ppp.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- PPP.1: `tests/test_red_verifier.py` に条件分岐（`if`/`while`）後に変数を評価するテスト（`x = 1; if compute_val(1) == 2: x = 2; self.assertEqual(x, 2)`）が TRIVIAL_RED として誤拒絶されない単体テストを追加。
- PPP.2: `tests/test_red_verifier.py` に静的到達不能な分岐（`if False:` 等）のアサーションが TRIVIAL_RED の誤判定根拠にならない単体テストを追加。
- PPP.3: `tests/test_level7_real_loop_e2e.py` および `tests/test_independent_audit_runner.py` に不確定性が安全側に倒され、受入ハーネスおよび実ループが完走する受入テストを追加。
- PPP.4: `NIGHTLY_REPORT.md` を更新し、v0.7.0 受入ハーネス確定と実外部モデル移行準備を記載。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. 条件分岐（`if`/`while`）における変数合流（Merge）と不確定性保護（Red-First）（PPP.1）
- [ ] 2. 静的到達不能コード（`if False:` 等）の判定抑止と不確実性の安全側判定（Red-First）（PPP.2）
- [ ] 3. 判定の不確実性保護・受入ハーネスと Level 7 E2E 完走確認（Red-First）（PPP.3）
- [ ] 4. 最終 Evidence Bundle 生成・受入ハーネス確定と正式独立第三者監査移行準備（PPP.4）

## 止まっていること・判断待ち
- なし。Sol 氏の指摘（条件分岐後の変数合流不備、静的確定できない不確定性の安全側扱い）に対応する Phase 56（機能PPP）に着手可能。
