# SPEC.md — Auto Developer 仕様と不変条件

<!--
この SPEC は v0.9.0 で書き直した「現在の挙動の契約」です。
v0.8.0 までの開発ログ形式の SPEC（機能A〜YYY）は docs/history/SPEC-v0.8.md に凍結しています。
旧機能IDと新IDの対応は docs/history/README.md を参照してください。

書き方の規則
- 要件は「領域」ごとにまとめ、ID は <領域>-<番号>（例: CORE-3, TEAM-2）とする。番号は欠番にしてよいが再利用しない。
- [x] は実装済みでテストがあるもの、[ ] は未実装（現在のフェーズの対象）を表す。
- 新しいフェーズの要件は「## 5. 次のリリースで追加する要件」に書き、リリース後に 4 章の該当領域へ移す。
  開発の経緯（なぜそうしたか）はここに書かず、LESSONS.md・CHANGELOG.md・git 履歴に残す。
- Checker への入力は、該当節を機械的に切り出した原文とする（要約しない）:
    awk '/^## 5\./,/^## 6\./' SPEC.md > .dev/checker/spec_current.md
- この SPEC は Elephant（人間と設計セッション）が書き、人間が承認する。Maker は変更できない（保護ファイル）。
-->

## 1. 概要

- **名称**: Auto Developer
- **目的**: 人間が不在の間も、合意された仕様（SPEC.md）と客観的な検証条件（検証スクリプト）に従ってタスクを1つずつ進め、成功も失敗も検証可能な形で人間に引き渡す。
- **利用形態**:
  - CLI `auto-dev`（Python パッケージ。外部依存なし）
  - エージェント向けスキル（`skills/` 配下の `/nightly-setup`, `/nightly-run`, `/nightly-bundle`, `/nightly-verify`）。エージェント自身が Maker を務め、低レベルサブコマンドで検証・コミットする。
- **想定環境**: ローカルの Git リポジトリ、Python 3.10 以上、macOS または Linux。

## 2. 用語

- **Maker**: タスクを実装するエージェント。
- **Checker**: 実装時の会話を持たず、SPEC と差分だけを読んで APPROVE / REJECT を返す査読エージェント。Maker と異なる系統のモデルを使う。
- **Auditor**: リリース前などにリポジトリ全体を監査する独立 LLM。Maker と異なる系統のモデルを使う。
- **検証スクリプト**: `scripts/verify_local.sh`。リンター・型検査・テストを実行し、終了コード 0 のときだけ合格とする。
- **Evidence（証跡）**: 検証合格時に発行され、作業ツリーの Git Tree SHA と SPEC.md のハッシュに拘束された一回限りの記録。コミット時に照合・消費され、監査レコードとして残る。
- **Quarantine（隔離）**: 失敗した作業を捨てずに `quarantine/*` ブランチへ退避し、本線を直前の正常状態に戻すこと。
- **保護ファイル**: Maker が作成・変更・削除してはならないファイル。`.github/**`, `SPEC.md`, `scripts/verify_local.sh`, `tests/immunity/**`（v0.9.0 から `teams.json` を追加。TEAM-11）。
- **免疫テスト**: 過去の重大な失敗の再発を防ぐテスト（`tests/immunity/`）。人間だけが追加・変更する。
- **チーム**: 担当範囲（`owns`）と計画ファイルを持つ作業単位。チームごとに専用の Git worktree とブランチで作業する。

## 3. 不変条件

破れば即座に REJECT する。

