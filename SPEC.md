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
- [x] 機能Y (Stage 1 最終確定と厳格監査 Stage 1 Finalization & Strict Audit):
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
- [x] 機能Z (Stage 1 完全堅牢化と物理 Git Tree 拘束 Stage 1 Complete Hardening & Git Tree Binding):
  1. Evidence と物理 Git Tree SHA の厳格拘束（P0-Fail-Open遮断）:
     - `verify_bundle` は Evidence Record の自己申告を信じず、同梱の `git_bundle.bundle` を検証・参照解決してコミットと Tree SHA を物理照合する。
     - コミットが存在しない、または Tree SHA が一致しない、または架空ダミーハッシュ（例: `0000...`）の場合は即座に `INVALID`（Exit 1）として遮断する。
     - Evidence の `spec_sha` がバンドル内 `SPEC.md` の SHA-256 と一致しない（または空）の場合も即座に `INVALID` とする。
     - `status == COMPLETED` かつ `checker.decision == APPROVE` かつ Git Tree SHA と SPEC SHA が物理実証された場合のみ `VERIFIED`（Exit 0）を許可する。
  2. Worktree 強制削除時の成果保全と完全破棄ガード（P0-成果保護）:
     - 未コミット変更がある Worktree の削除時、直接の破壊的消去を禁止し、必ず Quarantine スナップショットアーカイブ（`.dev/quarantines/snapshot_worktree_...`）へ自動退避した上で解除する。
     - 未統合ブランチは `delete_branch=True` でも保全し、明示フラグ `purge_unmerged=True` が指定された場合のみ削除を許可する。
  3. 決定論的受入テストの追加:
     - 架空 Evidence 注入ケース（Sol氏再現シナリオ）に対する `INVALID` 遮断テスト。
     - 未コミット変更 Worktree 削除時の自動 Quarantine 保全テスト。
     - 未統合ブランチの `purge_unmerged` ガードテスト。
- [x] 機能Ω (Stage 1 最終完成・厳格 Fail-Closed ハードニング Stage 1 Final Completion & Strict Fail-Closed Hardening):
  1. Git Bundle 検証失敗時の完全 Fail-Closed（P0）:
     - `git_bundle.bundle` のクローンまたは検証が失敗した場合、またはコミット履歴が空の場合は即座に `INVALID`（Exit 1）として遮断する。
     - `valid_trees` が空の場合は検証スルーを許さず、必ず拒否する（Fail-Open 脆弱性の完全撤廃）。
  2. Manifest HEAD / Tree と実 Git オブジェクトの照合（P0）:
     - Manifest に記載された `head_commit` および `head_tree_sha` が、Git Bundle 内の実際の `HEAD`（`git rev-parse HEAD`, `git rev-parse HEAD^{tree}`）と完全一致することを検証する。
  3. Task / Commit / Tree の 1対1 物理対応と完全性（P1）:
     - Evidence Record の `commit` SHA が実在し、`git rev-parse <commit>^{tree}` が Evidence の `tree_sha` と 1対1 で完全一致することを検証する。
     - Manifest の `tasks` リストと `evidence/` ディレクトリ内の全 `task_*.json` のタスク集合が双方向で完全一致することを検証する。
  4. Worktree Quarantine の機密除外と Git status の Fail-Closed（P1）:
     - 未コミット退避アーカイブ生成時、`.env` や秘密鍵等の機密ファイルを除外（サニタイズ）する。
     - `git status` が失敗した場合は例外を送出して削除を拒否し、安全停止する。
  5. 決定論的受入テストの追加:
     - Sol 氏再現の 4 つの異常系ストレステスト（Git Bundle 偽造、Commit 不正、Evidence ファイル欠落、HEAD 偽造）がすべて確実に `INVALID` で弾かれることを検証するテスト。
     - 機密ファイル除外と `git status` 異常終了時の安全停止テスト。
- [x] 機能Ψ (Stage 1 完全防衛・機密スキャン & 厳格マニフェスト照合 Stage 1 Ultimate Defense: Deep Secret Scanning & Strict Manifest Verification):
  1. Worktree 隔離における秘密情報内容スキャン & SHA-256 Manifest 読み戻し検証（P0）:
     - ファイル名だけでなくファイル内容（APIキー、シークレットトークン等）をディープスキャンし、秘密情報が検出された場合は意図しない機密漏洩・破壊を防ぐため Worktree 削除を安全に停止（Fail-Closed）する。
     - 安全な変更の場合は各ファイルの SHA-256 ハッシュ付き Manifest を生成し、アーカイブ作成後に読み戻し整合性検証を行い、保全が物理的に確認できた場合のみ Worktree 削除を許可する。
  2. Bundle Manifest の必須項目厳格化・HEAD 欠落拒否・ZIP 未知ファイル排除（P1）:
     - Manifest 内の `head_commit`, `head_tree_sha`, `job_id` 等の必須項目が空文字・欠落・無効形式の場合は即座に `INVALID` で遮断する。
     - ZIP 内の全ファイルエントリ集合が Manifest 記載ファイル集合 + `manifest.json` と完全一致することを検証し、未知の追加ファイル（`mystery.txt` 等）やパストラバーサルを即座に拒否する。
  3. Manifest タスク情報と Evidence Record 内容の完全照合（P1）:
     - Manifest の `tasks` リストにおけるタスクID重複を排除する。
     - 各タスクの `task_id`, `commit`, `tree_sha`, `spec_sha`, `status`, `checker` を同梱された Evidence Record と 1対1 で完全一致照合し、フィールド改変や不整合を即座に `INVALID` として拒否する。
  4. 決定論的受入テストの拡充:
     - Sol 氏再現シナリオ（HEAD空文字、Manifestタスク改変、ZIP内未知ファイル追加、config.json内APIキー混入時のWorktree削除停止）の決定論的テストを追加。
