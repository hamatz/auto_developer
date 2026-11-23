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
- Checker への入力は、現在のフェーズの節を機械的に切り出した原文とする（要約しない）。例（Phase 70）:
    awk '/^### 5\.1/,/^### 5\.2/' SPEC.md > .dev/checker/spec_current.md
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
- **保護ファイル**: Maker が作成・変更・削除してはならないファイル。`.github/**`, `SPEC.md`, `scripts/verify_local.sh`, `tests/immunity/**`, `teams.json`。
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
- **INV-10 判定は申告ではなく出力から**: 査読の判定は、Checker の出力そのものから機械的に読み取り、記録の後に出力・差分が変わっていないことを確定の直前に確かめる。エージェントが申告した判定だけでは、確定させない（GATE-5〜7）。

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
- [x] **CORE-10 計画ファイルの読み取り範囲**: タスクの取得（CORE-1）は次の規則に従う。`teams status` などの進捗表示や、完了・失敗の書き込みも同じ規則で対象行を決める。
  - HTML コメント（`<!--` から `-->` まで。複数行にまたがるものを含む）の中の行は、タスクとして扱わない。
  - 「## ステップ」見出しがある場合は、その見出しから次の `## ` 見出しまでの中のタスクだけを読む。見出しがない場合は、従来どおりファイル全体から読む。
  - 状態を書き込むときは対象のタスク行だけを書き換え、コメントや他の節は変更しない。
- [x] **CORE-11 生成物の無視**: `auto-dev init` と `auto-dev teams init` は、`.gitignore` に次を追記する。既にある行は重複させない。
  - Auto Developer の生成物: `.dev/`, `logs/`, `NIGHTLY_REPORT.md`
  - Python の一般的な生成物: `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`

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
- [x] **RPT-5 判断待ちの表示**: チームのジョブがあるときの `brief` と `NIGHTLY_REPORT.md` で、「判断待ちの事項」節には、そのジョブで人間が判断すべきことだけを表示する。判断すべきことがなければ「なし」とし、単一チーム用の文言を出さない。
- [x] **RPT-6 レポートと Bundle の順序**: `auto-dev run` は、成功時・失敗時とも、Bundle の出力先を先に決め、そのパスを含むレポートを書いてから Bundle を作る。Bundle に入る `NIGHTLY_REPORT.md` は、その実行の結果を表す。
- [x] **RPT-7 チームのジョブのレポート**（欠陥あり。Phase 70 の RPT-8 で修正）: チームのジョブを実行した後の `NIGHTLY_REPORT.md` と `brief` に、チームごとに次を表示する。
  - 完了したタスク
  - 各タスクの Checker の判定とモデル名（保全済みの Bundle、または worktree の Evidence の記録から読む）
  - `NO_CHANGES` のチームはその旨
  - 「最新コミット (HEAD)」の代わりに RC コミットを示し、main（基点）が変更されていないことがわかる表示にする。単一チームのレポートは変えない。

### 4.5 BDL — 提出パッケージ（Evidence Bundle）

- [x] **BDL-1 生成**: `auto-dev bundle` は、Git 履歴、ソースのスナップショット、監査レコード、ジョブ状態、ログ、`NIGHTLY_REPORT.md`, `PLAN.md`, `SPEC.md`, `BACKLOG.md`、監査結果を1つの ZIP にまとめる。機密と OS の不要ファイルは除外し、`bundle_manifest.json` に全ファイルの SHA-256 を記録する。ジョブが完了したときにも自動で生成する。
- [x] **BDL-2 検証**: `auto-dev verify-bundle <zip> [--json]` は、ファイルのハッシュ、Evidence とコミットの Tree SHA の一致、SPEC の不変性、Checker の承認を検証して、終了コード 0 / 1 で判定する。監査後に実装が変わっていれば `AUDIT_OUTDATED`、未解決の P0/P1 またはエスカレーションがあれば `RELEASE_BLOCKED` とする。
- [x] **BDL-3 Bundle とレポートに入れる記録の選択**（欠陥あり。Phase 70 の BDL-5 で修正）: `auto-dev bundle` と `NIGHTLY_REPORT.md` の生成で、次の規則に従って記録を選ぶ。
  - 対象の計画ファイル（単一チームでは `PLAN.md`、チームの worktree ではそのチームの計画ファイル）で完了（`- [x]`）しているタスクの Evidence の記録だけを対象にする（CORE-10 の読み取り範囲に従う）。
  - 対象外とした記録は Bundle に入れず、manifest の `excluded_records` にタスク ID と理由を記録する。
  - 計画ファイルにタスクとして読める行が1つもない場合は、従来どおりすべての記録を対象にする。
  - `verify-bundle` の検査（BDL-2）は弱めない。

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
- [x] **SBX-3 サンドボックスの実働確認**: サンドボックスを使えるかどうかは、実行ファイルがあるかではなく、実際に起動できるかで判定する。
  - 判定は、最小のコマンドをサンドボックスの中で1回実行して行い、結果はプロセスの中でキャッシュする。
  - 起動できなければ `SANDBOX_UNAVAILABLE` とする（SBX-1 の Fail-Closed は変えない）。その出力には、OS ごとの対処を含める。
    - Linux で bwrap がない: bubblewrap のインストール。
    - Linux で bwrap が起動できない: Ubuntu 23.10 以降の AppArmor による非特権ユーザー名前空間の制限。`bwrap-userns-restrict` プロファイルの読み込み。
    - macOS で `sandbox-exec` が使えない: その旨。
  - 判定の結果を返す関数 `_get_sandbox_backend` の名前と戻り値（`"sandbox-exec"` / `"bwrap"` / `None`）は変えない。