- **INV-1 保護ファイル不変**: 保護ファイルの無断変更は、未追跡ファイルを含めて検証スクリプトの実行前に検知し、作業を隔離する。
- **INV-2 未検証コードを確定しない**: 有効な Evidence を照合・消費しないコミットは行わない。コミットの Tree SHA と SPEC ハッシュは Evidence の値と一致しなければならない。
- **INV-3 失敗した作業を消さない**: 退避が完了したことを確認するまで、`git reset --hard` や `git clean` などの破壊的操作を行わない。退避に失敗したら操作を止める。
- **INV-4 Strict Fail-Fast**: 同一タスクで検証または査読の失敗が2回続いたら、推測で後続タスクに進まず停止して記録する。
- **INV-5 機密を外に出さない**: `.env*`, `*.pem`, `*.key` など機密に当たるファイルは、隔離ブランチ・スナップショット・Evidence Bundle に含めない。
- **INV-6 異系統の査読**: Maker と Checker（および Auditor）が同一モデル・同一系統・系統不明のいずれかなら、承認として扱わない。
- **INV-7 監査ループは終わる**: 監査役が指摘を返し続けても、P2 以下は実行タスクにならず、同一モジュールへの P0/P1 自動登録は上限で止まり人間に渡される。
- **INV-8 計測はゲートに影響しない**: 計測（イベントログ・集計）の成否は、検証・査読・コミット・隔離の結果を変えない。
- **INV-9 担当範囲を越えない**: チームとして作業するとき、担当範囲（`owns`）外のファイルを変更した作業は確定させず隔離する。

## 4. 機能要件（実装済み）

### 4.1 CORE — 直列パイプライン

- [x] **CORE-1 タスクの取得と状態更新**: `PLAN.md` の「## ステップ」から未完了タスク（`- [ ]`）を上から1件ずつ取得する。完了したら `- [x]`、失敗したら `**[FAILED]**` を付ける。
- [x] **CORE-2 クリーンツリーの事前検査**: 起動時に `git status --porcelain` が空でなければ、終了コード 2 で起動を拒否する。
- [x] **CORE-3 1タスクの手順**: 各タスクを [Maker 実装 → 保護ファイル検査 → 検証スクリプト → Checker 査読 → 証跡つきコミット] の順で処理する。Maker 実行後に差分がなければ、タスク未達成として扱う。
- [x] **CORE-4 再試行**: 検証または査読に失敗したら、失敗内容を Maker に渡して1回だけ再試行する。2回目も失敗したら隔離して停止する（INV-4）。各試行の番号・段階・終了コード・出力要約を記録する。
- [x] **CORE-5 実行方式**: `auto-dev run` は Maker / Checker を外部コマンドのテンプレート（`--maker-cmd`, `--checker-cmd`）で起動する。どちらかが未設定なら、`--mock` を付けない限り終了コード 2 で起動を拒否する。`--dry-run` は Git と状態ファイルを変更しない。
- [x] **CORE-6 Checker の判定の読み取り**: JSON の `{"decision": "APPROVE" | "REJECT"}`、または行全体が `APPROVE` / `REJECT`（`DECISION:` 前置可）の判定行がちょうど1種類あるときだけ、その判定に従う。判定行がない、または両方がある場合は不承認とする。
- [x] **CORE-7 低レベルサブコマンド**: エージェントが自分で Maker を務めるために、`pre-flight`, `verify-step`, `record-review`, `commit-step`, `rollback-step` を提供する。
- [x] **CORE-8 教訓の注入**: Maker 起動時に `LESSONS.md` の「## 恒久教訓インデックス」から関連する教訓を探し、コンテキストファイルまたは標準入力で渡す。
- [x] **CORE-9 初期化**: `auto-dev init` は `PLAN.md`, `SPEC.md`, `scripts/verify_local.sh`, `tests/immunity/` の雛形と `.gitignore` の追記を行う。既存ファイルは上書きしない。雛形の検証スクリプトは、テストが未構成なら失敗する（`|| true` などで失敗を握りつぶさない）。

### 4.2 GATE — 保護と証跡

- [x] **GATE-1 保護ファイル検査**: チェックポイントからの差分と `git status --porcelain` の両方で保護ファイルを検査する（INV-1）。`verify-step` は違反を検知したら Evidence を発行しない。
- [x] **GATE-2 Evidence の発行**: `verify-step` は検証スクリプトが成功したときだけ、PLAN.md の完了反映を含む作業ツリーの Tree SHA、SPEC ハッシュ、検証の結果を拘束した Evidence を発行する。
- [x] **GATE-3 証跡つきコミット**: `commit-step` は Tree SHA と SPEC ハッシュを再計算し、Evidence と一致しなければ終了コード 3 で拒否する。成功したら Evidence を消費し、監査レコード（`.git/nightly_evidence/`、worktree では `.dev/evidence/`）として保存する。
- [x] **GATE-4 Checker 方針の検査**: `commit-step` は `--maker-model`, `--checker-model`, `--checker-decision` を必須とする。判定が APPROVE 以外の場合、または INV-6 に反する場合は、`CHECKER_POLICY_VIOLATION:` を出して終了コード 4 で拒否する。このとき Evidence は消費せず、HEAD と PLAN.md も変更しない。監査レコードには判定・理由・両モデル名・系統を残す。