- [x] 機能Φ (Stage 1 完璧クローズ・未保存成果消失阻止 & 全必須フィールド厳格拘束 Stage 1 Perfection: Zero Unsaved Loss & Complete Task Binding):
  1. Worktree 削除時の未保存秘密情報（.env 等）存在時における削除拒否（P0）:
     - Worktree 内に Quarantine 対象外となる秘密情報ファイル（`.env`, 秘密鍵等）が未コミットのまま存在する場合、Worktree の削除を即座に拒否し、設定・成果の物理的消失を防止（Fail-Closed 保全）する。
  2. Bundle Manifest タスク全必須フィールドの欠落不可・完全一致照合（P1）:
     - 各タスクについて、`task_id`, `commit`, `tree_sha`, `spec_sha`, `status`, `checker_decision` を全必須（空文字・欠落不可）とし、Manifest と Evidence Record の間で完全一致照合する。
  3. 決定論的受入テストの拡充:
     - `.env` 存在時の Worktree 削除拒否・保全テスト。
     - Manifest タスクの `commit` 空文字、`spec_sha` 不一致に対する `INVALID` 遮断テスト。
- [x] 機能Χ (Stage 1 完全無欠・Git status 独立の全ファイル秘密情報スキャン Stage 1 Invincibility: Git-Status-Independent Secret Scanning):
  1. Git status 独立の実ファイル事前スキャン（P0）:
     - Worktree 削除時、`git status --porcelain` の判定前に Worktree 内の実ファイルを走査する。
     - `.gitignore` に含まれている等の理由で Git status が空（変更なし）であっても、`.env` や秘密鍵、または秘密情報を含むファイルが存在する場合は即座に `WorktreeError` を送出して削除を拒否し、設定・成果の物理的消失を絶対に防止する。
  2. 決定論的受入テスト:
     - `.gitignore` で無視された `.env` のみが存在する Worktree において、`remove_worktree(force=True)` が確実に削除を拒否し、`.env` が物理保全されることの実証テスト。
- [x] 機能Ψ (Stage 1 完全無欠・Git無視通常ファイルの包括的退避保全 & レポート鮮度保証 Stage 1 Absolute Zero-Loss: Ignored File Quarantine & Report Freshness):
  1. Git無視通常ファイル（`local-notes.txt` 等）の Quarantine 包括退避保全（P0）:
     - Worktree 削除時、ノイズパターン（`.git/`, `__pycache__`, `.DS_Store` 等）以外の通常ファイル（`.gitignore` で無視されたファイルを含む）が存在する場合を検出する。
     - `git status --porcelain` が空であっても、これらのファイルが存在する場合は未コミット成果（`has_uncommitted = True`）として扱い、`force=False` では削除拒否、`force=True` では Quarantine アーカイブへ確実に退避・保全してから削除する。
  2. Morning Brief (NIGHTLY_REPORT.md) の最新鮮度保証（P1）:
     - Bundle 生成時に、現行ジョブの最新コミットおよびタスク実績が反映されたレポートを確実に同梱・整合させる。
  3. 決定論的受入テスト:
     - `.gitignore` で無視された `local-notes.txt` が `remove_worktree(force=True)` で Quarantine スナップショットに退避され、中身がハッシュ一致で復元・保全可能であることの実証テスト。
- [x] 機能Ω (Stage 1 完全無欠・管理済み設定ファイル誤検知防止 & クリーンWorktree正常削除 Stage 1 Perfect Close: Tracked Config Exemption):
  1. Git管理済み・未変更ファイル（`.env.example` 等）の事前スキャン免除（P1）:
     - Worktree 削除時、事前スキャンおよび機密性検査の対象を「未コミット・未追跡・無視ファイル等の未保存成果」に限定する。
     - リポジトリに管理済みで未変更（`tracked` かつ clean）のファイル（`.env.example` 等）は、Worktree 削除によって失われる成果ではないため、機密検査で誤検出せず通常削除を許可する。
  2. 決定論的受入テスト:
     - `.env.example` がコミットされている状態で、クリーンな Worktree を作成し、編集なしで `remove_worktree()` が正常に削除を完了することの実証テスト。
- [x] 機能AA (Stage 2: 独立自律チーム統合ゲート堅牢化 IntegrationGate Hardening & Strict Binding):
  1. Bundle とマージ対象 Commit SHA の厳格束縛 (P0-1):
     - `audit_team_inputs`: 各チームブランチ先端（`git rev-parse <branch>`）と Bundle の `verification.head_commit` が完全一致することを必須検証。不一致時は即座に監査拒絶（Fail-Closed）。
     - `merge_team_branches`: ブランチ名ではなく、監査済み Commit SHA を直接指定してマージ。マージ後にマージコミットの第2親（`git rev-parse HEAD^2`）が監査済み Commit SHA と一致することを機械的検証。
  2. 統合ブランチの保護と開始前クリーン検査 (P0-2):
     - `execute_integration`: 実行開始時に `git status --porcelain` を検査し、未コミット変更がある場合は即座に停止。
     - `create_integration_branch`: 既存の同名統合ブランチが存在する場合は上書きリセット（`-B`）を禁止し、Fail-Closed で拒絶。
  3. エラーハンドリングと隔離処理の堅牢化 (P1-1):
     - 保護ファイル検査で `git diff` 自体が異常終了した場合は Fail-Closed 遮断。全 Git 操作の終了コードを検証。
     - 統合失敗時の隔離ブランチ名にタイムスタンプ（`quarantine/integration-<job_id>-<timestamp>`）を付与して衝突を防ぎ、元のブランチへ確実に復帰。
  4. Release Candidate (RC) タグの冪等性と不変性 (P1-2):
     - `git tag -f` による強制上書きを廃止。既存タグが同一コミットを指している場合は冪等成功、異なるコミットを指している場合は上書き拒絶。
  5. 実機能結合の決定論的受入テスト (P1-3):
     - Core チームの計算関数を UI チームがインポートして呼び出す実機能結合テストを追加し、マージ後の全体検証通過を実証。
     - API 不整合（引数不一致など意味的競合）による統合後全体検証失敗・安全隔離シナリオを実証。