- [x] **SBX-4 OS に依存しない期待値**: テストの期待値は、OS 固有の手段ではなく、守るべき性質で書く。
  - 改ざんを仕込むテストは、macOS 専用のコマンド（`chflags`）を、それが存在する場合にだけ呼ぶ。
  - 機密の読み取りを試すテストは、次の2つを確かめる。どの例外（`PermissionError` / `FileNotFoundError`）で拒否されたかは問わない。
    - 機密の値が出力に現れない。
    - Red として受理されない。
  - サンドボックスが使えないときにスキップする、またはモックに切り替えるテストは、環境変数 `AUTO_DEV_REQUIRE_SANDBOX=1` のときは、スキップや切り替えをせずに失敗にする。
- [x] **SBX-5 実行による確認の対称性**: macOS で実際にサンドボックスの中で実行して確かめている性質は、Linux でも bwrap の中で実際に実行して確かめる。少なくとも、次の4つが読めないことを、Linux の実行テストで確かめる。macOS 専用のテスト（`test_sandbox_blocks_reading_*`）は残す。
  - ホストの機密ファイル（`/etc/shadow` など）
  - 許可していないホストのパス（`/opt` など）
  - ホームディレクトリ（`~/.ssh` を含む）
  - 元のリポジトリ

### 4.8 TEAM — マルチチーム実行

#### ライブラリ層

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

#### CLI とスキル（v0.9.0 Phase 66 で接続）

- [x] **TEAM-10 チーム定義ファイル**: リポジトリ直下の `teams.json` を `TeamManifest` で読み込む。各チームに `plan`（既定 `plans/<team_id>.md`）を追加する。次の場合は、理由を示す `TEAMS_CONFIG_ERROR:` を出して終了コード 2 で拒否する。
  - `depends_on` が空でない（v0.9.0 は独立したチームのみ対応）。
  - 2つのチームの `owns` が同じパスに一致しうる（同一のパターン、または一方が他方を含む）。
  - チームの `plan` が他のチームの `owns` に含まれる。
- [x] **TEAM-11 チーム文脈の決定**: worktree の中で実行する `run`, `pre-flight`, `verify-step`, `commit-step`, `rollback-step` は、現在のブランチ `team/<team_id>` と、コミット済みの `teams.json` から、チーム ID・`owns`・計画ファイルを自動で決める。
  - 作業ツリー内の書き換え可能なファイルからは決めない。
  - チームの計画ファイルは、そのチームの `owns` に自動で含める。
  - `teams.json` を保護ファイルに加える。
  - `run` には明示的な指定 `--team-id` / `--owns` / `--plan` も設ける。ただし `teams.json` と矛盾する指定は拒否する。
- [x] **TEAM-12 `auto-dev teams init`**: `teams.json` の雛形と、各チームの `plans/<team_id>.md` を作る（`--teams core,ui` で ID を指定）。既存のファイルは上書きしない。
- [x] **TEAM-13 `auto-dev teams prepare [--job-id ID] [--json]`**: クリーンツリーと `teams.json` を検査し、全チームの worktree を作る。
  - ジョブ ID と各 worktree のパスを出力する（`--json` では機械可読の形で）。
  - 前回のジョブの worktree やブランチが残っていれば、作り直さずに拒否し、`teams status` / `teams cleanup` / `teams recover` を案内する。