### 4.3 RCV — 失敗時の保全と復旧

- [x] **RCV-1 隔離**: 失敗したら、未追跡ファイルと無視ファイル（機密を除く）を含む作業ツリーを `quarantine/task-<id>-*` ブランチに退避する。そのうえで本線をチェックポイントに完全に戻し（`git status --porcelain` が空）、`logs/nightly/task_<id>_failure.log` に各試行のログを残して終了コード 1 で停止する。
- [x] **RCV-2 状態の永続化**: ジョブの状態（`QUEUED` / `RUNNING` / `SUSPENDED` / `COMPLETED` / `FAILED`）と段階を `.dev/job_state.json` に原子的に書き込む。
- [x] **RCV-3 再開**: `run --resume` は、再開可能な状態で、HEAD がチェックポイントと一致するときだけ再開する。コミット済みのタスクを二重にコミットしない。
- [x] **RCV-4 クラッシュ復旧**: `auto-dev recover inspect | plan | apply` は、中断したジョブの状態を診断し、修復の計画を示し、冪等に適用する。オーケストレーターが動作中なら適用を拒否する。

### 4.4 RPT — レポートと計測

- [x] **RPT-1 Morning Brief**: `NIGHTLY_REPORT.md` には、成果の要約、確認コマンド（検証用と利用用を分けて示す）、初期チェックポイントと最終コミット、隔離ブランチと失敗理由、人間が判断すべきこと、監査の結果を記載する。失敗時の案内は、実行できる手順（隔離ブランチの確認・引き継ぎ・破棄）に限る。
- [x] **RPT-2 単独でのレポート生成**: `auto-dev report` は、ジョブ状態ファイルがなくても、Git・PLAN.md・監査レコード・監査結果からレポートを生成する。`brief` と `bundle` の実行時にもレポートを最新化する。
- [x] **RPT-3 イベントログ**: `verify_step`, `commit_step`, `rollback_step`, `checker_review`, `audit_stage` のイベントを `events.jsonl` に追記だけで記録する。書き込みに失敗しても `METRICS_WARNING:` を出すのみとする（INV-8）。
- [x] **RPT-4 フェーズ集計**: `auto-dev metrics [--phase N] [--json] [--append-to PATH]` は、フェーズごとの指標と直前のフェーズとの差を出力する。指標ごとに、イベントログ由来か Git 由来かを示す。算出できない指標は推定値で埋めずに null とする。指標の定義は METRICS.md に書く。

### 4.5 BDL — 提出パッケージ（Evidence Bundle）

- [x] **BDL-1 生成**: `auto-dev bundle` は、Git 履歴、ソースのスナップショット、監査レコード、ジョブ状態、ログ、`NIGHTLY_REPORT.md`, `PLAN.md`, `SPEC.md`, `BACKLOG.md`、監査結果を1つの ZIP にまとめる。機密と OS の不要ファイルは除外し、`bundle_manifest.json` に全ファイルの SHA-256 を記録する。ジョブが完了したときにも自動で生成する。
- [x] **BDL-2 検証**: `auto-dev verify-bundle <zip> [--json]` は、ファイルのハッシュ、Evidence とコミットの Tree SHA の一致、SPEC の不変性、Checker の承認を検証して、終了コード 0 / 1 で判定する。監査後に実装が変わっていれば `AUDIT_OUTDATED`、未解決の P0/P1 またはエスカレーションがあれば `RELEASE_BLOCKED` とする。

### 4.6 AUDIT — 独立監査と自己改善ループ