- [x] 機能BB (Stage 2: 専用 Integration Worktree 分離とクラッシュ復旧の完全性 Integration Worktree & Crash Recovery):
  1. 専用 Integration Worktree による親作業領域の完全非汚染化 (BB.1 / P1-1):
     - `execute_integration`: 呼び出し元作業ディレクトリ（親）上で直接 `git checkout` やマージを行わず、`WorktreeManager` を利用して専用の一時 Worktree（`.dev/worktrees/integration-<job_id>`）を新規ブランチ（`integration/<job_id>`）で作成し、その中で順次マージおよび全体検証を実行する。
     - 呼び出し元（親）の作業ディレクトリおよびカレントブランチ・作業ツリーは、統合処理の開始から終了（成功・失敗問わず）まで一切変更・切り替えされない。
     - 統合成功時は RC タグを発行後、Worktree を安全に削除・クリーンアップする。
  2. ライフサイクル管理と例外時の安全隔離・復旧 (BB.2 / P1-2):
     - Worktree の操作は `try ... finally` 等で保護され、Git 操作例外や RC タグ不変性違反（`RC_TAG_IMMUTABLE_BREACH`）等のあらゆる例外発生時にも親の作業ツリーは完全に保護され、Worktree の残留やリークを防止する。
     - 予期せぬ例外時も外へ生例外を伝播させず、`IntegrationResult(success=False, status="ERROR", errors=[...])` を安全に返却する。
  3. マージ競合時のタイムスタンプ付き隔離ブランチ明示と復旧不能状態管理 (BB.3 / P1-4):
     - Git マージ競合が発生した場合、`merge --abort` を実行した上で、途中マージブランチを `quarantine/integration-<job_id>-conflict-<timestamp>` へリネームし、通常の統合ブランチと誤認されないよう隔離状態を明示する。
     - `merge --abort` や作業ツリーの初期化等で復旧不能なエラーが発生した場合は、推測で処理を継続せず明示的に `status="RECOVERY_REQUIRED"` を返す。
  4. 受入テストの偽陽性排除と厳格な機能結合検証 (BB.4 / P1-3):
     - 契約受入テスト（`test_contract.py`）内の `except ImportError: return` を排除し、必要なモジュール（`core.app`, `ui.view`）のインポート失敗や期待値不一致は必ずテスト失敗（Exit 1）として検知する。
     - 統合前に契約テストが誤って実行されないようテスト実行条件・スコープを整理し、結合成功時のみ Exit 0、不整合時は即座に Exit 1 となる決定論的検証を保証する。
- [x] 機能CC (Stage 2: 統合クラッシュ耐性パック Integration Crash Consistency Pack):
  1. クリーンアップ状態の明示と隔離リネームの Fail-Closed 検査 (CC.1 / P1-A, P1-B):
     - Worktree の後片付け成否を `IntegrationResult.details` の `cleanup_status` ("SUCCESS" | "FAILED")、`worktree_path`、`cleanup_error` に記録し、クリーンアップ失敗時は警告またはエラー情報として呼び出し元へ明示する。
     - 競合（CONFLICT）や全体検証失敗（VERIFICATION_FAILED）時の隔離ブランチリネーム（`git branch -m`）の実行結果を検証し、リネームに失敗した場合は推測で処理を完了せず即座に `status="RECOVERY_REQUIRED"` を返す。
  2. 統合状態の原子的永続化 (CC.2 / P1-C):
     - 統合ジョブの進捗状態を管理する `IntegrationJobState` を定義し、`INITIALIZED` -> `AUDITED` -> `WORKTREE_CREATED` -> `MERGING` -> `VERIFYING` -> `RC_ISSUED` (完了時は `COMPLETED`、失敗時は `FAILED` または `RECOVERY_REQUIRED`) の状態遷移を記録する。
     - 状態ファイルは `.dev/integration_jobs/<job_id>.json` へ一時ファイル + `os.replace` によるアトミック書き込みを行い、プロセスクラッシュ時の破損を防止する。
  3. Git 実態照合による自律クラッシュ復旧 (CC.3 / P1-C, P1-D):
     - 統合処理が SIGKILL や電源断等で突然死した後の再開・復旧処理（`recover_or_resume`）を実装する。
     - 保存された状態ファイルを鵜呑みにせず、実際の Git 状態（Worktree の実在、統合ブランチの HEAD、マージ状態、RC タグ）と照合して安全な復旧経路を選択する。
     - 途中で孤立した Worktree やロックが存在する場合は安全に回収・隔離し、作業をクリーンな状態へ復元または安全に再開する。
  4. クラッシュ復旧の決定論的受入テスト (CC.4 / P1-D):
     - マージ途中や全体検証途中でプロセスが強制終了された状態を再現し、次回実行時に安全に検知・復旧（クリーンアップまたは再開）できることを実証する決定論的受入テストを追加。
- [x] 機能DD (Stage 2: クラッシュ復旧堅牢化 & 実プロセスSIGKILL障害注入実証 Stage 2 Crash Consistency Hardening & Real SIGKILL Verification):
  1. `recover_interrupted_job` の完全 Fail-Closed 化 (DD.1 / P0):
     - 孤立 Worktree の削除失敗（例外発生）時、または削除試行後にターゲットディレクトリや Git 管理下の Worktree が実在する場合、推測で `RECOVERED` とせず確実に `status="RECOVERY_REQUIRED"` を返す。対象が最初から存在しない場合のみ冪等成功として扱う。
  2. `IntegrationJobState` の識別子検証 & 原子的永続化の堅牢化 (DD.2 / P1):
     - `job_id` を正規表現 `^[a-zA-Z0-9_-]+$` で厳格検証しパストラバーサルを遮断。状態保存時に一時ファイルへの `flush()` + `os.fsync()` および親ディレクトリの同期を行い、突然の電源断に対する耐性を担保。
  3. 実プロセスへの `SIGKILL` 障害注入受入テストの実証 (DD.3 / P1):
     - `IntegrationGate` に障害注入フック（`fault_hook`）を設け、別プロセスで起動した `execute_integration` をマージ途中（`after_first_merge`）または RC 発行直後で実際に `os.kill(pid, signal.SIGKILL)` により強制終了。その後の `execute_integration(..., resume=True)` による安全な復旧・隔離・統合完遂を実証。
  4. Morning Brief (NIGHTLY_REPORT.md) と Manifest の Tree SHA 完全整合 (DD.4 / P2):
     - `MorningReporter` が全 Evidence 領域（`records`, `archive`, `.dev/evidence`）から実態照合を行い、Manifest の Tree SHA と 1対1 で完全一致するよう出力する。