- [x] **TEAM-14 `auto-dev teams integrate --job-id ID`**: 全チームの計画ファイルのタスクが `- [x]` で、作業ツリーがクリーンなことを確かめる。そのうえで各チームの Bundle を（なければ生成して）保全し、統合ゲート（TEAM-6）を実行する。
  - 成功したら RC タグと、main へ取り込むためのコマンドを表示する。
  - 未完了・失敗・コンフリクト・統合後の検証失敗のいずれかなら、終了コード 1 で止め、原因のチームと次の手順を表示する。
  - 失敗した場合は worktree を残す。
- [x] **TEAM-15 `auto-dev teams run`**: `prepare` → 全チームの並行実行 → `integrate` を一括で行う。
  - 各ワーカーは worktree の中で `auto-dev run` を実行する。`--maker-cmd`, `--checker-cmd`, モデル名、`--mock` をそのまま渡す。
  - ワーカーのタイムアウトは `--worker-timeout`（既定 21600 秒）とする。
  - 1チームでも失敗したら統合しない。
- [x] **TEAM-16 `auto-dev teams status [--job-id ID] [--json]` / `teams cleanup --job-id ID` / `teams recover [--job-id ID]`**:
  - `status` は、チームごとに worktree・ブランチ・完了数/全タスク数・ワーカーの状態・隔離ブランチを表示する。
  - `cleanup` は、統合に成功したジョブの worktree とチームブランチだけを削除する。統合に失敗したジョブの削除は `--force` を付けたときだけ行う。
  - `recover` は `recover_multi_team_job` を呼ぶ。
- [x] **TEAM-17 レポート**: チームのジョブを実行した後の `NIGHTLY_REPORT.md` と `auto-dev brief` に、チームごとの結果・RC タグ・判断待ちの事項を表示する。
- [x] **TEAM-18 スキル**:
  - `/nightly-setup` はチームを使うかを尋ね、使うなら `teams init` を実行する。
  - `/nightly-run` は、`teams.json` があれば `teams prepare` を実行し、各 worktree で従来の直列ループ（CORE-7 の低レベルサブコマンド）を回してから `teams integrate` を実行する。
  - 外部 CLI で Maker / Checker を起動する場合は `teams run` を使ってよいと案内する。
- [x] **TEAM-19 受入テスト**: 一時リポジトリに2チームを作り、mock の Maker で次を確かめる E2E テストを用意する。
  - (a) `teams run` で両チームの変更が統合され、RC タグができる。
  - (b) 担当範囲外を変更したチームは隔離され、統合されない。
  - (c) `depends_on` や重なった `owns` を指定すると終了コード 2 になる。
  - (d) `prepare` → worktree 内での低レベルサブコマンド → `integrate` の経路でも (a) と同じ結果になる。
- [x] **TEAM-20 検証後の担当範囲検査**: チームとして作業するとき、担当範囲の検査（TEAM-3）を、Maker の実行後に加えて、検証スクリプトの実行後・Evidence の発行前にも行う。違反があれば Evidence を発行しない。
  - `verify-step` は終了コード 1 で失敗する。違反したファイルの一覧と、「検証で生成されたファイルなら `.gitignore` に追加する」という案内を表示する。
  - `run` では検証の失敗として扱い、再試行（CORE-4）と隔離（RCV-1）の規則に従う。
- [x] **TEAM-21 コミット直前の違反の扱い**: コミット直前の担当範囲検査で違反を見つけた場合は、次のように扱う。
  - `run`: Evidence を破棄し、RCV-1 と同じく隔離・本線の完全な復元・失敗ログの保存・`**[FAILED]**` の記録を行って、終了コード 1 で停止する。ステージ済みの変更を作業ツリーに残さない。
  - `commit-step`: Evidence を無効にして終了コード 3 で拒否し、`rollback-step` の実行を案内する。HEAD と計画ファイルは変更しない。
- [x] **TEAM-22 成功したジョブの片付け**: 統合に成功したジョブの `teams cleanup`（`--force` なし）は、worktree 内の無視ファイルのうち、次のものを保全の対象とせずに削除する。
  - Auto Developer の生成物（`NIGHTLY_REPORT.md`, `.dev/`, `logs/`）
  - CORE-11 のキャッシュ類
  - 削除の前に、そのチームの Bundle が統合ジョブの保存先（TEAM-5）にあることを確かめる。なければ削除しない。
  - それ以外の未コミットのファイルや無視ファイルがある場合は、従来どおり拒否する（`--force` なら隔離してから削除する）。