- [x] **AUDIT-1 独立監査**: `run --enable-audit --auditor-cmd ...` は、対象コミットの SPEC.md と差分だけを入力に、Maker と異なる系統の Auditor（INV-6）に監査させる。設定したモデル名と実際に起動したモデル名が一致しなければ拒否する。出力が途中で切れていたり形式が不正だったりする場合は、監査失敗として扱う（Fail-Closed）。
- [x] **AUDIT-2 重要度の定義**: P0 はデータ消失・機密漏洩・証跡偽装の見逃し・保護ファイル防壁の迂回、P1 は安全ゲートが Fail-Open になる経路や主経路の仕様違反、P2 は限定条件下の誤判定や精度不足、INFO は改善提案とする。迷ったら低い方を付ける。
- [x] **AUDIT-3 振り分け**: 指摘を次のように振り分ける。
  - P0/P1: 再現テストが本物の Red（GENUINE_RED、SBX-2）であるものだけを、PLAN.md に `AUD-<id>` タスクとして登録する。
  - P2: `BACKLOG.md` に1行で追記する。
  - INFO: 監査ストアにだけ記録する。
  - 偽の Red や改ざんを伴う指摘: REJECTED とする。
  - 再報告: DUPLICATE とし、REJECTED とは分けて数える。
  - 修正済みの指摘が再び出た場合（回帰）: 新しい ID で登録し直す。
- [x] **AUDIT-4 修正予算**: 同じモジュールへの P0/P1 自動登録が `max_consecutive_cycles`（既定 5）サイクル続いたら、以後の指摘は ESCALATED として人間の判断待ちに回す。この状態は、人間が台帳（`.dev/audit/budget.json`）のエントリを消すまで解除しない（INV-7）。
- [x] **AUDIT-5 解決の証明と学習**: 指摘を解決とするには、実在する修正前後の2つのコミットで、同じ再現テストが修正前は Red、修正後は Green になることを示す必要がある。再現テストとそのハッシュは変更できない。解決したら教訓を LESSONS.md に記録し、記録に失敗したら解決としない。

### 4.7 SBX — 候補テストの隔離実行

- [x] **SBX-1 隔離環境**: 監査役が出した再現テストは、一時クローンの中で、OS のサンドボックス（macOS は `sandbox-exec`、Linux は `bwrap`）の内側でだけ実行する。
  - 許可する読み取りは実行に必要な最小限とし、ホームディレクトリ・元のリポジトリ・認証情報は読ませない。
  - Linux ではネットワークを遮断する。
  - サンドボックスが使えない環境、または root で実行している場合は、実行を拒否する。
  - 実行の前後で元のリポジトリ（未追跡・無視ファイルを含む）のスナップショットを比較し、改ざんがあれば結果に関わらず拒否する。
- [x] **SBX-2 本物の Red の判定**: 判定は構造化されたテスト結果に基づく。実行件数が1件以上で、アサーション失敗が1件以上あり、それ以外のエラーが0件のときだけ Red とする。製品コードと因果関係のない失敗（固定値どうしの比較、無条件の `fail` など）は、AST 解析と実行の追跡で TRIVIAL_RED として退ける。静的に確信できない場合は TRIVIAL_RED と断定しない。

### 4.8 TEAM — マルチチーム実行（ライブラリ層）

- [x] **TEAM-1 チーム定義**: `TeamManifest`（`manifest.py`）は、プロジェクト・基点ブランチ・チーム（`id`, `owns`, `depends_on`）・統合の設定を JSON から読み込み、検証する。
- [x] **TEAM-2 worktree の分離**: `WorktreeManager` は、チームごとに `.dev/worktrees/<team_id>/` とブランチ `team/<team_id>` を作る。ID の検証とパスの逸脱の拒否を行い、準備が途中で失敗したら作成済みの worktree を巻き戻す。
- [x] **TEAM-3 担当範囲の検査**: `BoundaryGuard` は `owns` 外の作成・変更・削除を検知する。`NightlyOrchestrator` に `owns_patterns` が渡されていれば、違反した作業を隔離する（INV-9）。
- [x] **TEAM-4 並行実行と監督**: `MultiTeamOrchestrator` は各チームのワーカーを別プロセスグループで並行に起動し、タイムアウト・中断・親の終了のときにプロセスグループごと回収する。プロセスの同一性を確かめられないワーカーがいる場合は、統合と worktree の削除を行わない。
- [x] **TEAM-5 成果の保全**: 統合の前に、各チームの Evidence Bundle を `.dev/integration_jobs/<job_id>/evidence/<team_id>.zip` に排他的・原子的に保存する。同じ内容なら再利用し、異なる内容なら衝突として拒否する。
- [x] **TEAM-6 統合ゲート**: `IntegrationGate` は次の順で統合する。
  - 各チームの Bundle を検証する。
  - 専用の統合 worktree で各チームのブランチを順にマージする。コンフリクトしたら推測で解決せずに中断する。
  - 保護ファイルの不変を確認し、統合後の検証スクリプトを実行する。
  - 合格したら `refs/tags/rc/<job_id>` を原子的に作成する。基点ブランチ（main）は更新しない。
  - 中断したジョブは `recover_interrupted_job` で復旧する。