- [x] 機能EE (Stage 2 最終確定: RC タグ復旧の完全 Fail-Closed 化 & 成果コミット束縛 Stage 2 Finalization: Strict RC Recovery & Commit Binding):
  1. RC 発行済み復旧経路における Worktree 削除の Fail-Closed 化 (EE.1 / P0):
     - `recover_interrupted_job` において、`rc/<job_id>` タグ検出時でも Worktree 削除（`remove_worktree`）で例外が発生した、または Worktree ディレクトリや Git 管理下の Worktree が実在する場合、推測で `ALREADY_COMPLETED` とせず確実に `status="RECOVERY_REQUIRED"` を返す。
  2. RC タグコミットと統合成果の厳密照合 (EE.2 / P0):
     - `rc/<job_id>` タグが指すコミット（`existing_rc_commit`）が、JobState に記録された `rc_commit` や統合ブランチ先端と完全一致することを検証する。
     - また、`job_state.verified_commits` の全チームコミットが `existing_rc_commit` の祖先（`git merge-base --is-ancestor <team_commit> <existing_rc_commit>`）であることを機械的に検証し、架空・無関係なコミットを指す不正な RC タグによる誤完了を物理遮断する。不一致時は即座に `status="RECOVERY_REQUIRED"` を返す。
  3. 決定論的受入・回帰テストの実証 (EE.3 / P0):
     - テスト1: RC タグが存在する状態で Worktree 削除に失敗した際、`RECOVERY_REQUIRED` で遮断されることの実証。
     - テスト2: RC タグが存在するが、保存された統合成果と SHA 不一致または未統合コミットである際、`RECOVERY_REQUIRED` で遮断されることの実証。
- [x] 機能FF (Stage 2 完全完了: RC 復旧時の証拠欠落ガード Stage 2 Final Closure: Missing Evidence Guard in RC Recovery):
  1. 監査済みチーム成果未記録時の Fail-Closed 遮断 (FF.1 / P1):
     - `recover_interrupted_job` において、`rc/<job_id>` タグ検出時、`job_state.verified_commits` が空（未記録）の場合は即座に `status="RECOVERY_REQUIRED"` で遮断する。
  2. 照合根拠欠落時の Fail-Closed 遮断 (FF.2 / P1):
     - `job_state.rc_commit` が未記録、かつ統合ブランチ（`refs/heads/<integration_branch>`）が存在しない場合、RC タグの正当性を証明する根拠が欠落していると判断し、推測で `ALREADY_COMPLETED` とせず確実に `status="RECOVERY_REQUIRED"` で遮断する。
  3. 決定論的受入・回帰テストの実証 (FF.3 / P1):
     - JobState が INITIALIZED かつ verified_commits が空、統合ブランチなしの状態で同名 RC タグが存在する際、`RECOVERY_REQUIRED` で遮断されることを実証する回帰テストを追加。
- [x] 機能GG (Stage 3 MultiTeamOrchestrator 始動: 複数ワーカー並行実行・統合基盤 MultiTeam Orchestration Foundation):
  1. 複数チーム並行実行データ構造と契約 (GG.1):
     - `TeamWorkerSpec`, `MultiTeamConfig`, `MultiTeamResult` などのデータ構造を定義し、複数ワーカーの Worktree 分割と成果統合の不変条件を確立する。
  2. 独立ワーカーオーケストレーション基盤 (GG.2):
     - 各チームの独立 Worktree 上での実行・成果収集（Evidence Bundle 収集）と `IntegrationGate` への安全な引き渡しパイプラインの基本実装。
- [x] 機能HH (Stage 3 Worktree 管理堅牢化 & トランザクション準備 Stage 3 Worktree Robustness & Atomic Preparation):
  1. Worktree 削除成否判定の実態整合 (HH.1 / P1-1):
     - `cleanup_workers()` において、`remove_worktree` の戻り値（ブランチ削除フラグ）に依存せず、Worktree ディレクトリの消滅および Git 管理下の解放実態に基づいて削除成功（`True`）を判定する。
  2. 重複チームID拒否 & Worktree 原子的準備 (HH.2 / P1-2):
     - `MultiTeamConfig` において重複した `team_id` が渡された場合、即座に拒絶（`ValueError`）。
     - `prepare_workers()` において一部チームの Worktree 作成が失敗した場合、作成済みの全 Worktree を安全に自動ロールバック解放し、孤立 Worktree を残さないトランザクション的準備を保証する。
- [x] 機能II (Stage 3 実ワーカー並行実行 & 成果統合パイプライン MultiTeam Parallel Execution & Integrated Delivery):
  1. 複数ワーカーの実並行プロセス実行と監督 (II.1):
     - `MultiTeamOrchestrator.run_workers()` を実装し、各チームの独立 Worktree 上でワーカープロセス（`NightlyOrchestrator` / コマンド）を並行起動・監視する。
     - タイムアウトおよびエラー発生時のプロセス回収・安全停止を担保。
  2. 成果収集・ワーカー失敗遮断・統合ハンドオフ (II.2):
     - 各ワーカーが完了時に生成した Evidence Bundle を収集し、いずれかのワーカーが失敗した場合は未承認成果を統合せず `WORKER_FAILED` で安全停止。双方が成功した場合のみ `IntegrationGate` へ引き渡して統合検証・RC発行。