- [x] **TEAM-23 隔離ブランチとジョブの対応**: チームのジョブで作る隔離ブランチの名前には、ジョブ ID を含める（`quarantine/<job_id>/<team_id>/...`）。`teams status`、`brief`、`NIGHTLY_REPORT.md` は、対象ジョブの隔離ブランチだけを表示・集計する。
- [x] **TEAM-24 雛形をそのまま使う受入テスト**（欠陥あり。Phase 70 の TEAM-28 で修正）: 新しい受入テストで、一時リポジトリに対して次を確かめる。TEAM-19 の既存テストは変更しない。
  - 準備: `auto-dev init` と `auto-dev teams init --teams core,ui` の雛形を、そのまま使う（計画ファイルのコメントを削らない）。追記するのは、タスク行と、Python のテスト1件だけとする。
  - 実行環境: テストのサブプロセスでは、`PYTHONDONTWRITEBYTECODE` と `PYTHONPYCACHEPREFIX` を外す（バイトコードが worktree の中に生成される状態で実行し、環境によって結果が変わらないようにする）。
  - (a) `teams run` が成功し、RC タグができる。計画ファイルの進捗が、コメント内の記法例を数えずに正しく表示される。
  - (b) 続けて `teams cleanup --job-id <id>`（`--force` なし）が成功し、隔離ブランチを作らない。そのあと、新しいジョブ ID の `teams prepare` が成功する。
  - (c) 一方のチームの Maker が担当範囲外に書いた場合、そのチームの作業が、そのジョブの隔離ブランチ（TEAM-23）に退避される。worktree はクリーンに戻り、統合は行われない。
- [x] **TEAM-25 `teams init` の基点ブランチ**: `teams init` は `teams.json` の `base.ref` に、現在のブランチ名を書く。コミットがまだないリポジトリでも、`HEAD` が指すブランチ名を使う。ブランチ名が取れない場合（detached HEAD など）は `main` を書き、その旨を表示する。
- [x] **TEAM-26 統合ブランチの片付け**: 統合に成功したジョブの `teams cleanup` は、統合ブランチ `integration/<job_id>` も削除する。RC タグ `rc/<job_id>` は残す。統合に失敗したジョブ・未完了のジョブの統合ブランチは削除しない。
- [x] **TEAM-27 変更のないチーム**: 統合の時点で、基点から自分のブランチへのコミットが0件のチームを「変更なし（`NO_CHANGES`）」として扱う。
  - そのチームについては Bundle の検証とマージを省き、統合の結果とレポートに `NO_CHANGES` と表示する。
  - すべてのチームが `NO_CHANGES` なら、RC タグを作らずにその旨を表示し、終了コード 0 とする。
  - コミットが1件以上あるのに Evidence の記録がないチームは、従来どおり拒否する（TEAM-6）。
  - 計画に未完了のタスクがあるチームは、従来どおり統合を拒否する（TEAM-14）。
  - `teams run` も同じ規則に従う。

### 4.9 REL — リリースと互換性

- [x] **REL-1 Bundle の形式バージョン**: `bundle_manifest.json` に `format_version`（v0.9.0 は 2）と `tool_version` を記録する。`verify-bundle` は、`format_version` がないか 2 未満の Bundle について、「v0.8 以前の形式です。v0.8.0 の auto-dev で検証してください」という趣旨を示して終了コード 1 を返す（v0.8 以前の Bundle との互換は持たない）。
- [x] **REL-3 CI**: CI は macOS と Linux（Ubuntu 24.04）、Python 3.10 と 3.12 の組み合わせで検証スクリプトを実行する。
  - Linux では bubblewrap と、その AppArmor プロファイルを準備する。
  - `AUTO_DEV_REQUIRE_SANDBOX=1` を設定し、サンドボックスのテストをスキップさせない。
- [x] **REL-4 型検査の両 platform 対応**: `mypy --platform linux src scripts` と `mypy --platform darwin src scripts` の両方が通る。`sys.platform` で分岐する処理では、分岐の前で変数の型を宣言する。

### 4.10 DX — 利用者の体験

- [x] **DX-2 モデル名の既定値**: CLI の `run` と `teams run` で、`--maker-cmd` / `--checker-cmd`（`--enable-audit` を付けたときは `--auditor-cmd`）を指定して、対応する `--maker-model` / `--checker-model` / `--auditor-model` を省略した場合は、指定を求めるメッセージを出して終了コード 2 とする。
  - 特定ベンダーのモデル名を、暗黙に記録しない。
  - `--mock` のときは、従来どおり既定値を使う。
  - ライブラリ（`NightlyOrchestrator` などのクラス）の既定値は変えない。
