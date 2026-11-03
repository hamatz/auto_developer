# Product Specification & Invariant Contracts (SPEC.md)

<!--
Elephant（設計セッション）が人間と議論して起草し、仕様査読（記憶を持たないエージェント）の検査を経て確定する。
ここが誤っていれば、以降のすべての検証が誤った物差しになる。人間が必ず読み、承認する。

起点設計書: docs/designs/autonomous_pipeline_orchestrator.md
-->

## 1. 概要 (Overview)
- プロジェクト名称: Antigravity Autonomous Pipeline Orchestrator (夜間自律稼働エンジン)
- 目的 / 解決する課題: 人間不在の夜間時間帯等において、EGM設計書および `PLAN.md` に基づき、タスクを1ステップずつ安全かつ確実に自動完遂する。
- 想定ユーザー & 利用形態: ローカルGit環境で完結するAntigravityワークスペーススキル（`/nightly-run`）およびPython CLIヘルパー（`scripts/nightly_orchestrator.py`）。

## 2. コア要件 (MVP Scope)
<!-- 受入テストと1対1に対応させる -->
- [ ] 機能A (直列タスク抽出と状態更新): `PLAN.md` から未実行タスク（`- [ ]`）を上から順に1件ずつ取得し、完了時は `- [x]`、失敗時は `- [ ] Task **[FAILED]**` に更新する。
- [ ] 機能B (クリーンツリー事前検証): 起動時に `git status --porcelain` を検査し、未コミットの変更が存在する場合は Exit 2 で起動を拒否する。
- [ ] 機能C (外部不変条件検査): 各タスクのMaker実装後、検証スクリプト実行前に `git diff --name-only <checkpoint>` を検査し、保護対象（`.github/**`, `SPEC.md`, `scripts/verify_local.sh`, `tests/immunity/**`）への変更を検知した場合はテスト実行を行わず直ちにQuarantine（隔離ロールバック）を発動する。
- [ ] 機能D (ローカル検証ゲート実行): `scripts/verify_local.sh` を実行し、型検査・リンター・単体テストがすべて Exit 0 で通過した時のみ合格と判定する。
- [ ] 機能E (Strict Fail-Fast & クリーンロールバック): 同一タスクで2回失敗した場合、`git reset --hard <checkpoint> && git clean -fd` を実行して未追跡ファイル含めて初期化し、`logs/nightly/task_<id>_failure.log` にエラーダンプを保存してパイプライン全体を即座に安全停止する（Exit 1）。
- [ ] 機能F (朝次レポート生成): 正常完了時およびFail-Fast停止時に、完了タスク一覧・失敗理由・残タスクをまとめた `NIGHTLY_REPORT.md` を自動生成する。

## 3. 非機能要件・絶対制約 (Constraints & Out of Scope)
<!-- 過剰設計（YAGNI）とスコープ逸脱を防ぐ「やらないこと」リスト -->
- スコープ外: 複数Workerの同時並行Git編集 / 仕様（SPEC.md）の自己改変 / タスクの憶測スキップと継続 / クラウド分散実行 / 外部DB
- 技術スタック制約: Python 3.10+ (CLIヘルパー)、Antigravity スキル、Git
- 依存ライブラリ方針: `pyproject.toml` に `pytest>=8.0.0`, `mypy>=1.9.0`, `ruff>=0.3.0` をピン留めし、それ以外の外部ライブラリは導入しない（標準ライブラリ `dataclasses`, `argparse`, `subprocess`, `pathlib` 等のみ使用）。

## 4. 厳格な不変条件 (System Invariants)
<!-- 破られたら即座にリジェクトされるルール -->
1. データ整合性・クリーン復元: 障害ロールバック時に未追跡ファイル・ゴミスクリプト含めて直前の成功コミットまで完全に巻き戻され、作業ツリーを一切汚染しないこと（`git clean -fd` 必須）。
2. 境界値・安全停止 (Strict Fail-Fast): 2回連続失敗時は推測で後続タスクに進まず直ちにパイプラインを安全停止し、状況を記録すること（AGENTS.md Rule 5 の機械的強制）。
3. 保護アセット不変: `.github/**`, `SPEC.md`, `scripts/verify_local.sh`, 免疫テストの無断改変を検証実行前に物理遮断すること。

## 5. 完了定義 (Definition of Done)
- `bash scripts/verify_local.sh` がすべて成功（Exit 0）していること。
- `pytest tests/test_nightly_orchestrator.py -v` の受入テストスイートがすべて通過していること。