## 5. 次のリリース（v0.9.0）で追加する要件

<!-- v0.9.0 の目的: 4.8 TEAM を CLI とスキルから使えるようにし、初めての利用者がつまずかずに試せる状態にする。 -->

### 5.1 TEAM — マルチチーム実行の CLI 接続

- [ ] **TEAM-10 チーム定義ファイル**: リポジトリ直下の `teams.json` を `TeamManifest` で読み込む。各チームに `plan`（既定 `plans/<team_id>.md`）を追加する。次の場合は、理由を示す `TEAMS_CONFIG_ERROR:` を出して終了コード 2 で拒否する。
  - `depends_on` が空でない（v0.9.0 は独立したチームのみ対応）。
  - 2つのチームの `owns` が同じパスに一致しうる（同一のパターン、または一方が他方を含む）。
  - チームの `plan` が他のチームの `owns` に含まれる。
- [ ] **TEAM-11 チーム文脈の決定**: worktree の中で実行する `run`, `pre-flight`, `verify-step`, `commit-step`, `rollback-step` は、現在のブランチ `team/<team_id>` と、コミット済みの `teams.json` から、チーム ID・`owns`・計画ファイルを自動で決める。
  - 作業ツリー内の書き換え可能なファイルからは決めない。
  - チームの計画ファイルは、そのチームの `owns` に自動で含める。
  - `teams.json` を保護ファイルに加える。
  - `run` には明示的な指定 `--team-id` / `--owns` / `--plan` も設ける。ただし `teams.json` と矛盾する指定は拒否する。
- [ ] **TEAM-12 `auto-dev teams init`**: `teams.json` の雛形と、各チームの `plans/<team_id>.md` を作る（`--teams core,ui` で ID を指定）。既存のファイルは上書きしない。
- [ ] **TEAM-13 `auto-dev teams prepare [--job-id ID] [--json]`**: クリーンツリーと `teams.json` を検査し、全チームの worktree を作る。
  - ジョブ ID と各 worktree のパスを出力する（`--json` では機械可読の形で）。
  - 前回のジョブの worktree やブランチが残っていれば、作り直さずに拒否し、`teams status` / `teams cleanup` / `teams recover` を案内する。
- [ ] **TEAM-14 `auto-dev teams integrate --job-id ID`**: 全チームの計画ファイルのタスクが `- [x]` で、作業ツリーがクリーンなことを確かめる。そのうえで各チームの Bundle を（なければ生成して）保全し、統合ゲート（TEAM-6）を実行する。
  - 成功したら RC タグと、main へ取り込むためのコマンドを表示する。
  - 未完了・失敗・コンフリクト・統合後の検証失敗のいずれかなら、終了コード 1 で止め、原因のチームと次の手順を表示する。
  - 失敗した場合は worktree を残す。
- [ ] **TEAM-15 `auto-dev teams run`**: `prepare` → 全チームの並行実行 → `integrate` を一括で行う。
  - 各ワーカーは worktree の中で `auto-dev run` を実行する。`--maker-cmd`, `--checker-cmd`, モデル名、`--mock` をそのまま渡す。
  - ワーカーのタイムアウトは `--worker-timeout`（既定 21600 秒）とする。
  - 1チームでも失敗したら統合しない。
- [ ] **TEAM-16 `auto-dev teams status [--job-id ID] [--json]` / `teams cleanup --job-id ID` / `teams recover [--job-id ID]`**:
  - `status` は、チームごとに worktree・ブランチ・完了数/全タスク数・ワーカーの状態・隔離ブランチを表示する。
  - `cleanup` は、統合に成功したジョブの worktree とチームブランチだけを削除する。統合に失敗したジョブの削除は `--force` を付けたときだけ行う。
  - `recover` は `recover_multi_team_job` を呼ぶ。