- [x] 機能JJ (Stage 3 決定論的受入・並行実行実証 Stage 3 Deterministic Multi-Worker Acceptance Verification):
  1. 2ワーカー並行稼働 & 成果統合受入テストの実証 (JJ.1):
     - 2つのワーカーが独立 Worktree 上で並行実行され、独立した成果物と Evidence Bundle を作成し、親オーケストレーターがこれらを安全に統合して Release Candidate を発行できることを実証。
     - 片方のワーカーが失敗した際に未承認成果の統合が遮断されることの実証。
- [x] 機能KK (Stage 3 標準CLIワーカー起動コマンド修正 & パラメータ拡張 Stage 3 Standard CLI Worker Command & Parameter Extension):
  1. CLI コマンド引数順序の修正 (KK.1 / P0):
     - `run_workers()` におけるデフォルト CLI コマンドを `["python3", "-m", "auto_developer.cli", "--work-dir", str(wt_path), "run"]` に修正し、サブコマンドの前に `--work-dir` を配置して引数解析エラー（Exit 2）を解消する。
  2. ワーカー引数・環境変数の明示的設定 (KK.2):
     - `TeamWorkerSpec` に `extra_args` および `env` フィールドを追加し、ワーカー起動時に `--mock` や固有の引数・環境変数を透過的に渡せるよう拡張する。
- [x] 機能LL (Stage 3 統合前 Evidence Bundle の Worktree 外保全 & ハッシュ照合 Stage 3 Permanent Evidence Bundle Preservation & Hash Verification):
  1. Worktree 削除前の Evidence Bundle 保全 (LL.1 / P1):
     - ワーカー完了後、IntegrationGate による統合または Worktree クリーンアップの前に、各チームの Evidence Bundle を永続保存先（`.dev/integration_jobs/<job_id>/evidence/<team_id>.zip`）へ退避コピーする。
  2. SHA-256 整合性検査と返却パス更新 (LL.2):
     - 退避前後の SHA-256 チェックサムを照合し、不整合時は即座にエラーとして遮断する。
     - `TeamExecutionResult.bundle_path` を保全後の恒久パスへ更新し、Worktree が削除された後でも呼び出し元が返却されたパスから有効な Evidence Bundle を監査・取得できることを保証する。
- [x] 機能MM (Stage 3 実サブプロセス 2チーム並行実行 & 意味的機能結合 E2E 受入実証 Stage 3 Real Subprocess Multi-Worker E2E & Semantic Integration Verification):
  1. 2つの実サブプロセスによる並行稼働受入実証 (MM.1 / P2):
     - `worker_fn` を使わず、実サブプロセス（`python3 -m auto_developer.cli --work-dir <wt> run --mock` 等）を2本同時に独立 Worktree 上で起動・監督し、並行開発を完遂することの実証。
  2. Core / UI 意味的機能結合検証 (MM.2):
     - Core チームのモジュールと UI チームのモジュールを別々のワーカーが実装・コミットし、統合検証スクリプトが両者の実インポート・結合呼び出しを検証して Exit 0 となり、Release Candidate が発行されることの実証。
     - クリーンアップ後も全 Evidence Bundle が実在し、独立 Verifier で `VERIFIED` となることの確認。
- [x] 機能NN (Stage 3 Evidence退避失敗時の原本保護 & 原子的保全 Stage 3 Original Worktree Protection on Preservation Failure & Atomic Storage):
  1. 退避未完了 Worktree の選択的原本保護 (NN.1 / P0):
     - `cleanup_workers()` に `team_results` 引数を追加し、各チームの実行結果を参照可能にする。
     - Evidence Bundle の Worktree 外退避が未完了（`BUNDLE_PRESERVATION_FAILED`, `BUNDLE_CORRUPTED`、または保全パスが存在しない）チームの Worktree は、`finally` 節においても削除せずディスク上に確実に保全（原本保護）する。
  2. 一時ファイル & `os.replace` による原子的保全 (NN.2):
     - `preserve_team_bundles()` において、保存先へ直接上書きコピーせず、同一ディレクトリ内のテンポラリファイル（`<dest_path>.tmp_<uuid>`）にコピー。
     - 一時ファイルの SHA-256 チェックサムを元 Bundle と照合し、完全一致した場合のみ `os.replace()` で原子的に確定置換する。失敗時は一時ファイルを安全に削除し、既存の有効な Evidence を汚染・破壊しない。
- [x] 機能OO (Stage 3 成果保全障害注入テスト & 受入実証 Stage 3 Fault Injection Verification & Full Acceptance):
  1. 退避失敗・原本保護・部分失敗の障害注入実証 (OO.1):
     - `shutil.copy2` / `os.replace` に `OSError` を注入するテストを追加し、退避失敗時でも Worktree とその中の Evidence Bundle が削除されずディスク上に物理残存することを実証。
     - Team A 成功 / Team B 失敗の部分退避失敗において、Team A はクリーンアップされ、失敗した Team B の Worktree のみ原本保護されることを実証。
  2. 全体検証ゲート通過と Evidence Bundle 生成・検証 (OO.2):
     - 全自動検証ゲート（175+ テスト、Ruff、Mypy）の合格確認。
     - 独立 Verifier による Evidence Bundle の完全性検証（errors: 0, warnings: 0）。
- [x] 機能PP (Stage 3 既存Evidenceの非破壊保全 & 衝突拒否 Stage 3 Non-destructive Evidence Preservation & Collision Guard):
  1. 既存 Evidence の非破壊照合 & 同一ハッシュ冪等再利用 (PP.1 / P1):
     - `preserve_team_bundles()` において、恒久保存先（`dest_path`）が既に存在する場合、既存ファイルの SHA-256 と退避対象 Bundle の SHA-256 を照合する。
     - 両者の SHA-256 が完全一致する場合、上書きコピーを実行せず既存ファイルを再利用（冪等成功）として扱い、`res.bundle_path = dest_path`, `res.evidence_preserved = True` を設定する。
  2. 異ハッシュ衝突時の上書き拒絶 & 原本保護 (PP.2 / P1):
     - 既存ファイルが存在し SHA-256 が異なる場合、既存の過去 Evidence を破壊・上書きしないよう即座にコピーを中止（Fail-Closed）し、`res.status = "BUNDLE_PRESERVATION_COLLISION"` を記録して統合を安全停止する。
     - 退避未完了として扱うことで、既存の保存先 Evidence を保護すると同時に、退避に失敗した新しい Worktree の削除も抑止して双方の成果を完全保全する。
  3. 冪等保存・衝突遮断の回帰テスト実証 (PP.3):
     - 同一 Bundle の冪等保存成功、および内容が異なる Bundle の衝突検知・上書き遮断・原本保護を検証する決定論的テストを追加。