- [x] **DX-5 Python の選択と src/ の構成**: 次の3点を変える。
  - `auto-dev init` が作る検証スクリプトは、テストの実行に、環境変数 `PYTHON` があればそれを、なければ PATH の `python3` を使う（`/usr/bin/python3` を優先しない）。
  - `src/` ディレクトリがあれば、`PYTHONPATH` に加えてテストを実行する。
  - 雛形の区切りの文字列「Running Project Tests」、テストの節に `|| true` を含まないこと、`teams.json` の保護は保つ。
  - ワーカーの既定の起動コマンドは、`"python3"` ではなく `sys.executable` を使う。
- [x] **DX-6 `auto-dev demo`**（欠陥あり。Phase 70 の DX-8 で修正）: 外部のエージェントやモデルを使わずに、次の流れを1コマンドで見せる。
  - 新しい一時ディレクトリに Git リポジトリを作り、`init` の雛形と2タスクの計画を置く。
  - 組み込みのスクリプトの Maker / Checker で2タスクを完了させる。
  - 次に、保護ファイル（`SPEC.md`）を書き換える Maker のタスクを1つ実行し、隔離されることを見せる。
  - `brief` の要約を表示し、Bundle を作って `verify-bundle` で検証する。
  - 各段階で何が起きたかを、短い説明とともに表示する。
  - 期待どおりに進めば終了コード 0、そうでなければ 1 とする。
  - 既定では終了時に一時ディレクトリを削除する。`--keep` を付けると残し、場所を表示する。利用者のカレントディレクトリは変更しない。
  - Maker / Checker のモデル名と系統には、デモであることがわかる値（例: モデル名 `auto-dev-demo-maker`、系統 `demo-maker`）を DX-7 の明示で使い、実在のモデル名は記録しない。
- [x] **DX-7 モデルの系統**: 次の5点を変える。
  - `run`, `teams run`, `commit-step`, `record-review` と独立監査の設定に、`--maker-family` / `--checker-family` / `--auditor-family` を追加し、系統を明示できるようにする。
  - 明示した系統は空でなく、`unknown` でないこと。
  - 指定した系統と、組み込みの判定で分かる系統が食い違う場合は拒否する（系統の偽装を防ぐ）。
  - 系統の独立性の検査（INV-6）は、明示した系統にも同じように適用する。
  - 組み込みの判定に `mistral` / `llama` / `qwen` / `deepseek` を加える。明示がない場合の既存の挙動（不明な系統の拒否、`"pro"` で終了コード 4 など）は変えない。
  - Evidence の記録と `checker_review` / `commit_step` のイベントに、系統が明示か推定か（`family_source`）を記録する。

## 5. 次のリリース（v0.9.0）で追加する要件

<!-- v0.9.0 の目的: 4.8 TEAM を CLI とスキルから使えるようにし、初めての利用者がつまずかずに試せる状態にする。
     Phase 66〜69 で実装した部分は 4 章に移した。以下は残りの要件。
     要件ごとの影響分析は docs/contracts/phase70.md を参照。 -->

### 5.1 Phase 70 — 査読の証跡を機械で強制し、Checker に差し戻されたまま入った変更を直す（Linux のサンドボックスの起動の修正を含む）

<!-- 起点: Phase 67〜69 で、Checker の出力が REJECT なのに APPROVE として記録・コミットされたものが4件あった
     （Checker の完了通知が2回届き、1回目の APPROVE だけを見て記録した。`auto-dev` は判定を自己申告で受け取っていた）。
     INV-6 と GATE-4 は「判定が正しく申告されること」を前提にしていた。この前提を、出力ファイルからの読み取りと照合に置き換える。 -->

