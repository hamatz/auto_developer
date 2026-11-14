# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 54: Stage 7 — 独立第三者監査の実行と6大受入条件の体系的実証（SPEC.md 機能NNN）
  - macOS `sandbox-exec` 読み取り許可範囲の最小権限化（機能NNN.1 / Sol氏 Phase 53 P1指摘）
  - 巧妙な偽 Red（因果関係のない固定値比較等）の AST 解析と排除（機能NNN.2 / Sol氏 Phase 53 P1指摘）
  - 6大受入条件（実行隔離・偽陽性耐性・Evidence改ざん検知・複数ワーカー耐障害性・Multi-LLM境界・Level 7継続自己修復）の体系的監査ハーネス整備（機能NNN.3）
  - 独立監査完走・Evidence Bundle 生成と v0.7.0 リリース準備（機能NNN.4）
- スコープ外（Phase 55 以降）: 実際の異系統実外部LLM API（Anthropic/OpenAI）との本番接続による複数日にわたる無人自律開発パイプライン実稼働。

## 作業手順（Phase 54 から更新・LESSONS.md 2026-11-14 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能NNN/,/^## 3\./' SPEC.md > .dev/checker/spec_nnn.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- NNN.1: `tests/test_red_verifier.py` に macOS `sandbox-exec` 最小権限化（`/Library`, `/private/var`, `/etc` allow 排除）の単体テストを追加。
- NNN.2: `tests/test_red_verifier.py`, `tests/test_audit_dispatch.py` に、プロダクトコードを形骸的に呼び出しつつ固定値アサーションで失敗するテストの検知・隔離テストを追加。
- NNN.3: `tests/test_independent_audit_runner.py` または関連テストに 6 大受入条件検証スイートを追加。
- NNN.4: `tests/test_level7_real_loop_e2e.py` に 6 大受入条件検証の E2E 完走テストを追加。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. macOS `sandbox-exec` 読み取り許可範囲の最小権限化（Red-First）（NNN.1）
- [ ] 2. 巧妙な偽 Red（因果関係のない固定値比較等）の AST 解析と排除（Red-First）（NNN.2）
- [ ] 3. 6大受入条件（実行隔離・偽陽性耐性・Evidence・複数ワーカー・Multi-LLM・継続運用）の体系的監査ハーネス（Red-First）（NNN.3）
- [ ] 4. 独立監査完走・Evidence Bundle 生成と v0.7.0 リリース準備（NNN.4）

## 止まっていること・判断待ち
- なし。Phase 53（機能MMM）がマージされ、Sol 氏の評価により「独立監査開始：GO」が確定。6 大受入条件の体系的実証（機能NNN）に着手可能。
