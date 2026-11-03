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
- [x] 機能A (直列タスク抽出と状態更新): `PLAN.md` から未実行タスク（`- [ ]`）を上から順に1件ずつ取得し、完了時は `- [x]`、失敗時は `- [ ] Task **[FAILED]**` に更新する。
- [x] 機能B (クリーンツリー事前検証): 起動時に `git status --porcelain` を検査し、未コミットの変更が存在する場合は Exit 2 で起動を拒否する。
- [x] 機能C (外部不変条件検査): 各タスクのMaker実装後、検証スクリプト実行前に `git diff` および `git status --porcelain` を検査し、未追跡ファイル含めて保護対象（`.github/**`, `SPEC.md`, `scripts/verify_local.sh`, `tests/immunity/**`）への変更・作成を検知した場合はテスト実行を行わず直ちにQuarantine（隔離ロールバック）を発動する。
- [x] 機能D (ローカル検証ゲート実行): `scripts/verify_local.sh` を実行し、型検査・リンター・単体テストがすべて Exit 0 で通過した時のみ合格と判定し、暗号学的証跡（Evidence Token）を発行する。
- [x] 機能E (隔離退避 & クリーンロールバック): 失敗時、作業ツリーの差分・成果物を破棄せず `quarantine/task-<id>-*` ブランチへ退避コミットした上で、本線作業ツリーを直前のチェックポイントに完全復元（`git reset --hard && git clean -fd`）し、`logs/nightly/task_<id>_failure.log` にエラーダンプを保存してパイプライン全体を即座に安全停止する（Exit 1）。
- [x] 機能F (朝次レポート生成): 正常完了時およびFail-Fast停止時に、完了タスク一覧・隔離ブランチ・失敗理由・残タスクをまとめた `NIGHTLY_REPORT.md` を自動生成する。
- [x] 機能G (証跡ベース確定ゲート): `commit-step` は、直前の `verify-step` で発行された有効な Evidence Token を検証・消費しない限り、コミットを拒否（Exit 3）する。未検証コードの混入を物理遮断する。
- [x] 機能H (Fail-Closed Runner & 検証ゲート不変性):
  1. `cli.py` / `DEFAULT_VERIFY_SH` に `|| true` 等の失敗握りつぶしを含めず、テスト未設定や失敗時は Exit 0 以外で終了する。
  2. 本番実行時（`--mock` 未指定時）に Maker/Checker が未設定の場合は Exit 2 で起動を拒否する。
  3. Maker 実行後に作業ツリーに差分が存在しない場合（No-Op）、タスク未達成として合格させず安全停止（Exit 1）する。
- [x] 機能I (暗号学的ツリー拘束・Evidence Binding):
  1. `verify-step` / Verifier は、検証成功時の作業ツリー状態（ステージ済みGit Tree SHA）および `SPEC.md` のハッシュを Evidence Token と内部証跡レコードに拘束（Bind）して発行する。
  2. `commit-step` は、確定直前のステージ済みツリー SHA と `SPEC.md` ハッシュを再計算し、Evidence に記録された値と完全一致しない限りコミットを拒否（Exit 3）する。検証後の事後改ざんを物理遮断する。
- [x] 機能J (安全な隔離退避・データ消失防止 Safe Quarantine):
  1. 失敗時、未追跡ファイルおよび `.gitignore` 対象の生成物を含めた作業ツリー全体の物理スナップショット（アーカイブ）を `.dev/quarantines/` または隔離ブランチへ完全保全する。
  2. 退避の正常完了が確認されるまで `git reset --hard` や `git clean` 等の破壊的リセット操作を一切実行してはならない。退避処理で例外が発生した場合はリセットを中止して即座に停止する。
- [x] 機能K (Checker 構造化判定 & 型検査完全性):
  1. `CommandRunnerAdapter.run_checker()` は JSON 構造化結果 (`{ "decision": "APPROVE" | "REJECT", ... }`) を解析し判定する。"NOT APPROVED" などの否定文が誤って承認される誤判定を物理遮断する。
  2. 検証ゲートの `mypy` 対象を `src scripts` に拡大し、全パッケージ実装の静的型検査を通過させる。
- [x] 機能L (安全なクラッシュ復旧と状態遷移):
  1. ジョブ状態は `QUEUED`, `RUNNING`, `SUSPENDED`, `COMPLETED`, `FAILED` の状態遷移を定義し、永続化する。
  2. `--resume` 実行時、`can_resume()` により再開可能状態であることを厳密に検証し、完了済み（COMPLETED）または隔離停止（FAILED）ジョブの意図しない再処理・巻き戻しを拒否する。
  3. コミットフェーズでのクラッシュ時、Git 履歴を照合してタスクコミット完了済みか判定し、二重コミットを防止する。
- [ ] 機能M (最終確定ツリー整合性 & 安全な隔離・復旧):
  1. `commit-step` / オーケストレーターは `PLAN.md` のタスク完了更新を含めた最終作業ツリーに対して Evidence を発行・検証・消費し、生成されたコミットの Tree SHA (`git rev-parse HEAD^{tree}`) が Evidence の `tree_sha` と完全一致することを保証する（無検証コード確定の物理遮断）。
  2. `quarantine_and_restore()` は `git add -A -f` を廃止して `.gitignore` を厳密に遵守し、シークレット（`.env*`, `*.pem`, `*.key`, `*token*` 等）の Git 格納を物理遮断する。Recovery Snapshot においてもシークレットパターンを除外する。
  3. `JobState.save()` は一時ファイルと `os.replace` によるアトミック書き込みを行い、クラッシュ時の JSON 破損を防止する。また `--resume` 時に HEAD が `checkpoint_hash` と異なる場合は巻き戻しを拒否する。
  4. `DEFAULT_VERIFY_SH` は `tests/` が存在しない、またはテストが未構成の場合に Exit 1 で終了する。

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