- [ ] **SBX-6 サンドボックスの起動の上限と失敗の分類**: 次の3点を変える。
  - 候補テストに設定するプロセス数の上限（`RLIMIT_NPROC`）は、Linux ではスレッド（タスク）単位で数えた、同じユーザーの現在の数に余裕の分を足して決める。プロセスの数だけで決めない（Linux の `RLIMIT_NPROC` はスレッドを数えるため、スレッドの多いプロセスがある環境で bwrap が `Creating new namespace failed: Resource temporarily unavailable` で起動できなくなる）。
  - サンドボックスの起動の確認（SBX-3）には、実際の候補テストの実行で使う値（制限時間と、算出したプロセス数の上限）をそのまま渡して、同じ資源の上限をかける。上限を算出できず、実際の実行で上限をかけない場合は、起動の確認でもかけない（起動の確認の中で算出し直さない）。起動の確認の結果は、渡した値の組ごとに保持し、別の値の組の結果を使わない（失敗の案内の表示を含む）。
  - 候補テストを動かすスクリプトは、サンドボックスの中で、テストの読み込みより前に「開始の目印」（`===AUTO_DEV_RUNNER_STARTED===`）を1行出力する。
  - 候補テストの出力に開始の目印がない場合は、サンドボックスの機構（bwrap / sandbox-exec）がテストの実行より前に失敗したものとして、終了コードや出力の文字列にかかわらず `SANDBOX_UNAVAILABLE` とする。機構のエラーの出力（標準エラー出力）と、OS ごとの対処を示す。機構のエラーの文字列（`bwrap:`、`sandbox-exec:` など）は、表示にだけ使い、分類には使わない。
  - 開始の目印がある場合の分類（結果の目印がない場合のフォールバック、終了コード 5 の扱い、テストが実際に0件だった場合の `NO_TESTS_RAN` を含む）は変えない。
- [ ] **GATE-5 査読の入力と記録**: 次の2つのサブコマンドを用意する。
  - `auto-dev review-input --task-id N --out-dir DIR`: Checker に渡す差分を `DIR/diff.patch` に書き出す。
    - 対象のタスクに、検証を通った Evidence がない場合は、終了コード 3 とする。
    - 差分は「その Evidence のチェックポイントから、未追跡ファイル（無視ファイルを除く）を含む作業ツリー全体まで」とする。
    - 作業ツリーのインデックスは変更しない。
    - 色・外部の diff ツール・リネームの検出に依存しない、決まった形式で書く（以下「正規の差分」）。
  - `auto-dev record-review --task-id N --checker-output FILE --diff-file FILE --spec-file FILE --maker-model M --checker-model M [--maker-family F] [--checker-family F] [--decision D] [--reason R]`: 3つのファイルを照合し、査読を記録する。
    - 判定は、Checker の出力ファイルから CORE-6 の規則で読み取る。判定行がない場合、または APPROVE と REJECT が両方ある場合は、`CHECKER_POLICY_VIOLATION:` を出して終了コード 4 とし、査読を記録しない。
    - `--decision` を指定した場合、出力から読み取った判定と食い違えば、終了コード 4 とする。
    - 対象のタスクに、検証を通った Evidence がない場合は、終了コード 3 とする。
    - `--diff-file` が、記録する時点の正規の差分と1バイトでも違う場合は、終了コード 3 とする。
    - `--spec-file` が空の場合、または今の `SPEC.md` の連続した一部（要約ではない原文）でない場合は、終了コード 4 とする。
    - 系統の独立性の検査（GATE-4、INV-6）はここでは行わず、記録だけする（GATE-6 でコミットのときに行う）。明示した系統の入力の検査（DX-7: 空・`unknown`・組み込みの判定との食い違いは終了コード 2）は、ここでも行う。
    - 正規の差分に含まれる機密に当たるファイル（INV-5）の扱いは、この要件では変えない（GATE-8 で決める）。
    - 記録が成功したら（REJECT の判定を含む）、終了コード 0 とする。記録するのは、その Evidence（検証）との対応、3つのファイルのパスと SHA-256、読み取った判定、モデルと系統、記録した時刻である。3つのファイルの写しは Evidence の保存先に置く。
    - 査読の記録は Evidence を消費しない。計測のイベント（`checker_review`）の書き込みに失敗しても、記録の結果は変わらない（INV-8）。
  - 旧来の、`--checker-output` を指定しない `record-review` は、終了コード 2 で拒否し、新しい使い方を示す。
