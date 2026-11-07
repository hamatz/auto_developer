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
- [x] 機能M (最終確定ツリー整合性 & 安全な隔離・復旧):
  1. `commit-step` / オーケストレーターは `PLAN.md` のタスク完了更新を含めた最終作業ツリーに対して Evidence を発行・検証・消費し、生成されたコミットの Tree SHA (`git rev-parse HEAD^{tree}`) が Evidence の `tree_sha` と完全一致することを保証する（無検証コード確定の物理遮断）。
  2. `quarantine_and_restore()` は `git add -A -f` を廃止して `.gitignore` を厳密に遵守し、シークレット（`.env*`, `*.pem`, `*.key`, `*token*` 等）の Git 格納を物理遮断する。Recovery Snapshot においてもシークレットパターンを除外する。
  3. `JobState.save()` は一時ファイルと `os.replace` によるアトミック書き込みを行い、クラッシュ時の JSON 破損を防止する。また `--resume` 時に HEAD が `checkpoint_hash` と異なる場合は巻き戻しを拒否する。
  4. `DEFAULT_VERIFY_SH` は `tests/` が存在しない、またはテストが未構成の場合に Exit 1 で終了する。
- [x] 機能N (Evidence Record の永続化と監査ログ分離):
  1. `verify_and_consume()` において、認証トークン消費時に証跡を破棄せず、永続監査レコード（`.git/nightly_evidence/archive/task_<id>.json` または `.dev/evidence/task_<id>.json`）として保存し、事後監査を可能にする。
  2. Evidence Record には `task_id`, `checkpoint`, `tree_sha`, `spec_sha`, `issued_at`, `consumed_at`, `status`, `verification`, `checker`, `commit` などの監査メタデータを記録・照合可能とする。
- [x] 機能O (免疫テストディレクトリ初期化と保護):
  1. `auto-dev init` 実行時、人間が承認した受入仕様テストを格納する `tests/immunity/` ディレクトリおよび初期受入テスト `tests/immunity/test_acceptance_sample.py` を自動生成する。
  2. Maker による `tests/immunity/` 配下の改変・新規作成・削除を外部不変条件違反として機械的に遮断・隔離する。
- [x] 機能P (意思決定支援型 Morning Brief の生成):
  1. `NIGHTLY_REPORT.md` において、単なるコミット一覧だけでなく、「成果の要約」「即時動作確認コマンド」「既知の制約・境界条件」「人間への次のアクション」「監査証跡サマリー」を構造的に出力する。
- [x] 機能Q (完全監査レコードの保持とCLI配管強化):
  1. `auto-dev verify-step` 実行時、検証ゲートの成否・終了コード・出力サマリーを Evidence Token の `details.verification` に拘束して発行する。
  2. `auto-dev commit-step` は `--checker-decision` および `--checker-reason`（または `--checker-json`）を受け取り、コミット時に `EvidenceStore.archive_record()` へ渡して永続監査レコードに Checker 査読情報を確実に記録する。Checker 情報が未指定の場合は `UNSPECIFIED`（証拠未取得）として記録・表示する。
- [x] 機能R (Morning Brief のデュアルコマンド案内とチェックポイント分離表示):
  1. Morning Brief の「動作確認コマンド」において、「検証コマンド (Verify)」と「成果物利用コマンド (Run)」を明確に分離して提示する。
  2. レポートの概要テーブルにおいて、開発開始時の「初期チェックポイント (Initial Checkpoint)」と完了時の「最新コミット (Final Commit)」を区別して表示する。
- [x] 機能S (失敗隔離時の本線完全クリーン復元):
  1. タスク失敗時（Quarantine 発動時）、本線 Git 作業ツリー（`PLAN.md` 含む）に未コミットの差分を残さず、直前のクリーンチェックポイントに完全復元（`git status --porcelain` が空）する。
  2. 失敗タスク情報（`**[FAILED]**`）は `job_state.json`、`NIGHTLY_REPORT.md`、および隔離ブランチ側の `PLAN.md` に保全する。
- [x] 機能T (実行可能な復旧案内への刷新):
  1. `NIGHTLY_REPORT.md` において、`FAILED` / 隔離時の案内から、実行拒否される `--resume` を排除し、隔離ブランチの確認・引き継ぎ・破棄などの実行可能な手順を提示する。
- [ ] 機能U (試行単位 Attempts 履歴の完全監査記録):
  1. タスク実行における各 Attempt（試行番号、フェーズ、終了コード、出力要約）の履歴を配列として追跡・保存する。
  2. 成功時は Evidence Record に `attempts` を記録し、失敗時は `logs/nightly/task_<id>_failure.log` に各 Attempt のログを漏れなく出力する。
- [x] 機能V (成果提出パッケージ Evidence Bundle の生成と独立検証):
  1. `auto-dev bundle` は、Gitコミット履歴、Evidence Record、Checker査読ログ、JobState、Morning Brief を集約し、機密（`.env*`, `*.pem`, `*.key` 等）および OS固有不要物（`__MACOSX`, `.DS_Store`）を排除した `bundle_<job_id>.zip` を出力する。
  2. パッケージ内にはファイルハッシュ一覧、Tree SHA、タスク監査情報を含む `bundle_manifest.json` を含める。
  3. `auto-dev verify-bundle <path>` は、マニフェストと各ファイルの実ハッシュ、Evidence Record の Tree SHA とコミットの Tree SHA の整合性を検証し、改ざんや欠落がないかを Exit 0/1 で判定する。
  4. オーケストレーターはジョブ完了時に自動で Evidence Bundle を `.dev/bundles/` 配下に生成し、`NIGHTLY_REPORT.md` に検証コマンドを提示する。
  5. Antigravity プラグインスキルとして `/nightly-bundle` および `/nightly-verify` を提供し、チャットUI上で完結する。