- [x] 機能QQ (Stage 3 同時保存の排他的確定 & TOCTOU競合解消 Stage 3 Exclusive Atomic Publication & TOCTOU Race Condition Elimination):
  1. `os.link` による排他的・アトミック確定 (QQ.1 / P1):
     - `preserve_team_bundles()` において、ハッシュ照合済みの一時ファイル（`tmp_path`）から保存先（`dest_path`）への公開確定時に、上書き置換する `os.replace` ではなく、排他的リンク作成 `os.link(tmp_path, dest_path)` を使用する。
     - 作成成功時は `tmp_path.unlink()` を行い、`res.bundle_path = dest_path`, `res.evidence_preserved = True` とする。
     - `os.link` が `FileExistsError`（既存ファイル存在）を送出した場合、先行プロセスまたは並行プロセスが `dest_path` を確定させたと判断し、直ちに `dest_path` の SHA-256 を照合する。
       - 同一ハッシュなら冪等成功として `tmp_path.unlink()`, `res.bundle_path = dest_path`, `res.evidence_preserved = True`。
       - 異ハッシュなら `BUNDLE_PRESERVATION_COLLISION` として `tmp_path.unlink()`, `res.success = False`, `res.evidence_preserved = False`（既存ファイル保護 & 新規Worktree原本保護）。
     - 万が一ファイルシステムが `os.link` をサポートしない環境（`OSError` / `EPERM` / `ENOTSUP`）の場合に備え、安全なフォールバック機構を用意する。
  2. TOCTOU 競合注入テスト & 決定論的受入実証 (QQ.2 / P1):
     - 初期検査（`dest_path.exists()`）時点ではファイルが存在しなかったが、一時ファイル作成・確定直前に別プロセスによって同名ファイルが作成された状況を人工的に注入するテストを追加。
     - 先行作成ファイルが同一ハッシュの場合は安全に冪等成功し、異ハッシュの場合は既存ファイルを破壊・上書きせず `BUNDLE_PRESERVATION_COLLISION` で拒絶され原本保護されることを実証。
  3. 全体検証ゲート合格と Evidence Bundle 生成・検証 (QQ.3):
     - 全自動検証ゲート（179+ テスト、Ruff、Mypy）の合格確認。
     - 独立 Verifier による Evidence Bundle の完全性検証（errors: 0, warnings: 0）。
- [x] 機能RR (Stage 3 システムアーキテクチャ刷新 & ワーカープロセス監督・回収 Stage 3 Architecture Refresh & Worker Process Lifecycle Supervision):
  1. 多層防衛アーキテクチャ図 & 設計ドキュメント刷新 (RR.1):
     - `docs/designs/autonomous_pipeline_orchestrator.md` および `README.md` を更新。
     - Stage 1（Worktree物理分離・領空防御）、Stage 2（IntegrationGate・RC確定）、Stage 3（並列ワーカー・排他確定・非破壊保全）を統合した包括的 Mermaid システム構成図を反映。
     - プロセスグループ（PGID）管理およびクラッシュ整合性（Crash Consistency）の設計仕様を明文化。
  2. ワーカープロセスのグループ監督 & 異常停止時回収機構 (RR.2):
     - `MultiTeamOrchestrator.run_workers()` において、ワーカー起動時に独立したプロセスグループ（`start_new_session=True`）を設定し、各チームの PID/PGID を追跡・管理。
     - 親プロセスの終了、シグナル受信（SIGTERM/SIGINT等）、タイムアウト、または例外発生時に、プロセスグループ全体（`os.killpg`）へシグナルを送信して子プロセス・孤児プロセスを安全かつ確実に回収・終了する。
     - 異常停止時でも退避未完了 Worktree の原本保護原則を遵守しつつ、回収完了ステータスを記録。
  3. プロセス回収 & 孤児防止テストの実証、全体検証ゲート通過、および Evidence Bundle 生成・検証 (RR.3):
     - ワーカータイムアウトおよび異常停止時にプロセスグループ全体が確実に回収され、孤児プロセスが残存しないことを実証する決定論的テストを追加。
     - 全自動検証ゲート（180+ テスト、Ruff、Mypy）の合格確認。
     - 独立 Verifier による Evidence Bundle の完全性検証（errors: 0, warnings: 0）。
- [x] 機能SS (Stage 3 ワーカープロセス監督堅牢化 & 決定論的孤児防止実証 Stage 3 Robust Worker Process Lifecycle & Deterministic Orphan Prevention):
  1. 起動時 PGID 保持と厳格な終了ステータス判定 (SS.1 / P1):
     - `MultiTeamOrchestrator` において、ワーカー起動直後（`subprocess.Popen` 呼び出し直後）に `proc.pid` から PGID を確定取得・保持するデータ構造（`WorkerProcessHandle`）を導入。
     - `terminate_worker_process_group()` において、親ワーカープロセスが先行終了・クラッシュした場合でも、保持された PGID に対して `os.killpg(SIGTERM/SIGKILL)` を確実に発行し、残存した孫プロセス・子孫プロセスを完全回収する。
     - プロセスグループ全体の消滅（`os.killpg(pgid, 0)` が `ProcessLookupError` を送出）を検証し、停止指示・回収結果の成否を決定論的に判定・返却する。
  2. 起動同期 & ゾンビ除外による決定論的回収テスト (SS.2 / P1):
     - テスト環境において起動準備の時間差による Flakiness を解消するため、子プロセスの起動完了（PID ファイル書き込み等）を確実に待機・同期してから回収操作を実行する。
     - Linux 等の環境で終了済みゾンビプロセス（`State: Z`）を生存プロセスと誤認しないよう、ゾンビ状態を除外して真に実行中の孤児プロセスが存在しないことを厳密に検証する。
     - 親ワーカー先行終了時でも孫プロセスがプロセスグループごと安全に回収される決定論的テストを追加。
  3. ドキュメント実装状況マトリクスの追加、全体検証ゲート通過、および Evidence Bundle 生成・検証 (SS.3):
     - `README.md` および `docs/designs/autonomous_pipeline_orchestrator.md` に「実装状況マトリクス（実装・検証完了 / 開発中 / 将来マイルストーン）」を追加し、北極星アーキテクチャ図と現在地を明確化。
     - 全自動検証ゲート（181+ テスト、Ruff、Mypy）の合格確認。
     - 独立 Verifier による Evidence Bundle の完全性検証（errors: 0, warnings: 0）。
