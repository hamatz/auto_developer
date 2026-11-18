# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 65: v0.8.0 リリース確定と自律レポート復旧・計測指標の適正化（SPEC.md 機能YYY）
  - 計測指標の実態との乖離解消（`human_commits` → `out_of_path_commits` 改名、`rework_tasks_ratio` の追加、METRICS.md の Phase 64 確定値更新）。
  - NIGHTLY_REPORT.md の自動生成とモーニングブリーフ復旧（対話型 `/nightly-run` でも完全なレポートを生成・表示、bundle / brief 連携）。
  - バージョン整合（0.8.0）とリリースドキュメント整備（CHANGELOG.md, docs/releases/v0.8.0.md）。
  - 最新 HEAD に対する実 Claude Opus 5.5 本番監査の実行と完全性検証済み Evidence Bundle の生成・検証（AUDIT_OUTDATED 解消）。
- スコープ外: Web UI ダッシュボード、自動リリースデプロイ。

## 作業手順（Phase 65・LESSONS.md に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。
  - Maker: Gemini 3.8 Flash (`gemini-3.8-flash`)
  - Checker: Claude Opus 5.5 (`claude-opus-5-5-medium`、Anthropic API 利用。環境変数 `ANTHROPIC_API_KEY` を利用)
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能YYY/,/^## 3\./' SPEC.md > .dev/checker/spec_yyy.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイルがないことを確かめる。
- **査読記録の順序厳守**:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. **Checker 査読直後、commit-step の前に** `python3 scripts/nightly_orchestrator.py record-review --task-id <n> --decision <decision> --checker-model <checker> --maker-model <maker> --reason "<reason>"` を実行する（REJECT 率・手戻りの正確な記録のため）。
  4. Checker が REJECT した場合は commit-step を呼ばず、手直しして再度 1 からやり直す。
  5. Checker が APPROVE した場合のみ `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --maker-model <maker> --checker-model <checker> --checker-decision APPROVE --checker-reason "<reason>"` を実行する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- YYY.1: 既存テストで `human_commits` を検証している箇所を `out_of_path_commits` に更新する。
- 上記以外の既存テストは変更しない。免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. 指標の適正化: human_commits → out_of_path_commits 改名、rework_tasks_ratio の追加、METRICS.md の Phase 64 最終数値更新と注記（Red-First）（YYY.1）
- [x] 2. NIGHTLY_REPORT.md の自動生成とモーニングブリーフ復旧: 独立 report 生成機能、auto-dev brief および bundle との連携（Red-First）（YYY.2）
- [x] 3. バージョン整合（0.8.0）とリリースドキュメント整備（CHANGELOG.md, docs/releases/v0.8.0.md）（YYY.3）
- [ ] 4. 最新 HEAD に対する実 Claude Opus 5.5 本番監査の実行と完全性検証済み Evidence Bundle の生成・検証（YYY.4）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [済] Phase 64 の後片付け（docs/proposals/phase64/ 削除、免疫テスト配置完了）。
- ステップ 4 完了後: Evidence Bundle の最終確認および v0.8.0 タグ・リリースの承認。