- [x] 機能W (マルチチーム自律開発の基盤構造 Multi-Team Foundation - Stage 1):
  1. `TeamManifest`（`src/auto_developer/manifest.py`）: プロジェクト設定、チーム定義（`id`, `owns`, `depends_on`, `max_parallel_tasks`）、契約参照（`contracts`）、統合設定（`integration`）を保持し、JSONおよび構造化定義から検証・ロードできるデータモデル。
  2. `WorktreeManager`（`src/auto_developer/worktree.py`）: 各チーム専用の独立作業ディレクトリ（`.dev/worktrees/<team_id>/`）およびブランチ（`feature/<team_id>`）をアトミックに作成、一覧、衝突検知、安全なクリーンアップを行う。Makerには作業ディレクトリのみを渡し、worktree管理権限は分離する。
  3. `BoundaryGuard`（`src/auto_developer/boundary.py`）: チームの `owns`（変更許可パターン）を逸脱したファイル作成・変更・削除を機械的に検知し、領域外編集を検知した場合は外部不変条件違反として即座にQuarantine（隔離ロールバック）を発動する。
  4. 決定論的受入テスト（`tests/test_multi_team_foundation.py`）: 独立した2つのWorktreeが競合なく共存できること、およびBoundaryGuardによる領空侵犯の即時遮断を証明する。
- [x] 機能X (Stage 1 堅牢化とパイプライン統合 Stage 1 Hardening & Pipeline Integration):
  1. `WorktreeManager` のパス・削除安全性（P0-1, P0-2）:
     - `team_id` を正規表現 `^[a-z][a-z0-9_-]{0,63}$` に制限し、不適合なIDを拒否する。
     - 作成・削除時に、解決されたターゲットパスが `self.worktree_base` の直下であることを検証し、パストラバーサルを遮断する。
     - `shutil.rmtree` による直接削除を廃止し、Gitが削除を拒否した場合は例外を送出して安全停止する。未コミット成果の無断破棄を遮断する。
  2. `TeamManifest` のスキーマ厳格化（P1-2）:
     - `team_id` の正規表現検証、空の `owns` リスト拒否。
     - `max_parallel_tasks` の正の整数制約（`> 0`）。
     - 真偽値フィールドにおける型安全性（`isinstance(val, bool)` を検証し、文字列 `"false"` による誤判定を遮断）。
  3. `BoundaryGuard` の Fail-Closed 化・glob 厳格化・rename 検査（P0-3, P1-1）:
     - `git diff` または `git status` が失敗した場合、結果を握りつぶさず例外を送出し検査失敗（Fail-Closed）とする。
     - glob照合をリポジトリルートからの相対パス先頭一致に統一し、意図しない曖昧一致を排除する。
     - Git rename (`R  old -> new`) において `old` と `new` の両方を検査対象とし、許可領域外へのファイル移動・持ち出しを遮断する。
  4. 境界違反時の自動 Quarantine とパイプライン統合（P0-3, P1-3）:
     - チーム実行時、Maker 実行直後およびコミット確定直前に `BoundaryGuard` を機械的に実行する。
     - 領空侵犯検知時にチーム固有の隔離ブランチ（`quarantine/<team_id>-...`）へ自動退避させ、成果をアーカイブ保全した上で本線をクリーン復元する。
  5. 決定論的受入テスト（`tests/test_multi_team_foundation.py`）:
     - パストラバーサル遮断、削除時の成果保全、Fail-Closed動作、領空侵犯時の自動Quarantineと完全復元を実証する。
- [ ] 機能Y (Stage 1 最終確定と厳格監査 Stage 1 Finalization & Strict Audit):
  1. `WorktreeManager` の非破壊的一括クリーンアップとブランチ成果保全:
     - `cleanup_all(force: bool = False, delete_branches: bool = False)` をデフォルト安全化。
     - `delete_branch=True` 指定時でも、ベースブランチに未マージのコミットが存在する場合はブランチ強制削除（`git branch -D`）を拒否し、未マージ成果を安全に保全する。
     - `remove_worktree` / `cleanup_all` のブランチ削除成否・保全状況を構造的に返却またはログ出力する。
  2. Evidence Bundle の 3値監査判定（`VERIFIED` / `INTEGRITY_ONLY` / `INVALID`）:
     - `verify-bundle` の検証結果として、`VERIFIED`（Exit 0 / 統合許可）、`INTEGRITY_ONLY`（Exit 2 / 承認証跡欠落、統合拒否）、`INVALID`（Exit 1 / 改ざん・破損、不許可）を厳格に判定・出力する。
     - 承認済みの Evidence Record が 0 件またはタスク不一致の場合は `INTEGRITY_ONLY` とし、未承認成果の統合を物理遮断する。
  3. Evidence Record の完全同梱:
     - `EvidenceBundler` は `.git/nightly_evidence/archive/` および `.dev/evidence/` からタスクの物理 Evidence Record を漏れなく集約し、バンドル内に収録する。
  4. 決定論的受入テストの追加:
     - 未マージコミットを持つ Worktree のブランチ保全テスト。
     - 証跡欠落時の `INTEGRITY_ONLY` 拒否判定テスト、および証跡完備時の `VERIFIED` 合格テスト。

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