- [x] 機能TT (Stage 3 プロセスグループ実行中判定の厳格化とアクティブハンドル寿命管理 Stage 3 Process Group Active State Verification & Handle Lifecycle):
  1. プロセスグループ実行中判定の厳格化 (TT.1 / P1):
     - `terminate_worker_process_group()` において、単なる `killpg(pgid, 0)` の消滅待ちではなく、プロセスグループ内に「実行中プロセス（非ゾンビ）」が残存していないことを直接検査する判定ロジックを実装。
     - Linux 環境では `/proc` を走査して対象 PGID のプロセス状態（`State: Z/X` 除外）を確認し、macOS/BSD/その他環境では `ps -eo pid=,pgid=,stat=` を用いて検査。
     - グループ内に実行可能プロセスが存在しない場合（全プロセス消滅またはゾンビのみ）に回収成功（`True`）と判定し、停止指示失敗や状態確認不可時は `False` を返却する。
  2. 終了済みハンドルのアクティブ監督対象除外とPID/PGID再利用対策 (TT.2 / P1):
     - `terminate_all_workers()` において、過去に終了・回収済みのハンドルへシグナルを誤送信しないよう、現在アクティブに実行中のワーカー（`_active_workers`）のみを対象とする。
     - 終了確認済みのワーカーはアクティブ対象から除外し、OSによるPID/PGID再利用時の無関係なプロセスグループ誤爆を物理的に防止する。
  3. 2ワーカー並行停止テストの決定論的実証、全体検証ゲート通過、および Evidence Bundle 生成・検証 (TT.3):
     - 独立実行環境において `test_terminate_all_workers_terminates_running_subprocesses` を含む全 23 件の multi-team テストが 100% 決定論的に合格することを実証。
     - 全自動検証ゲート（181+ テスト、Ruff、Mypy）の合格確認。
     - 独立 Verifier による Evidence Bundle の完全性検証（errors: 0, warnings: 0）。
- [x] 機能UU (Stage 3 ワーカー停止失敗時のハンドル保持と再試行回収保証 Stage 3 Termination Failure Handle Retention & Recovery Retry):
  1. 停止失敗時のハンドル維持 (UU.1 / P1):
     - `terminate_worker_process_group()` において、プロセスグループ内に実行中プロセスが残存している場合（`still_alive == True`）は、`self._active_workers` および `self._all_worker_handles` からハンドルを破棄せず監督対象として保持する。
     - `_worker_history[team_id]` には `status: "TERMINATION_FAILED"` および失敗時刻を記録する。
  2. 再試行による回収能力の実証 (UU.2 / P1):
     - 一度停止に失敗したワーカーであっても、ハンドルが保持されているため、後続の `terminate_worker_process_group()` または `terminate_all_workers()` による再試行で回収可能であることを Red-First テストで実証する（「停止失敗 → ハンドル保持 → 再回収成功」）。
  3. 全体検証ゲート通過、および Evidence Bundle 生成・検証 (UU.3):
     - 全自動検証ゲート（181+ テスト、Ruff、Mypy）の合格確認。
     - 独立 Verifier による Evidence Bundle の完全性検証（errors: 0, warnings: 0）。
- [ ] 機能VV (Stage 3 親プロセス強制終了耐性 & ワーカークラッシュ自律復旧 Stage 3 Parent SIGKILL Resilience & Worker Crash Autonomic Recovery):
  1. 永続 Worker Registry の導入 (VV.1 / P1):
     - `MultiTeamOrchestrator` において、ワーカー起動時にチーム情報、Worktree パス、PID、PGID、起動タイムスタンプ、実行状態を `.dev/integration_jobs/<job_id>/workers.json` へ原子的に永続化する `WorkerRegistry` 機構を導入。
     - 親オーケストレーターが SIGKILL で強制終了されメモリが揮発しても、新プロセスがジョブ情報とワーカー状態をディスクから完全に復元可能とする。
  2. 3状態自律復旧エンジン `recover_multi_team_job()` の実装 (VV.2 / P1):
     - Case 1 (孤児ワーカー生存): Registry に記録された PGID とプロセスの実態（`has_active_processes_in_group`）を照合し、稼働中の孤児プロセスグループを安全に再回収・停止する。
     - Case 2 (ワーカー停止 & 未回収成果残存): Worktree 内の Evidence Bundle を検査・発見し、`preserve_team_bundles` により恒久保存先へ安全に救出して Worktree を解放する。
     - Case 3 (確定済み成果からの前進): 全チームの Evidence が検証済みの場合は再実行をスキップし、原子的 RC 発行および統合完了へ前進（Roll-forward）する。
  3. 親プロセス強制終了（SIGKILL）障害注入テストの実証、全体検証ゲート通過、および Evidence Bundle 生成・検証 (VV.3):
     - 親プロセスを疑似クラッシュ（強制終了）させた後、独立した新オーケストレーターインスタンスが上記 3 ケースを決定論的に自律復旧できることを実証する受入テストを追加。
     - 全自動検証ゲート（181+ テスト、Ruff、Mypy）の合格確認。
     - 独立 Verifier による Evidence Bundle の完全性検証（errors: 0, warnings: 0）。