- [ ] **TEAM-17 レポート**: チームのジョブを実行した後の `NIGHTLY_REPORT.md` と `auto-dev brief` に、チームごとの結果・RC タグ・判断待ちの事項を表示する。
- [ ] **TEAM-18 スキル**:
  - `/nightly-setup` はチームを使うかを尋ね、使うなら `teams init` を実行する。
  - `/nightly-run` は、`teams.json` があれば `teams prepare` を実行し、各 worktree で従来の直列ループ（CORE-7 の低レベルサブコマンド）を回してから `teams integrate` を実行する。
  - 外部 CLI で Maker / Checker を起動する場合は `teams run` を使ってよいと案内する。
- [ ] **TEAM-19 受入テスト**: 一時リポジトリに2チームを作り、mock の Maker で次を確かめる E2E テストを用意する。
  - (a) `teams run` で両チームの変更が統合され、RC タグができる。
  - (b) 担当範囲外を変更したチームは隔離され、統合されない。
  - (c) `depends_on` や重なった `owns` を指定すると終了コード 2 になる。
  - (d) `prepare` → worktree 内での低レベルサブコマンド → `integrate` の経路でも (a) と同じ結果になる。

### 5.2 REL — リリースと互換性

- [ ] **REL-1 Bundle の形式バージョン**: `bundle_manifest.json` に `format_version`（v0.9.0 は 2）と `tool_version` を記録する。`verify-bundle` は、`format_version` がないか 2 未満の Bundle について、「v0.8 以前の形式です。v0.8.0 の auto-dev で検証してください」という趣旨を示して終了コード 1 を返す（v0.8 以前の Bundle との互換は持たない）。
- [ ] **REL-2 バージョン表記**: `pyproject.toml`, `plugin.json`, `auto_developer.__version__`, CLI の表示を 0.9.0 に揃える。タグは `v0.9.0` の形式とする（v0.8.0 までの `v.0.x.0` 形式は使わない）。
- [ ] **REL-3 CI**: CI は macOS と Linux（Ubuntu）、Python 3.10 と 3.12 の組み合わせで検証スクリプトを実行する。Linux で `bwrap` を必要とするテストは、`bwrap` がなければ理由を示してスキップしてよい（判定を合格にすり替えない）。

### 5.3 DX — 初めての利用者の体験

- [ ] **DX-1 README のクイックスタートをテストする**: README に書いたクイックスタートの手順（新しい空のリポジトリでの `init` → `run --mock` → `brief`、および失敗から隔離までの例）を、そのまま一時リポジトリで実行するテストを用意する。README と実際の挙動のずれを検査で検出する。
- [ ] **DX-2 モデル名の既定値**: `run` の `--maker-model` / `--checker-model` / `--auditor-model` の既定値は、外部コマンドを指定したときには適用しない。外部コマンドを指定してモデル名を省略した場合は、指定を求めて終了コード 2 とする（特定ベンダーのモデル名を暗黙に記録しない）。
- [ ] **DX-3 サンプル**: `examples/two-teams/` に、2チーム構成の最小プロジェクト（`teams.json`, `plans/`, 検証スクリプト）を置く。DX-1 と TEAM-19 のテストから参照する。

## 6. スコープ外

- チーム間の依存関係（`depends_on`）に従った段階実行（v0.10 以降で検討）
- 統合結果の main への自動マージ（RC タグまでで止め、取り込みは人間が行う）
- クラウドでの分散実行、外部データベース、Web UI ダッシュボード、リリースの自動デプロイ
- SPEC.md の自己改変、タスクを推測で飛ばして続行すること
- 標準ライブラリ以外の実行時依存（開発用の `pytest`, `mypy`, `ruff` は除く）

## 7. 完了定義

- `bash scripts/verify_local.sh` が終了コード 0 で通る（保護ファイル検査・ruff・mypy・全テスト）。
- `tests/immunity/` の免疫テストがすべて通る。
- 当該フェーズの要件に対応する受入テストがあり、通っている。
- リリース時は、最新の HEAD に対する独立監査と `verify-bundle` が、`AUDIT_OUTDATED` / `RELEASE_BLOCKED` なしで通る。