- [ ] **GATE-6 コミット時の査読の照合**: `commit-step` は、Evidence の照合・消費より前に、次のすべてを確かめる。満たさない場合は、HEAD・計画ファイル・Evidence を変えずに拒否する。
  - そのタスクの最新の Evidence（検証）に対応する査読の記録がある。ない場合、または古い検証に対応する記録しかない場合は、`CHECKER_POLICY_VIOLATION:` を出して終了コード 4 とする。
  - 記録された判定が APPROVE である。そうでなければ終了コード 4 とする。
  - 記録したときのパスにある Checker の出力ファイルを読み直し、SHA-256 が記録と一致する。記録した後に出力が書き換わった場合は、終了コード 4 とする。
  - `--maker-model` / `--checker-model`（と系統の明示）が、査読の記録と一致する。そのうえで GATE-4 の系統の検査を行う。一致しない場合は終了コード 4 とする。
  - `--checker-decision` は省略できる。指定した場合は、記録された判定と一致しなければ終了コード 4 とする。
  - 今の正規の差分の SHA-256 が、査読の記録と一致する。一致しない場合は終了コード 3 とする。
  - 成功時は、Evidence の記録の `checker.review` に、`output_sha256`, `diff_sha256`, `spec_sha256`, `recorded_at` と、判定の出所（`decision_source: "checker_output"`）を残す。
- [ ] **GATE-7 `run` の経路の査読の証跡**: `auto-dev run`（`teams run` と `demo` を含む）が Checker のコマンドを起動するときは、次を GATE-5 と同じ形式で Evidence の保存先に記録し、GATE-6 と同じ項目を Evidence の記録に残す。
  - Checker に渡した正規の差分と SPEC
  - Checker の出力
  - それぞれの SHA-256
- [ ] **BDL-4 Bundle の査読の証跡**: 次の3点を変える。
  - Bundle には、収録するタスクごとに、査読の3つのファイルの写しを入れる。
  - `bundle_manifest.json` の `format_version` は 3 とする。
  - `verify-bundle` は、各タスクについて次の3つを確かめ、満たさなければ INVALID とする。`format_version` が 3 未満の Bundle は、v0.9 の開発版までの形式として、REL-1 と同じように案内を出して拒否する。
    - 査読の記録があること
    - Bundle 内のファイルの SHA-256 が記録と一致すること
    - Bundle 内の Checker の出力から CORE-6 の規則で読み取った判定が APPROVE であること
  - 査読の記録がない Evidence の記録（GATE-6 より前にコミットしたタスク）は、Bundle に入れず、`excluded_records` に理由 `NO_REVIEW_EVIDENCE` で記録する。この理由の除外があるときは、ほかの検査をすべて満たしても、`verify-bundle` の結果を VERIFIED ではなく INTEGRITY_ONLY とし、対象のタスク ID を示す。Bundle に入っている記録に査読の証跡が欠けている場合は、これまでどおり INVALID とする。
- [ ] **RCV-5 失敗ログの原因**: `logs/nightly/task_<id>_failure.log` の先頭に、次の2行を書く。
  - 失敗した段階（`Failure Phase:`。例: `PROTECTED_FILES`, `MAKER_NO_DIFF`, `VERIFY`, `CHECKER_REJECT`, `BOUNDARY`）
  - 原因（`Reason:`。保護ファイルの違反なら、そのファイルの一覧）
- [ ] **DX-8 デモの修正（DX-6 の欠陥）**: 次の4点を直す。
  - デモの Checker は、ファイル名などで独自に拒否せず、差分が空でなければ APPROVE を返す（保護ファイルを止めるのは INV-1 の検知であることを見せる）。
  - 隔離の段階では、失敗ログの `Failure Phase: PROTECTED_FILES`（RCV-5）を確かめ、それ以外の原因で隔離された場合は終了コード 1 とする。
  - Bundle の検証の判定は、`verify-bundle --json` の `status` が `VERIFIED` と完全に一致することで行う。
  - 段階ごとの失敗（隔離の段階で終了コードが 1 でない、隔離ブランチがない、隔離の原因が違う、Bundle が VERIFIED でない）で終了コード 1 になることを、テストで確かめる。
- [ ] **RPT-8 チームのジョブのレポートの修正（RPT-7 の欠陥）**: 次の4点を直す。
  - 「main（基点）は変更されていない」と表示するのは、記録された基点のコミットと、今の基点の ref が指すコミットの両方が取れて、一致する場合に限る。どちらかが取れなければ「不明」と表示する。値がないときの既定を「未変更」にしない。
  - Checker・Maker のモデル名は、そのタスクの Evidence の記録（保全済みの Bundle、または worktree）からだけ読む。記録がなければ「記録なし」と表示し、ジョブの設定やイベントログから補わない。
  - 単一チームの要約の集計の挙動は、1de845d の時点に戻す。
  - パスの先頭の `./` は、接頭辞としてだけ取り除く。