- [ ] 機能WW (Stage 3 復旧時プロセス同一性照合・Worktree安全境界復元・Evidence実証厳格化 Stage 3 Identity Verification, Worktree Boundary Restoration & Strict Evidence Validation):
  1. プロセス同一性照合による無関係プロセス誤停止の防止 (WW.1 / P0):
     - `WorkerRegistration` にプロセスの OS 開始識別子（`process_start_time`）を記録するフィールドを追加。
     - ワーカー起動時、OS からプロセスの開始時刻（Linux: `/proc/[pid]/stat` の `starttime`、macOS/POSIX: `ps -o lstart=`）を取得して Registry に原子的に記録。
     - `recover_multi_team_job()` でのプロセスグループ停止時、対象 PID の現在の OS 開始時刻を取得・照合。不一致（PID再利用）または確認不能な場合はシグナル送信を絶対に実行せず、`UNVERIFIED_PROCESS` または `RECOVERY_REQUIRED` として安全に停止を保留する。
  2. Worktree 削除安全境界の復元と直接 rmtree 撤廃 (WW.2 / P0):
     - `recover_multi_team_job()` クリーンアップ処理から直接の `shutil.rmtree(wt_p)` を完全撤廃。
     - 削除操作は `WorktreeManager.remove_worktree`（Git 登録確認・`.dev/worktrees` 境界確認）に一本化。
     - Git の worktree 一覧に未登録、またはベースディレクトリ外のパスは一切削除を試みず、保全したまま `UNVERIFIED_WORKTREE` / 復旧保留とする。
  3. Evidence 実検証の義務化、状態厳格化、全体検証ゲート通過、および Evidence Bundle 生成・検証 (WW.3 / P1):
     - 復旧処理において、単なるファイル存在（`dest_bundle.exists()`）で成功扱いとせず、`EvidenceBundler.verify_bundle()` またはハッシュ照合による実検証を義務化。不正なデータや破損 zip は `INVALID_EVIDENCE` として拒絶。
     - Worktree からの救出時も SHA-256 ハッシュを計算・照合。
     - `verify_script=None` や IntegrationGate 未実行時は `RECOVERED_EVIDENCE_FOUND`（復旧保留）中間状態とし、IntegrationGate 成功時のみ `SUCCESS` とする。
     - 全自動検証ゲート（182+ テスト、Ruff、Mypy）の合格確認。
     - 独立 Verifier による Evidence Bundle の完全性検証（errors: 0, warnings: 0）。
- [x] 機能XX (Stage 3 PID-PGID所属照合 & EvidenceBundler独立実検証 Stage 3 Process Group Membership & Strict Evidence Audit):
  1. PID-PGID 所属照合による誤停止の完全遮断 (XX.1 / P0):
     - `recover_multi_team_job()` において、プロセスグループ停止を試みる前に、`w.pid` の OS 開始識別子照合（`curr_ident == w.process_start_time`）に加え、`os.getpgid(w.pid) == w.pgid` の所属確認を必須化。
     - 親ワーカー PID が既に終了している場合、あるいは `os.getpgid(w.pid) != w.pgid` の場合は、絶対に `os.killpg` を送らず、`UNVERIFIED_PROCESS` または `RECOVERY_REQUIRED` として安全に停止を保留（Fail-Closed）。
  2. EvidenceBundler による独立実検証の適用 & 復旧状態の厳格化 (XX.2 / P1):
     - 復旧処理における Evidence Bundle 判定に `EvidenceBundler.verify_bundle()` を適用。`bundle_manifest.json`、全ファイルハッシュ、Git オブジェクト、Checker 承認がすべて有効（`verification.is_valid`）である場合のみ `evidence_preserved=True` として認定。
     - マニフェストが存在しない zip や不正な証拠は `INVALID_EVIDENCE` として厳格に拒絶。
     - `verify_script=None` や IntegrationGate 未実行時は `RECOVERED_EVIDENCE_FOUND`（復旧保留中間状態）とし、RC 発行が完了するまでジョブ全体を `SUCCESS` としない。
  3. Stage 3 通しクラッシュ復旧受入実証、全体検証ゲート合格確認（184+ テスト）、および Evidence Bundle 生成・検証 (XX.3 / Stage 3 完了判定):
     - 親プロセス SIGKILL クラッシュからの通し自律復旧受入テストの実証。
     - 全自動検証ゲート（184+ テスト、Ruff、Mypy）の合格確認。
     - 独立 Verifier による Evidence Bundle の完全性検証（errors: 0, warnings: 0）。
- [ ] 機能YY (Stage 3 未確認ワーカー存在時の復旧抑止 & Worktree保全 Stage 3 Fail-Closed Unverified Worker Recovery & Worktree Preservation):
  1. 未確認ワーカー存在時の統合ゲート進行抑止 & RECOVERY_REQUIRED 状態遷移 (YY.1 / P1):
     - `recover_multi_team_job()` において、`result.unverified_processes` が空でない（同一性・所属関係の照合に失敗して停止を保留したワーカーが存在する）場合は、たとえ全チームの Evidence Bundle が揃っていても `all_preserved` を `False` と判定し、統合ゲート（IntegrationGate）への進行を物理遮断する。
     - パイプラインステータスを `RECOVERY_REQUIRED` として安全に停止・保留する。
  2. 未確認ワーカーの Worktree クリーンアップ抑止 & 保全 (YY.2 / P1):
     - `cleanup=True` 指定時であっても、`unverified_processes` に含まれるチームの Worktree は削除せず、`result.unverified_worktrees` に記録して保全する。
  3. 決定論的回帰テスト & Stage 3 最終完了実証 (YY.3 / Stage 3 完了判定):
     - 「有効な Evidence が存在しても、未確認ワーカーが残っている場合は統合処理に進まず status が RECOVERY_REQUIRED となり、Worktree も削除されない」ことを実証する単体・受入テスト（Red-First）。
     - 全自動検証ゲート（198+ テスト、Ruff、Mypy）の完全合格確認。
     - 独立 Verifier による Evidence Bundle の完全性検証（errors: 0, warnings: 0）。

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