- [ ] **BDL-5 記録の読み取り場所の修正（BDL-3 の欠陥）**: 次の4点を直す。
  - Evidence の記録をどこから読むかは、`EvidenceStore` が保存先を決める規則と同じ規則（1つの関数）で決める。ディレクトリの列挙と、中身から記録かどうかを推測する判定（`token` の有無など）をやめる。
  - 計画ファイルにタスクとして読める行がなく、すべての記録を対象にする場合でも、レポートの「今回の成果」には状態が `COMPLETED` の記録だけを載せ、件数と一覧を一致させる。
  - 対象の計画ファイルを指定する経路は1つにする。
  - `teams.json` が不正なときの扱いは、`report` / `brief` / `bundle` で揃える（`TEAMS_CONFIG_ERROR:` と終了コード 2）。
- [ ] **TEAM-28 雛形の受入テストの実行環境（TEAM-24 の欠陥）**: TEAM-24 の受入テストは、次の2点で、どの OS でも同じ検査をする。
  - 検証スクリプトが使う Python を、環境変数 `PYTHON`（DX-5）で、`sys.pycache_prefix` が設定されていないものに固定する。
  - `teams cleanup` の前に、worktree の中に `__pycache__/*.pyc` があることを、条件なしで確かめる。
- [ ] **CORE-12 スキルの査読の手順**: `skills/nightly-run/SKILL.md` の単一チームとチームの両方の経路を、次の手順にする。
  1. `review-input` で差分を作る。
  2. SPEC の該当節を原文のまま切り出す。
  3. Checker のプロセスが終わり、出力ファイルに判定行がちょうど1つあることを確かめる。完了の通知だけでは次に進まない。
  4. `record-review` に3つのファイルを渡す。
  5. `commit-step` を実行する。
- [ ] **GATE-8 正規の差分の機密（INV-5）**: `review-input` と `record-review` が作る正規の差分（GATE-5）で、次のように機密を出さない。
  - 内容が INV-5 の機密の内容の規則（秘密鍵・API キーなどの形。隔離ブランチの作成と同じ規則）に当たる行は、行ごと `[auto-dev: INV-5 の規則により省略]` に置き換える。ファイルのほかの行は変えない。
  - ファイル名が `.env*`、鍵ファイル（`*.key` / `*.pem` / `*.pkcs12` / `*.pfx`）、SSH の鍵の名前（`id_rsa` / `id_ed25519` / `id_ecdsa`）に当たるファイルは、内容を出さず、変更があったことだけを1行で示す。ハッシュは書かない。
  - 名前に `secret` / `token` / `credential` を含むことだけを理由に、ファイルの内容を省略しない（Checker から隠せる抜け穴にしない）。
  - 置き換えは決まった規則で行う。`record-review` と `commit-step` の照合（1バイトの一致、SHA-256 の一致）は変えない。

### 5.2 Phase 71 — README・サンプル・リリース

<!-- README は「人間が書くもの」として、Phase 71 の準備コミットで先に書く（契約の一部）。DX-1 はその README に対して Red から始める。
     クイックスタートの中心は `pip install` → `auto-dev demo`。 -->

- [ ] **DX-1 README のクイックスタートをテストする**: README（日本語・英語）に書いたクイックスタートのコマンドを、一時ディレクトリでそのまま実行するテストを用意し、README と実際の挙動のずれを検査で検出する。対象は `auto-dev demo`、新しいリポジトリでの `init`、および `teams init` から `teams prepare` まで。外部のエージェントやモデルを要する手順は対象外とし、README の中でそれとわかるように書く。
- [ ] **DX-3 サンプル**: `examples/two-teams/` に、2チーム構成の最小プロジェクトを置く（ad-team-trial を原型とする電卓。`teams.json`, `plans/`, SPEC, 検証スクリプト）。DX-1 のテストから、`teams init` の代わりにこのサンプルを使う経路も確かめる。
- [ ] **DX-4 CLI リファレンスの生成**: `docs/cli.md` を、CLI の引数定義（argparse）から生成するスクリプトを置く。生成結果と `docs/cli.md` が一致しない場合に失敗するテストを用意し、CLI の変更でリファレンスが古くならないようにする。
- [ ] **REL-2 バージョン表記**: `pyproject.toml`, `plugin.json`, `auto_developer.__version__`, CLI の表示を 0.9.0 に揃える。タグは `v0.9.0` の形式とする（v0.8.0 までの `v.0.x.0` 形式は使わない）。 `CHANGELOG.md` に `## [0.9.0]` 節を、`docs/releases/v0.9.0.md` にリリースノートを置く。

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
