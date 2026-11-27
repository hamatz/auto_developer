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
- Checker への入力は、現在のフェーズの節を機械的に切り出した原文とする（要約しない）。例（Phase 80）:
    awk '/^### 5\.1/,/^## 6\./' SPEC.md > .dev/checker/spec_current.md
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
- **保護ファイル**: Maker が作成・変更・削除してはならないファイル。`.github/**`, `SPEC.md`, `scripts/verify_local.sh`, `tests/immunity/**`, `teams.json`、承認した IR（`docs/designs/` の直下の `*.ir.json`。GATE-14）。
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
- **INV-11 表示は記録から**: 状態の表示（`status`、ダッシュボード）は、Auto Developer 自身が書いた記録（イベントログ、Evidence と査読の記録、ジョブとチームの状態、Git）と計画ファイルから作る。エージェントが進み具合を自己申告で書いたファイルを、表示の元にしない。計画ファイルの完了の印は、記録と照らし合わせて示す。
- **INV-12 Spec Compiler は契約を書かない**: Spec Compiler（`auto-dev compile`）は、SPEC.md・PLAN.md・`teams.json`・保護ファイルを作成・変更・削除しない。出力は標準出力（と、後のフェーズで加える `.dev/compile/` の下の下書き）だけとする。下書きを契約にするのは、人間が読んで承認し、人間がコミットしたときだけである。

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
- [x] **CORE-12 スキルの査読の手順**: `skills/nightly-run/SKILL.md` の単一チームとチームの両方の経路を、次の手順にする。
  1. `review-input` で差分を作る。
  2. SPEC の該当節を原文のまま切り出す。
  3. Checker のプロセスが終わり、出力ファイルから GATE-5 の判定の読み取りの規則で判定が1つに決まることを確かめる。完了の通知だけでは次に進まない。
  4. `record-review` に3つのファイルを渡す。
  5. `commit-step` を実行する。

### 4.2 GATE — 保護と証跡

- [x] **GATE-1 保護ファイル検査**: チェックポイントからの差分と `git status --porcelain` の両方で保護ファイルを検査する（INV-1）。`verify-step` は違反を検知したら Evidence を発行しない。
- [x] **GATE-2 Evidence の発行**: `verify-step` は検証スクリプトが成功したときだけ、PLAN.md の完了反映を含む作業ツリーの Tree SHA、SPEC ハッシュ、検証の結果を拘束した Evidence を発行する。
- [x] **GATE-3 証跡つきコミット**: `commit-step` は Tree SHA と SPEC ハッシュを再計算し、Evidence と一致しなければ終了コード 3 で拒否する。成功したら Evidence を消費し、監査レコード（`.git/nightly_evidence/`、worktree では `.dev/evidence/`）として保存する。
- [x] **GATE-4 Checker 方針の検査**: `commit-step` は `--maker-model`, `--checker-model`, `--checker-decision` を必須とする。判定が APPROVE 以外の場合、または INV-6 に反する場合は、`CHECKER_POLICY_VIOLATION:` を出して終了コード 4 で拒否する。このとき Evidence は消費せず、HEAD と PLAN.md も変更しない。監査レコードには判定・理由・両モデル名・系統を残す。
- [x] **GATE-5 査読の入力と記録**: 次の2つのサブコマンドを用意する。
  - `auto-dev review-input --task-id N --out-dir DIR`: Checker に渡す差分を `DIR/diff.patch` に書き出す。
    - 対象のタスクに、検証を通った Evidence がない場合は、終了コード 3 とする。
    - 差分は「その Evidence のチェックポイントから、未追跡ファイル（無視ファイルを除く）を含む作業ツリー全体まで」とする。
    - 作業ツリーのインデックスは変更しない。
    - 色・外部の diff ツール・リネームの検出に依存しない、決まった形式で書く（以下「正規の差分」）。
  - `auto-dev record-review --task-id N --checker-output FILE --diff-file FILE --spec-file FILE --maker-model M --checker-model M [--maker-family F] [--checker-family F] [--decision D] [--reason R]`: 3つのファイルを照合し、査読を記録する。
    - 判定は、Checker の出力ファイルから、次の規則（CORE-6 を明確にしたもの。以下「判定の読み取りの規則」）で読み取る。`run` の経路（GATE-7）と `verify-bundle`（BDL-4）も同じ規則を使う。
      - 出力全体（前後の空白を除く）が1つの JSON オブジェクトとして読め、`decision` が `APPROVE` または `REJECT` のときは、その判定とする。
      - それ以外の出力では、判定行（行全体が `APPROVE` / `REJECT`。`DECISION:` の前置可）がちょうど1種類あるときに、その判定とする。
      - 出力の一部に含まれる JSON（差分やコードの引用など）は、判定に使わない。
    - 判定が1つに決まらない場合（判定行がない、APPROVE と REJECT が両方ある、など）は、`CHECKER_POLICY_VIOLATION:` を出して終了コード 4 とし、査読を記録しない。
    - `--decision` を指定した場合、出力から読み取った判定と食い違えば、終了コード 4 とする。
    - 対象のタスクに、検証を通った Evidence がない場合は、終了コード 3 とする。
    - `--diff-file` が、記録する時点の正規の差分と1バイトでも違う場合は、終了コード 3 とする。
    - `--spec-file` が空の場合、または今の `SPEC.md` の連続した一部（要約ではない原文）でない場合は、終了コード 4 とする。
    - 系統の独立性の検査（GATE-4、INV-6）はここでは行わず、記録だけする（GATE-6 でコミットのときに行う）。明示した系統の入力の検査（DX-7: 空・`unknown`・組み込みの判定との食い違いは終了コード 2）は、ここでも行う。
    - 正規の差分に含まれる機密に当たるファイル（INV-5）の扱いは、この要件では変えない（GATE-8 で決める）。
    - 記録が成功したら（REJECT の判定を含む）、終了コード 0 とする。記録するのは、その Evidence（検証）との対応、3つのファイルのパスと SHA-256、読み取った判定、モデルと系統、記録した時刻である。3つのファイルの写しは Evidence の保存先に置く。
    - 査読の記録は Evidence を消費しない。計測のイベント（`checker_review`）の書き込みに失敗しても、記録の結果は変わらない（INV-8）。
  - 旧来の、`--checker-output` を指定しない `record-review` は、終了コード 2 で拒否し、新しい使い方を示す。
- [x] **GATE-6 コミット時の査読の照合**: `commit-step` は、Evidence の照合・消費より前に、次のすべてを確かめる。満たさない場合は、HEAD・計画ファイル・Evidence を変えずに拒否する。
  - そのタスクの最新の Evidence（検証）に対応する査読の記録がある。ない場合、または古い検証に対応する記録しかない場合は、`CHECKER_POLICY_VIOLATION:` を出して終了コード 4 とする。
  - 記録された判定が APPROVE である。そうでなければ終了コード 4 とする。
  - 記録したときのパスにある Checker の出力ファイルを読み直し、SHA-256 が記録と一致する。記録した後に出力が書き換わった場合は、終了コード 4 とする。
  - `--maker-model` / `--checker-model`（と系統の明示）が、査読の記録と一致する。そのうえで GATE-4 の系統の検査を行う。一致しない場合は終了コード 4 とする。
  - `--checker-decision` は省略できる。指定した場合は、記録された判定と一致しなければ終了コード 4 とする。
  - 今の正規の差分の SHA-256 が、査読の記録と一致する。一致しない場合は終了コード 3 とする。
  - 成功時は、Evidence の記録の `checker.review` に、`output_sha256`, `diff_sha256`, `spec_sha256`, `recorded_at` と、判定の出所（`decision_source: "checker_output"`）を残す。
- [x] **GATE-7 `run` の経路の査読の証跡**: `auto-dev run`（`teams run` と `demo` を含む）が Checker のコマンドを起動するときは、次を GATE-5 と同じ形式で Evidence の保存先に記録し、GATE-6 と同じ項目を Evidence の記録に残す。
  - Checker に渡した正規の差分と SPEC
  - Checker の出力
  - それぞれの SHA-256
  - 判定は、GATE-5 の判定の読み取りの規則で、Checker の出力から読み取る。どのアダプタ（外部コマンド・組み込みのモック・Python で書いたアダプタ）でも同じとし、`run_checker` の返り値の真偽値は、偽のときに不承認とするためだけに使う。
  - 承認の場合: 3つのファイルと記録を GATE-5 と同じ場所・形式で保存し、発行する Evidence の記録の `checker.review` に GATE-6 と同じ項目を残す。検証の Evidence はまだないため、対応は査読した作業ツリーの Tree SHA と SPEC の SHA で示し、発行する Evidence と一致することを確かめる。
  - 不承認の場合（判定が読み取れない場合を含む）: 3つのファイルと、GATE-5 と同じ形式の記録（各ファイルの SHA-256、判定、モデルと系統、記録した時刻）を `reviews/rejected/<job_id>/task_<id>/` に保存する。上書きしない。Evidence との対応は空とする。保存の成否は、タスクの失敗・隔離の結果を変えない。不承認の査読は Bundle に入れない。
- [x] **GATE-8 正規の差分の機密（INV-5）**: `review-input` と `record-review` が作る正規の差分（GATE-5）で、次のように機密を出さない。
  - 内容が INV-5 の機密の内容の規則（秘密鍵・API キーなどの形。隔離ブランチの作成と同じ規則）に当たる行は、行ごと `[auto-dev: INV-5 の規則により省略]` に置き換える。ファイルのほかの行は変えない。
  - ファイル名が `.env*`、鍵ファイル（`*.key` / `*.pem` / `*.pkcs12` / `*.pfx`）、SSH の鍵の名前（`id_rsa` / `id_ed25519` / `id_ecdsa`）に当たるファイルは、内容を出さず、変更があったことだけを1行で示す。ハッシュは書かない。
  - 名前に `secret` / `token` / `credential` を含むことだけを理由に、ファイルの内容を省略しない（Checker から隠せる抜け穴にしない）。
  - 置き換えは決まった規則で行う。`record-review` と `commit-step` の照合（1バイトの一致、SHA-256 の一致）は変えない。
- [x] **GATE-9 検証で本物のインデックスを変えない**: `verify-step`（`run` の経路の検証を含む）は、作業ツリーの Tree SHA を、一時的なインデックス（`GIT_INDEX_FILE`）で求め、本物のインデックスを変更しない。`verify-step` の前後で、計画ファイルの完了の印を除き、`git status --porcelain` の出力が変わらないことを、テストで確かめる（`.env` などの未追跡ファイルが、`verify-step` によってステージされないこと）。
  - `commit-step` は、これまでどおり「検証した Tree SHA と一致するツリー」だけをコミットする。コミットの直前に求めた Tree SHA のツリーを、そのままコミットする（`read-tree` してからコミットするなど）。コミットしたツリーと検証したツリーの照合（一致しなければ致命的な誤りとして止める）は変えない。
  - 検証した後に作業ツリーに加わったファイルを、コミットに含めない挙動は変えない（`tests/test_nightly_orchestrator.py` の `commit_task must NOT restage unverified files` の検査）。
  - 隔離（`rollback-step` と失敗時の隔離）が、退避のためにステージする挙動は変えない。
- [x] **GATE-10 機密のファイルをコミットしない（INV-5）**: 次の3点を変える。
  - `auto-dev init` と `auto-dev teams init` は、CORE-11 の行に加えて、`.gitignore` に `.env`、`.env.*`、`!.env.example` を追記する（既にある行は重複させない。CORE-11 と同じ規則）。
  - `verify-step` と `commit-step`（`run` / `teams run` の経路を含む）は、コミットするツリーに、チェックポイントから追加または変更された次のファイルが含まれる場合、`SECRET_FILE_IN_COMMIT:` とそのパスを出して拒否する。`.gitignore` に加えるよう案内も出す。
    - ファイル名が `.env` または `.env.*`（`.env.example`, `.env.sample`, `.env.template` を除く）
    - 鍵ファイル（`*.key`, `*.pem`, `*.pkcs12`, `*.pfx`）と、SSH の鍵の名前（`id_rsa`, `id_ed25519`, `id_ecdsa`）
  - 拒否のときの扱い: `verify-step` は検証の失敗（終了コード 1）とし、Evidence を発行しない。`commit-step` は終了コード 4 とし、HEAD・計画ファイル・Evidence を変えない。`run` の経路では、そのタスクの失敗として隔離する（隔離ブランチには、INV-5 のとおり機密を入れない）。
  - 名前に `secret` / `token` / `credential` を含むことだけを理由に拒否しない（GATE-8 と同じ）。チェックポイントの時点で既に追跡されていて、変更されていないファイルは対象にしない。
- [x] **GATE-11 機密の検査のファイル名の扱い（GATE-10 の修正）**: 次の3点を直す。
  - `SECRET_FILE_IN_COMMIT` の検査（GATE-10）は、ファイル名を引用符で囲まれない形（`-z` による NUL 区切り、または `core.quotePath=false`）で読み、日本語などを含むファイル名（例: `鍵.pem`）も名前の規則で判定する（BACKLOG AUD-1004）。
  - 正規の差分の機密の省略（GATE-8）は、日本語などを含むファイル名の中身を、今も正しく省略している。ただし省略の表示のファイル名が8進数の形（`\351\215\265.pem`）になるので、同じ読み方にそろえて、元のファイル名で表示する。省略の規則は変えない。
  - `SECRET_FILE_IN_COMMIT` の検査は、チェックポイントからの追加・変更に加えて、種類の変更（`T`。シンボリックリンクから通常のファイルへ、など）も対象にする（BACKLOG AUD-1005）。
- [x] **GATE-12 査読の記録の履歴**: `record-review` は、記録するたびに、3つのファイルの写しと記録（各ファイルの SHA-256・判定・モデル・時刻）を `reviews/task_<id>/history/<連番>/` にも残し、上書きしない（REJECT を含む）。最新の記録（GATE-6 が照合するもの）と、Bundle に入れるもの（BDL-4）は変えない。Checker の出力をどこに置いた場合でも、やり直しの履歴が残るようにするためである。
- [x] **GATE-13 査読の入力と記録の厳格化**: GATE-5 の `review-input` と `record-review` を、次のように変える。`run` の経路（GATE-7）は変えない。
  - **検証の後の作業ツリー**: `review-input` と `record-review` は、そのタスクの最新の Evidence の Tree SHA が、今の作業ツリーの Tree SHA（`verify-step` と同じ求め方。GATE-9）と一致しない場合は、終了コード 3 とし、`verify-step` を先に実行するよう案内する。検証の後に作業ツリーを変えたまま、査読の入力を作らず、記録もしない。
  - **1回の検証に1回の査読**: `record-review` は、同じ Evidence（検証）に対して、判定の決まった査読を1回だけ記録する。すでに記録がある場合は、`CHECKER_POLICY_VIOLATION:` を出して終了コード 4 とし、記録しない。REJECT を記録した後に、検証をやり直さずに査読だけをやり直して APPROVE を記録することを防ぐ（INV-4）。
  - **判定の決まらない出力も残す**: 判定が1つに決まらない出力（GATE-5 の終了コード 4）も、3つのファイルの写しと記録（判定は空）を GATE-12 の履歴に残す。最新の記録は変えない。このあと、同じ Evidence に対して、判定の決まった査読を1回記録できる（出力が途中で切れた場合の実行し直しのため）。
  - **入力の記録**: `review-input` は、イベント `review_input`（タスク・チェックポイント・差分の SHA-256）を書く。`status` は、最新の Evidence について、`review_input` の回数が査読の履歴（判定の決まらないものを含む）の件数より多い場合に、`attention[]` に `REVIEW_NOT_RECORDED` として入れる。イベントの書き込みに失敗しても、結果は変えない（INV-8）。
  - スキル（`/nightly-run`）に、「1回の `review-input` に対して Checker は1回。Checker の出力は、判定が決まらない場合も含めて、必ず `record-review` に渡す」ことを書く。
- [x] **GATE-14 承認した IR の保護**: `docs/designs/` の直下の `*.ir.json`（人間が承認した IR）を、保護ファイル（INV-1）に加える。
  - `verify-step` と `commit-step` の保護ファイルの検査（GATE-1。追跡・未追跡の両方）で、作成・変更・削除を検知する。
  - `auto-dev init` が作る検証スクリプトの保護ファイルの検査にも加える。
  - スキル `/nightly-compile` に、承認した IR は人間が `docs/designs/<name>.ir.json` としてコミットすること（エージェントは書かないこと）を書く。
- [x] **GATE-15 低レベルのコマンドの経路での Fail-Fast（INV-4）**: `verify-step` と `commit-step` は、対象のタスクの失敗の回数を、イベントの記録から数える。
  - 数える範囲: 同じフェーズ・同じタスク（チームの worktree では同じチーム）のイベントのうち、そのタスクの最後の `commit_step`（`COMMITTED`）か `rollback_step` より後のもの。`task_start`（`pre-flight`）では数え直さない。
  - 失敗として数えるもの: `verify_step` の結果が `PASSED` 以外のもの（検証の失敗、打ち切り、保護ファイルの違反など）と、`checker_review` の判定が `REJECT` のもの。判定の決まらない査読（GATE-13）は数えない。
  - 失敗が2回以上あるとき、`verify-step` は検証スクリプトを実行せず、Evidence も発行せず、`INV4_FAIL_FAST:` で始まるメッセージ（失敗の内訳と、`rollback-step` で隔離するよう案内）を出して終了コード 1 とする。このときの `verify_step` のイベントの結果は `FAIL_FAST_STOPPED` とし、失敗の数には入れない。
  - 同じ条件のとき、`commit-step` は、HEAD・計画ファイル・Evidence を変えずに、`INV4_FAIL_FAST:` を出して終了コード 4 とする。
  - イベントの記録が読めない場合は、この検査を行わず、警告を出して従来どおりに進める（INV-8: 計測は関門の結果を変えない。記録が読めないことを理由に止めない）。
  - `run` / `teams run` の経路（CORE-4 の再試行の上限）は変えない。
  - スキル（`/nightly-run`）に、`INV4_FAIL_FAST:` が出たら `rollback-step` で隔離して止まることを書く。

### 4.3 RCV — 失敗時の保全と復旧

- [x] **RCV-1 隔離**: 失敗したら、追跡しているファイルの変更を隔離ブランチ（`quarantine/<id>-<時刻>`。チームでは TEAM-23 の名前）にコミットし、未追跡ファイルと無視ファイル（機密を除く。INV-5）を含む作業ツリー全体を、スナップショット（`.dev/quarantines/snapshot_task_<id>_<時刻>.tar.gz`）と照合用の一覧（`manifest_task_<id>_<時刻>.json`）に退避する。新しく作った未追跡のファイルは、隔離ブランチには入らず、スナップショットにだけある（RCV-6 で案内する）。そのうえで本線をチェックポイントに完全に戻し（`git status --porcelain` が空）、`logs/nightly/task_<id>_failure.log` に各試行のログを残して終了コード 1 で停止する。
- [x] **RCV-2 状態の永続化**: ジョブの状態（`QUEUED` / `RUNNING` / `SUSPENDED` / `COMPLETED` / `FAILED`）と段階を `.dev/job_state.json` に原子的に書き込む。
- [x] **RCV-3 再開**: `run --resume` は、再開可能な状態で、HEAD がチェックポイントと一致するときだけ再開する。コミット済みのタスクを二重にコミットしない。
- [x] **RCV-4 クラッシュ復旧**: `auto-dev recover inspect | plan | apply` は、中断したジョブの状態を診断し、修復の計画を示し、冪等に適用する。オーケストレーターが動作中なら適用を拒否する。
- [x] **RCV-5 失敗ログの原因**: `logs/nightly/task_<id>_failure.log` の先頭に、次の2行を書く。
  - 失敗した段階（`Failure Phase:`。例: `PROTECTED_FILES`, `MAKER_NO_DIFF`, `VERIFY`, `CHECKER_REJECT`, `BOUNDARY`）
  - 原因（`Reason:`。保護ファイルの違反なら、そのファイルの一覧）
- [x] **RCV-6 隔離の案内**: 隔離（RCV-1。`rollback-step`、`run`、`teams run` の経路）のあと、隔離ブランチにはなくスナップショットにだけある新しいファイル（隔離の直前に `git ls-files --others --exclude-standard` で求めた、Git が追跡しておらず `.gitignore` でも無視されていないファイル。機密を除く。INV-5）の一覧と、それを取り出すコマンド（`tar xzf <スナップショット> <パス>...`）を表示し、失敗ログ（RCV-5）にも書く。該当するファイルがない場合は、その旨を1行で示す。今の出力の1行目（`rollback-step` の `ROLLED BACK TO ...`）は変えない。
  - 一覧と案内を求める処理が失敗しても、本線の復元（RCV-1）は最後まで行い、一覧を作れなかったことと理由を1行で示す。
- [x] **RCV-7 隔離で消えるファイルをなくす（INV-3）**: 隔離（RCV-1。`rollback-step`、`run`、`teams run` の経路）を、次のように変える。
  - 隔離の前に、Git が追跡しておらず `.gitignore` でも無視されていないファイル（`git ls-files --others --exclude-standard`）のうち、機密の規則（INV-5。名前と中身）でスナップショットから除外されるものを求める。
  - それらのファイルは、`git clean` の前に `git hash-object -w` で Git のオブジェクトとして書く。隔離ブランチ・スナップショット・Bundle には入れない（INV-5 は変えない）。スナップショットの照合用の一覧（manifest）に、パスとオブジェクトの SHA を記録する（中身は書かない）。
  - 保全の段階で失敗した場合（残すべきファイルを決めるための `git ls-files --others --exclude-standard`、機密の判定のためのファイルの読み取り、`git hash-object -w` のどれかの失敗。オブジェクトを1つでも書けなかった場合を含む）は、隔離ブランチの作成・本線の復元・`git clean` のどれも行わず、作業ツリーをそのままにして終了コード 1 とし、書けなかったファイルと理由を示す（INV-3: 退避が終わる前に消さない）。
  - RCV-6 の案内を直す。「スナップショットにだけある新しいファイル」の一覧と `tar xzf` のコマンドには、実際にスナップショットに入ったファイルだけを並べる。Git のオブジェクトとして残したファイルは、別の見出しで、ファイルごとに `git cat-file -p <SHA> > <パス>` の形の取り出し方を示す。失敗ログ（RCV-5）にも同じものを書く。ファイルの中身は表示しない。
  - 今の出力の1行目（`rollback-step` の `ROLLED BACK TO ...`）と、失敗ログの先頭の2行（RCV-5）は変えない。
  - 保全が終わった後の、RCV-6 の一覧と案内を作る処理の失敗は、RCV-6 のとおり、一覧を作れなかったことを示して本線の復元を最後まで行う。
- [x] **RCV-8 取り出し方の案内のディレクトリ**: RCV-7 の `git cat-file -p <SHA> > <パス>` の案内で、パスにディレクトリが含まれる場合は、`mkdir -p '<ディレクトリ>' && git cat-file -p <SHA> > '<パス>'` の形で示す（隔離の `git clean -fd` で、新しいディレクトリごと消えているため）。パスは、シェルの引用符で正しく囲む。

### 4.4 RPT — レポートと計測

- [x] **RPT-1 Morning Brief**: `NIGHTLY_REPORT.md` には、成果の要約、確認コマンド（検証用と利用用を分けて示す）、初期チェックポイントと最終コミット、隔離ブランチと失敗理由、人間が判断すべきこと、監査の結果を記載する。失敗時の案内は、実行できる手順（隔離ブランチの確認・引き継ぎ・破棄）に限る。
- [x] **RPT-2 単独でのレポート生成**: `auto-dev report` は、ジョブ状態ファイルがなくても、Git・PLAN.md・監査レコード・監査結果からレポートを生成する。`brief` と `bundle` の実行時にもレポートを最新化する。
- [x] **RPT-3 イベントログ**: `verify_step`, `commit_step`, `rollback_step`, `checker_review`, `audit_stage` のイベントを `events.jsonl` に追記だけで記録する。書き込みに失敗しても `METRICS_WARNING:` を出すのみとする（INV-8）。
- [x] **RPT-4 フェーズ集計**: `auto-dev metrics [--phase N] [--json] [--append-to PATH]` は、フェーズごとの指標と直前のフェーズとの差を出力する。指標ごとに、イベントログ由来か Git 由来かを示す。算出できない指標は推定値で埋めずに null とする。指標の定義は METRICS.md に書く。
- [x] **RPT-5 判断待ちの表示**: チームのジョブがあるときの `brief` と `NIGHTLY_REPORT.md` で、「判断待ちの事項」節には、そのジョブで人間が判断すべきことだけを表示する。判断すべきことがなければ「なし」とし、単一チーム用の文言を出さない。
- [x] **RPT-6 レポートと Bundle の順序**: `auto-dev run` は、成功時・失敗時とも、Bundle の出力先を先に決め、そのパスを含むレポートを書いてから Bundle を作る。Bundle に入る `NIGHTLY_REPORT.md` は、その実行の結果を表す。
- [x] **RPT-7 チームのジョブのレポート**（不具合は RPT-8 で修正）: チームのジョブを実行した後の `NIGHTLY_REPORT.md` と `brief` に、チームごとに次を表示する。
  - 完了したタスク
  - 各タスクの Checker の判定とモデル名（保全済みの Bundle、または worktree の Evidence の記録から読む）
  - `NO_CHANGES` のチームはその旨
  - 「最新コミット (HEAD)」の代わりに RC コミットを示し、main（基点）が変更されていないことがわかる表示にする。単一チームのレポートは変えない。
- [x] **RPT-8 チームのジョブのレポートの修正（RPT-7 の欠陥）**: 次の4点を直す。
  - 「main（基点）は変更されていない」と表示するのは、記録された基点のコミットと、今の基点の ref が指すコミットの両方が取れて、一致する場合に限る。どちらかが取れなければ「不明」と表示する。値がないときの既定を「未変更」にしない。
  - Checker・Maker のモデル名は、そのタスクの Evidence の記録（保全済みの Bundle、または worktree）からだけ読む。記録がなければ「記録なし」と表示し、ジョブの設定やイベントログから補わない。
  - 単一チームの要約の集計の挙動は、1de845d の時点に戻す。
  - パスの先頭の `./` は、接頭辞としてだけ取り除く。
- [x] **RPT-8 監査の報告**: `NIGHTLY_REPORT.md` と `brief` の監査の節を、次のように直す。
  - **監査だけの実行**: その実行で新しく完了したタスクがなく、監査を行った場合は、「0 / N 件のタスクが正常に完了」ではなく、「監査だけの実行（計画のタスクは N 件すべて完了済み）」と示す。「すべての予定タスクがコミットされた」「`git log --oneline -n 0`」の案内は出さない。
  - **監査の深さ**: 監査の範囲（AUDIT-6）、組の数（AUDIT-7）、組ごとの入力と出力（うち考えた部分）のトークン数を示す。出力のうち考えた部分が、入力 1 万トークンあたり 100 トークンに満たない組があれば、「監査役の考えた量が入力に比べて少ない（弱い保証）」と注意を出す（判定は変えない）。

### 4.5 BDL — 提出パッケージ（Evidence Bundle）

- [x] **BDL-1 生成**: `auto-dev bundle` は、Git 履歴、ソースのスナップショット、監査レコード、ジョブ状態、ログ、`NIGHTLY_REPORT.md`, `PLAN.md`, `SPEC.md`, `BACKLOG.md`、監査結果を1つの ZIP にまとめる。機密と OS の不要ファイルは除外し、`bundle_manifest.json` に全ファイルの SHA-256 を記録する。ジョブが完了したときにも自動で生成する。
- [x] **BDL-2 検証**: `auto-dev verify-bundle <zip> [--json]` は、ファイルのハッシュ、Evidence とコミットの Tree SHA の一致、SPEC の不変性、Checker の承認を検証して、終了コード 0 / 1 で判定する。監査後に実装が変わっていれば `AUDIT_OUTDATED`、未解決の P0/P1 またはエスカレーションがあれば `RELEASE_BLOCKED` とする。
- [x] **BDL-3 Bundle とレポートに入れる記録の選択**（不具合は BDL-5 で修正）: `auto-dev bundle` と `NIGHTLY_REPORT.md` の生成で、次の規則に従って記録を選ぶ。
  - 対象の計画ファイル（単一チームでは `PLAN.md`、チームの worktree ではそのチームの計画ファイル）で完了（`- [x]`）しているタスクの Evidence の記録だけを対象にする（CORE-10 の読み取り範囲に従う）。
  - 対象外とした記録は Bundle に入れず、manifest の `excluded_records` にタスク ID と理由を記録する。
  - 計画ファイルにタスクとして読める行が1つもない場合は、従来どおりすべての記録を対象にする。
  - `verify-bundle` の検査（BDL-2）は弱めない。
- [x] **BDL-4 Bundle の査読の証跡**: 次の3点を変える。
  - Bundle には、収録するタスクごとに、査読の3つのファイルの写しを入れる。
  - `bundle_manifest.json` の `format_version` は 3 とする。
  - `verify-bundle` は、各タスクについて次の3つを確かめ、満たさなければ INVALID とする。`format_version` が 3 未満の Bundle は、v0.9 の開発版までの形式として、REL-1 と同じように案内を出して拒否する。
    - 査読の記録があること
    - Bundle 内のファイルの SHA-256 が記録と一致すること
    - Bundle 内の Checker の出力から CORE-6 の規則で読み取った判定が APPROVE であること
  - 査読の記録がない Evidence の記録（GATE-6 より前にコミットしたタスク）は、Bundle に入れず、`excluded_records` に理由 `NO_REVIEW_EVIDENCE` で記録する。この理由の除外があるときは、ほかの検査をすべて満たしても、`verify-bundle` の結果を VERIFIED ではなく INTEGRITY_ONLY とし、対象のタスク ID を示す。Bundle に入っている記録に査読の証跡が欠けている場合は、これまでどおり INVALID とする。
- [x] **BDL-5 記録の読み取り場所の修正（BDL-3 の欠陥）**: 次の4点を直す。
  - Evidence の記録をどこから読むかは、`EvidenceStore` が保存先を決める規則と同じ規則（1つの関数）で決める。ディレクトリの列挙と、中身から記録かどうかを推測する判定（`token` の有無など）をやめる。
  - 計画ファイルにタスクとして読める行がなく、すべての記録を対象にする場合でも、レポートの「今回の成果」には状態が `COMPLETED` の記録だけを載せ、件数と一覧を一致させる。
  - 対象の計画ファイルを指定する経路は1つにする。
  - `teams.json` が不正なときの扱いは、`report` / `brief` / `bundle` で揃える（`TEAMS_CONFIG_ERROR:` と終了コード 2）。

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
- [x] **AUDIT-6 監査の入力の範囲**: 独立監査（AUDIT-1）に渡す差分の範囲を、次のように決める。
  - 全体のオプション `--audit-paths <pathspec>`（カンマで区切って複数）を加える。指定した場合は、その Git の pathspec に当たるファイルの差分だけを渡す。
  - 指定しない場合は、起点から対象までの差分から、`tests/`、`docs/`、`examples/` の下のファイルと、拡張子が `.md` のファイルを除いたものを渡す（SPEC.md は、これまでどおり別に渡す）。テストとドキュメントは、実装の欠陥を探す監査の主な対象ではなく、入力を大きくするため。
  - 範囲に当たる差分が空の場合は、監査役を呼ばずに `SUCCESS`（指摘0件）とし、範囲が空だったことを記録と報告に示す。
  - 監査の記録（`audit_stage.json`）と報告に、使った範囲（pathspec の一覧）、差分のファイルの数とバイト数を残す。
  - `diff_sha256` は、範囲で絞った後の差分の SHA-256 とする（Bundle の照合はこれを使う）。
- [x] **AUDIT-7 監査の入力の分割**: 範囲で絞った差分が、全体のオプション `--audit-max-diff-bytes`（既定 200000）より大きい場合は、ファイルの単位で分けて、それぞれを別の監査の依頼にする。
  - 分け方: ファイルのパスの順に並べ、各組の差分の合計が上限を超えないように前から詰める。1つのファイルだけで上限を超える場合は、そのファイルだけで1組とし、上限を超えたことを示す（ファイルの途中では分けない）。
  - 各組の依頼には、SPEC.md の全体と、その組の差分だけを渡す。依頼の文に、全体のうち何組目かと、ほかの組に入っているファイルの一覧（パスだけ）を書く。
  - 指摘は、すべての組の指摘をまとめて、これまでと同じく振り分ける（AUDIT-3）。
  - すべての組が `SUCCESS` のときだけ、全体を `SUCCESS` とする。1組でも途中で切れた・形式が不正・失敗の場合は、全体を `AUDIT_PENDING` とし、どの組かを示す。
  - 記録に `chunks[]`（各組の番号、ファイルの一覧、差分の SHA-256、`provider_request_id`、`stop_reason`、入力と出力のトークン数）を残す。最上位の `provider_request_id` は1組目のものとする（これまでの読み手との互換のため）。
  - `verify-bundle`（BDL-4 の監査の検証）は、`chunks[]` がある記録では、すべての組が `LIVE` で、本物の `provider_request_id` を持つことを確かめる。`chunks[]` がない記録（v0.10 までの形）は、これまでどおりに確かめる。

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
- [x] **SBX-6 サンドボックスの起動の上限と失敗の分類**: 次の3点を変える。
  - 候補テストに設定するプロセス数の上限（`RLIMIT_NPROC`）は、Linux ではスレッド（タスク）単位で数えた、同じユーザーの現在の数に余裕の分を足して決める。プロセスの数だけで決めない（Linux の `RLIMIT_NPROC` はスレッドを数えるため、スレッドの多いプロセスがある環境で bwrap が `Creating new namespace failed: Resource temporarily unavailable` で起動できなくなる）。
  - サンドボックスの起動の確認（SBX-3）には、実際の候補テストの実行で使う値（制限時間と、算出したプロセス数の上限）をそのまま渡して、同じ資源の上限をかける。上限を算出できず、実際の実行で上限をかけない場合は、起動の確認でもかけない（起動の確認の中で算出し直さない）。起動の確認の結果は、渡した値の組ごとに保持し、別の値の組の結果を使わない（失敗の案内の表示を含む）。
  - 候補テストを動かすスクリプトは、サンドボックスの中で、テストの読み込みより前に「開始の目印」（`===AUTO_DEV_RUNNER_STARTED===`）を1行出力する。
  - 候補テストの出力に開始の目印がない場合は、サンドボックスの機構（bwrap / sandbox-exec）がテストの実行より前に失敗したものとして、終了コードや出力の文字列にかかわらず `SANDBOX_UNAVAILABLE` とする。機構のエラーの出力（標準エラー出力）と、OS ごとの対処を示す。機構のエラーの文字列（`bwrap:`、`sandbox-exec:` など）は、表示にだけ使い、分類には使わない。
  - 開始の目印がある場合の分類（結果の目印がない場合のフォールバック、終了コード 5 の扱い、テストが実際に0件だった場合の `NO_TESTS_RAN` を含む）は変えない。
- [x] **SBX-7 macOS のシンボリックリンクの Python**: macOS で、候補テストを動かす Python の実行ファイル（`sys.executable`）がシンボリックリンクで、リンクの先がサンドボックスで読み取りを許可していない場所を通る場合でも、候補テストを起動できるようにする。python.org のインストーラの Python（`/Library/Frameworks/Python.framework`）と、GitHub Actions の setup-python の Python（ホームの中の `hostedtoolcache` から `/Library/Frameworks` へのリンク）がこれに当たる。
  - `sys.executable` からリンクの先の実体まで、たどる途中のリンクそのものと、各パスの親ディレクトリについて、Python の起動に必要な最小の読み取り（リンクの読み取りとメタデータの読み取り）だけを許可する。
  - ホームディレクトリ・`~/.ssh`・元のリポジトリ・`/Library` の機密の場所などの読み取りの禁止（SBX-1、SBX-5 の macOS のテスト）は弱めない。許可するのはパスそのもの（`literal`）に限り、ディレクトリの中身（`subpath`）を新たに許可しない。
  - CI の macOS（Python 3.10 / 3.12）で、`realpath: ... Operation not permitted` による `SANDBOX_UNAVAILABLE` にならない。
- [x] **SBX-8 Linux の `/opt` の確認の修正（SBX-5）**: Linux の実行テストで「許可していないホストのパスが読めない」ことを確かめるとき、候補テストを動かす Python の置き場所として読み取り専用で見せるパス（とその途中のディレクトリ）を、読めてよいものとして除く。Python の置き場所が `/opt` の下にある環境（GitHub Actions の setup-python の `/opt/hostedtoolcache`）でも、置き場所に当たらない `/opt` の中身が見えないことを確かめる。そのような中身がない環境では、この確認をスキップし、理由を表示する。サンドボックスの製品の挙動（Python の置き場所だけをマウントする）は変えない。

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
- [x] **TEAM-24 雛形をそのまま使う受入テスト**（不具合は TEAM-28 で修正）: 新しい受入テストで、一時リポジトリに対して次を確かめる。TEAM-19 の既存テストは変更しない。
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
- [x] **TEAM-28 雛形の受入テストの実行環境（TEAM-24 の欠陥）**: TEAM-24 の受入テストは、次の2点で、どの OS でも同じ検査をする。
  - 検証スクリプトが使う Python を、環境変数 `PYTHON`（DX-5）で、`sys.pycache_prefix` が設定されていないものに固定する。
  - `teams cleanup` の前に、worktree の中に `__pycache__/*.pyc` があることを、条件なしで確かめる。

### 4.9 REL — リリースと互換性

- [x] **REL-1 Bundle の形式バージョン**: `bundle_manifest.json` に `format_version`（v0.9.0 は 2）と `tool_version` を記録する。`verify-bundle` は、`format_version` がないか 2 未満の Bundle について、「v0.8 以前の形式です。v0.8.0 の auto-dev で検証してください」という趣旨を示して終了コード 1 を返す（v0.8 以前の Bundle との互換は持たない）。
- [x] **REL-3 CI**: CI は macOS と Linux（Ubuntu 24.04）、Python 3.10 と 3.12 の組み合わせで検証スクリプトを実行する。
  - Linux では bubblewrap と、その AppArmor プロファイルを準備する。
  - `AUTO_DEV_REQUIRE_SANDBOX=1` を設定し、サンドボックスのテストをスキップさせない。
- [x] **REL-4 型検査の両 platform 対応**: `mypy --platform linux src scripts` と `mypy --platform darwin src scripts` の両方が通る。`sys.platform` で分岐する処理では、分岐の前で変数の型を宣言する。
- [x] **REL-5 公開前の清掃**: 追跡しているファイルに、開発者のローカルの絶対パス（`/Users/<名前>/...` や `/home/<名前>/...` のうち、例示用の名前でないもの）を含めない。
  - `scripts/run_pilot04_recovery_experiment.py` と `tests/test_pilot04_recovery_e2e.py` のパスは、環境変数で渡す形にし、指定がなければスキップする（テストのスキップの理由を表示する）。
  - 追跡しているファイルを検査し、該当するパスがあれば失敗するテストを置く。例示用の名前（`runner`, `user`, `alice` など）と、テストが一時的に作るパスは除く。
- [x] **REL-2 バージョン表記**: `pyproject.toml`, `plugin.json`, `auto_developer.__version__`, CLI の表示を 0.9.0 に揃える。タグは `v0.9.0` の形式とする（v0.8.0 までの `v.0.x.0` 形式は使わない）。`CHANGELOG.md` に `## [0.9.0]` 節を、`docs/releases/v0.9.0.md` にリリースノートを置く。
- [x] **REL-6 版の表記 0.10.0**: `pyproject.toml`、`plugin.json`、`auto_developer.__version__`、CLI の表示を 0.10.0 にそろえる。`tests/test_version_consistency.py` の期待を 0.10.0 にし、`CHANGELOG.md` の `## [0.10.0]` の節と `docs/releases/v0.10.0.md`（どちらも人間が準備のコミットで書く）があることを確かめる。

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
- [x] **DX-6 `auto-dev demo`**（不具合は DX-8 で修正）: 外部のエージェントやモデルを使わずに、次の流れを1コマンドで見せる。
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
- [x] **DX-8 デモの修正（DX-6 の欠陥）**: 次の4点を直す。
  - デモの Checker は、ファイル名などで独自に拒否せず、差分が空でなければ APPROVE を返す（保護ファイルを止めるのは INV-1 の検知であることを見せる）。
  - 隔離の段階では、失敗ログの `Failure Phase: PROTECTED_FILES`（RCV-5）を確かめ、それ以外の原因で隔離された場合は終了コード 1 とする。
  - Bundle の検証の判定は、`verify-bundle --json` の `status` が `VERIFIED` と完全に一致することで行う。
  - 段階ごとの失敗（隔離の段階で終了コードが 1 でない、隔離ブランチがない、隔離の原因が違う、Bundle が VERIFIED でない）で終了コード 1 になることを、テストで確かめる。
- [x] **DX-9 チェックポイントの記録**: `verify-step --checkpoint` に `HEAD` などの参照名を渡した場合も、Evidence の記録・査読の記録・イベントには、解決したコミットの SHA（40 桁）を記録する。解決できない参照は、これまでどおり拒否する。
- [x] **DX-4 CLI リファレンスの生成**: `docs/cli.md` を、CLI の引数定義（argparse）から生成するスクリプト（`scripts/gen_cli_reference.py`）を置く。生成結果と `docs/cli.md` が一致しない場合に失敗するテストを用意し、CLI の変更でリファレンスが古くならないようにする。README からは `docs/cli.md` に案内する。
- [x] **DX-3 サンプル**: `examples/two-teams/` に、2チーム構成の最小プロジェクトを置く（ad-team-trial を原型とする電卓。`teams.json`, `plans/`, SPEC, 検証スクリプト、テスト）。シナリオ2（cli の例外が外に出る実装）の欠陥は持ち込まない。DX-1 のテストから、このサンプルを一時ディレクトリに写して `teams prepare` まで通る経路を確かめる。
- [x] **DX-1 README のクイックスタートをテストする**: README（`README.md` と `README.en.md`）に書いたコマンドを、一時ディレクトリでそのまま実行するテストを用意し、README と実際の挙動のずれを検査で検出する。
  - 実行の対象は、README の中の `<!-- readme-test: run -->` の直後にあるコードブロックだけとする。対象のブロックは、`auto-dev demo`、新しいリポジトリでの `init`、`teams init` から `teams prepare` まで、`examples/two-teams` を使う手順を含む。
  - 対象のブロックは、ブロックごとに独立して `bash` で実行する。そのとき、`HOME` を新しい一時ディレクトリにし、作業ディレクトリをリポジトリの直下にする（README の手順は `~/` の下に作業用のディレクトリを作る）。Git の作者の名前とメールアドレスは環境変数で与える。実行の後に、リポジトリの作業ツリーが変わっていないことも確かめる。
  - 対象のブロックの中で `pip install` で始まる行は実行せず、テストを動かしている環境に入っている `auto-dev` を使う（ネットワークに依存しない）。
  - 外部のエージェントやモデル、実行の結果などを要する手順のブロックには `<!-- readme-test: skip（理由） -->` を付け、実行しない。README の本文でもそれとわかるように書く。
  - 日本語と英語の README で、実行の対象のブロックの数と、その中のコマンドが同じであることも確かめる。
- [x] **DX-10 2チームのデモ**: `auto-dev demo --teams [--keep]` が、外部のエージェントやモデルを使わずに、一時ディレクトリで次を実行する。終わったら、ダッシュボードのファイルの場所を表示する。
  - 2チーム（各チーム2つ以上のタスク）を `teams run` で並行して実行する。Maker と Checker は、デモ用の決まった動きをする（DX-6 と同じく、デモであることが分かるモデル名と系統を使う）。
  - 一方のチームの1つのタスクで、最初の試行の検証が失敗し、2回目の試行で直る。
  - 統合して RC タグを作り、ダッシュボード（STAT-6）を書く。
  - RC タグが作られ、ダッシュボードが書かれたときだけ終了コード 0。
- [x] **DX-11 ダッシュボードの動画の書き出し**: 開発用のスクリプト `scripts/record_dashboard.py` が、ダッシュボードの HTML を再生して GIF に書き出す（README や紹介の投稿のため）。製品（`auto_developer` のパッケージ）からは呼ばない。必要なライブラリ（ブラウザの操作と画像の処理）がない場合は、入れ方を表示して終了コード 2 とする。製品の実行時の依存は増やさない。
- [x] **DX-12 サンプルの `.gitignore`（DX-3 の欠陥）**: `examples/two-teams/.gitignore` に、CORE-11 で `init` が書く行（`NIGHTLY_REPORT.md` を含む）をすべて入れる。`teams run` の後の統合で、worktree が `NIGHTLY_REPORT.md` で汚れていると判定されないようにする。サンプルの `.gitignore` が CORE-11 の行をすべて含むことを、テストで確かめる。
- [x] **DX-13 検証の制限時間**: 検証スクリプトの制限時間を、次の順で決める。
  - `verify-step` に `--timeout SEC` を加える（サブコマンドの後に書ける）。全体のオプション `auto-dev --timeout SEC verify-step ...` も、これまでどおり使える。両方を指定した場合は、サブコマンドの後のものを使う。
  - どちらも指定しない場合は、検証スクリプトの先頭の 20 行の中の `# auto-dev: verify-timeout=<SEC>` の行の値を使う。その行もない場合は 300 秒とする。値が正の整数でない場合は、終了コード 2 とし、その旨を示す。
  - `run` と `teams run`（ワーカーと統合の関門）も、同じ順で制限時間を決める。
  - 検証が制限時間で打ち切られた場合（終了コード 124）は、今の1行目（`Verification timed out after <N>s`）を変えずに、続けて、使った制限時間の出所（オプション・検証スクリプトの行・既定値）と、`--timeout` と検証スクリプトの行で延ばせることを示す。
  - 検証スクリプトは保護ファイル（INV-1）なので、この行は人間が書く。
  - スキル（`/nightly-run`）に、制限時間の決め方を書く。
- [x] **DX-14 ドキュメントのコマンドの検査**: 開発用のスクリプト `scripts/check_doc_commands.py` が、README（`README.md`、`README.en.md`）とスキル（`skills/*/SKILL.md`）に書かれた `auto-dev` のコマンドを、CLI の引数定義（`build_parser()`）と突き合わせる。製品（`auto_developer` のパッケージ）からは呼ばない。
  - 存在しないサブコマンドと、そのサブコマンド（または全体）にないオプションを、ファイル名と行番号つきで報告し、終了コード 1 とする。なければ 0 とする。
  - 対象にする書き方: コードブロック（`` ``` `` と `~~~`）の中の行（行頭の `$ ` は除く。行末の `\` で続く行は1つにつなげ、最初の行の番号で報告する）と、文中の `` `auto-dev ...` ``。1行を、引用符の外の `&&`・`||`・`;`・`|` で区切り、先頭の `VAR=value` の代入を飛ばして、`auto-dev` で始まる部分を調べる。`<task_id>` のような山かっこの置き換え用の語は、オプションの値として扱う。
  - 対象にしない書き方: `$(...)` などの入れ子、`uv run` や `python -m` などで包んだ呼び出し、ヒアドキュメント。
  - 終了コード 0 のときは、確かめたコマンドの数を示す。対象のファイルが1つもない場合は終了コード 1 とする。
  - トップレベルのサブコマンドのうち、`README.md` に一度も出てこないものを、参考として一覧する（終了コードには影響しない）。
  - テストで、今のリポジトリのドキュメントが通ること、存在しないサブコマンドやオプションを書いた一時ファイルでは終了コード 1 になることを確かめる。
- [x] **DX-15 トップレベルのオプションの省略形**: CLI のトップレベルの `ArgumentParser` は、オプションの省略形（前方一致。例: `--work` を `--work-dir` とみなす）を受け付けない（`allow_abbrev=False`）。トップレベルのオプション（`--checker-cmd` など）と、サブコマンドのオプション（`compile --check` など）が前方一致でぶつからないようにするため。サブコマンドの側の省略形は変えない。

### 4.11 STAT — 状態の表示

- [x] **STAT-1 状態のスナップショット**: `auto-dev status --json` が、次をまとめた1つの JSON を標準出力に書く。読むのは INV-11 の記録だけとする。記録が読めない項目は、推測で埋めず `null` と理由を書く。
  - `schema`（形式の版。`"auto-dev-status/1"`）、作成した時刻、リポジトリの HEAD とブランチ。
  - 計画: 対象の計画ファイル（CORE-10 の読み取り範囲）のタスクと、その状態（未着手・完了・`[FAILED]`）。「止まっていること・判断待ち」の節の項目。
  - 計画で完了（`[x]`）なのに、そのタスクのコミット（`[refs: <id>]`）か Evidence の記録がないものは、食い違いとして `attention[]` に入れる。
  - タスクごとの経過: そのタスクの `verify_step` / `checker_review` / `commit_step` / `rollback_step` のイベント（時刻・結果・判定・モデル）。Evidence の記録があれば、コミットと査読の記録の有無。
  - 隔離ブランチ（`quarantine/*`）の一覧。
  - チームのジョブ（`teams.json` があり、ジョブがある場合）: ジョブの状態、チームごとの worktree・ブランチ・タスクの進み具合・`NO_CHANGES`・隔離、統合の結果（`integration/<job_id>` と `rc/<job_id>` の有無）。worktree の記録（`.dev/evidence/`）も読む。
  - 最新の独立監査の結果（`audit_stage.json` の状態・モード・対象のコミット・指摘の件数）。
  - 人間が判断すべきこと（`attention[]`）: 隔離、`[FAILED]` のタスク、「止まっていること・判断待ち」の項目、`SANDBOX_UNAVAILABLE` などの検証の失敗、Checker の REJECT、独立監査の P0/P1、`AUDIT_OUTDATED` の状態。
  - `--json` を付けない `auto-dev status` は、同じ内容を人が読む形で表示する。
- [x] **STAT-2 ダッシュボード**: `auto-dev dashboard [--out FILE] [--watch [SECONDS]]` が、STAT-1 のスナップショットを埋め込んだ1枚の HTML を書く（既定は `.dev/dashboard.html`）。
  - 標準ライブラリだけで作り、外部のファイル・フォント・スクリプトを読み込まない（オフラインで開ける）。
  - 表示の中心は `attention[]`（人間が判断すべきこと）とする。成功した項目より先に、隔離・失敗・判断待ちを目立たせる。
  - 単一チームでは計画のタスクの並びを、チームのジョブではチームごとの列と統合の段を表示する。各タスクに、検証・査読（判定とモデル）・コミットの状態を示す。
  - main への取り込みは行わない（RC タグまで）ことを、統合の段の表示で正しく示す。存在しない機能を表示しない。
  - `--watch` は、指定した秒数（既定 5 秒）ごとにスナップショットを作り直して HTML を書き直す。HTML は自分で一定の間隔で再読み込みする。サーバーは起動しない。終了は Ctrl-C。
  - スナップショットの `schema` が想定と違う場合は、HTML にその旨を表示する。
  - スナップショットの中の文字列（タスクの題名、理由など）は HTML に埋め込む前にエスケープする。
- [x] **STAT-4 イベントの記録の統一**: 次のイベントを、低レベルのコマンドの経路と同じ形式で書く。イベントの書き込みに失敗しても、結果は変えない（INV-8）。
  - `run` / `teams run` の経路でも、`verify_step`（試行の回数・結果・終了コード）、`checker_review`（判定・モデルと系統）、`commit_step`、`rollback_step` を書く。今は低レベルのコマンドの経路でしか書かれていない。
  - 新しいイベント `task_start`（タスク・試行の回数）: `run` の経路では Maker を起動する直前、低レベルのコマンドの経路では `pre-flight` が次のタスクを返したときに書く。
  - チームの worktree で書くイベントには、チームの ID（`team`）を入れる。
  - 統合のイベント: `integration_start`、`integration_merge`（チーム・コミット）、`integration_reverify`（結果）、`rc_tag`（タグ・コミット）、`integration_failed`（理由）。
- [x] **STAT-5 時系列と backlog**: `status --json` のスナップショットに、次を加え、`schema` を `"auto-dev-status/2"` にする。INV-11 の記録だけから作る。
  - `timeline[]`: STAT-4 のイベントを時刻の順に並べたもの（時刻・種類・チーム・タスク・試行・結果または判定・コミット）。コミットのイベントには、Git から求めた追加と削除の行数を付ける。
  - `backlog`: チームごと（単一チームでは計画ファイル）のタスクの ID と題名と状態。
  - 時系列の始まりと終わりの時刻。複数のジョブの記録がある場合は、最新のジョブ（単一チームでは、最新の計画の対象のタスク）に限る。
- [x] **STAT-6 再生できるダッシュボード**（再生の時刻の割り当ては STAT-9 で変更）: `auto-dev dashboard` が出力する HTML を、人間が承認したデザインの見本 `docs/designs/dashboard-prototype.html` と同じ構成・同じ動きにする。STAT-2 の条件（標準ライブラリだけ、外部の読み込みなし、文字列のエスケープ、`schema` の確認、`attention[]` を最上部に表示）は変えない。画面の文言は英語とする。
  - 構成: 上部の要約（タスク・査読・やり直し・隔離の数）と判断待ちの帯、左の backlog（まだ投入されていないチケット。投入されるたびに減る）、中央の設計図（チームごとのノード、Maker と Checker の表示、試行の回数、状態）、統合の関門（`main` は変更されず、取り込むのは人間であることを常に表示）、右側のコミットの積み上がり（行数）と出来事の記録（出所つき）、下部の時系列の帯（チームごとの列と統合の列）と再生の操作（再生・一時停止・1×/4×/60×・シークバー、帯のクリックで移動）。
  - 単一チームでは、チームのノードが1つの形にする（ブランチ名を表示）。
  - 再生は、時系列の記録の時刻どおりに行う。10 分を超える空白は、再生の時間を詰め、帯の上に印を付ける。
  - 時系列が空の場合（記録がまだない場合）も、backlog と判断待ちを表示する。
- [x] **STAT-7 ダッシュボードの自動更新**: 監視のプロセスを別に動かさずに、記録が書かれるたびにダッシュボードを書き直せるようにする。
  - `auto-dev dashboard --enable [--out FILE]` で、リポジトリごとの設定（`.dev/dashboard.json`）に有効と出力先を保存し、`--disable` で無効にする。有効なあいだは、STAT-4 のイベントを書くたびに、HTML を作り直す。チームの worktree でイベントを書いた場合も、元のリポジトリのダッシュボードを作り直す。
  - `run` / `teams run` に `--dashboard` を加える（その実行のあいだだけ有効）。
  - 自動で作り直した HTML は、ブラウザで開いたまま一定の間隔で再読み込みされ、最新の状態（時系列の終わり）を表示する（`LIVE` の表示）。
  - ダッシュボードの作成に失敗しても、検証・査読・コミット・隔離の結果は変えない（INV-8 と同じ）。失敗したことは、利用者が実行した1つのコマンドにつき1回だけ警告として表示する。`teams run` で親のプロセスの最初の書き込みに失敗した場合は、警告を1回出し、その実行ではダッシュボードを無効にする（ワーカーには有効にする指定を渡さない）。
  - スキル: `/nightly-run` に、ダッシュボードを有効にする選択肢を加える（開始時に `auto-dev dashboard --enable` を実行し、開くファイルの場所をユーザーに伝える）。`/nightly-setup` でも、この選択肢を案内する。
  - `--watch`（STAT-2）は残す。
- [x] **STAT-8 状態の表示の修正（STAT-1 の欠陥）**: 次の4点を直す。
  - 独立監査の記録は、Evidence の記録と同じ場所（`resolve_evidence_records_dir`）の `audit_stage.json` から読む。`AUDIT_OUTDATED` の判定は、`verify-bundle` と同じ関数（`check_audit_diff_status`）で行う。結果が `OK` 以外なら、すべて `attention[]` に入れ、その関数が返す理由を示す（監査の後に実装が変わった場合のほか、記録の対象のコミットが不正な場合、差分が取れない場合を含む。`verify-bundle` も、`OK` 以外では監査を検証済みにしない）。
  - 「止まっていること・判断待ち」の節の項目のうち、中身が「なし」「なし。」「None」「-」だけのものは、判断待ちとして数えない。
  - 同じ隔離ブランチを、`attention[]` に2回入れない。
  - チームが失敗した場合は、失敗したタスクの数と、原因（ワーカーのエラー、または失敗ログの `Failure Phase:` / `Reason:`）を示す。
- [x] **STAT-9 再生の最小の間隔**: STAT-6 の再生の時刻の割り当てを、次のように変える。
  - 時系列のイベントを時刻の順に並べ、続く2つのイベントの再生の時刻の差を、最低 1 秒（1× のとき）とする。実際の時刻の差がそれより短い場合だけ広げる。10 分を超える空白の圧縮（STAT-6）は変えない。
  - 同じ時刻のイベントは、それぞれの記録ファイルの中の順番を保つ（チームの worktree の記録と、リポジトリの記録をまとめる場合も）。
  - 再生の時刻の割り当ては、HTML に埋め込むデータ（payload）を作る Python 側で行う。テストは payload の値で確かめる。
  - `auto-dev demo --teams` が書くダッシュボードの再生の長さ（`duration`）は、時系列のイベントの数から 1 を引いた秒数以上になる（今は約1秒で、GIF に書き出しても3コマにしかならない）。
- [x] **STAT-10 ダッシュボードの細部**: 次の2点を直す。
  - 時系列の帯の上の「10 分を超える空白」の印の文字が、隣の印の文字と重ならないようにする。重なる場合は文字を出さず、印にカーソルを合わせたときに表示する。
  - `attention[]` の隔離ブランチの項目で、今の時系列の中の隔離（`rollback_step` のイベントに記録された隔離ブランチ）と、それより前の隔離を区別して示す。上部の要約の隔離の数が、今の時系列の中の隔離の数であることを、カーソルを合わせたときの説明で示す。
  - 見た目（文字の重なり）は人間が目で確かめる。テストでは、payload と HTML に、印の文字・説明・区別の情報が入っていることを確かめる。JavaScript を実行するテストは求めない。
- [x] **STAT-11 要件のスナップショット**: `status --json` のスナップショットに、次を加える。INV-11 の記録と、承認した IR（`docs/designs/*.ir.json`）だけから作る。`schema` は変えない（項目を足すだけ）。
  - `requirements[]`: 計画ファイルのタスクの題名の最後の括弧の中にある要件の ID（`[A-Z]+-[0-9]+` の形。全角と半角の括弧の両方）と、承認した IR の要件を合わせたもの。各要件に、`id`、`statement`（IR にあれば。なければ `null`）、`source`（IR にあれば）、その要件を参照するタスク（チームのジョブではチームの ID とともに）、状態（参照するタスクがすべて完了なら `done`、一部が完了なら `in_progress`、ほかは `pending`）。
  - `design_irs[]`: 承認した IR のファイルごとに、パス、ファイルの SHA-256、元の場所の検査（CMP-1）の結果（`ok` か `drift`。`drift` なら一致しない要素の一覧）。
  - 承認した IR の元の場所が設計書と一致しない場合は、`attention[]` に `DESIGN_DRIFT` として入れる（設計書が承認の後に変わったことを示す）。
  - IR が読めない場合は、推測で埋めず、その旨と理由を示す（STAT-1 と同じ）。
- [x] **STAT-3 計画の構造の表示**: ダッシュボード（STAT-6）に、要件の構造を重ねる。見本 `docs/designs/dashboard-prototype.html` の構成と動きは変えない。
  - backlog のチケットと、チームのノードの作業中のタスクに、要件の ID の小さな印を付ける。印にカーソルを合わせると、要件の文と元の場所（`<path>:L<開始>-L<終了>`）を示す（IR がない要件は ID だけ）。
  - 上部の要約に、要件の数（完了／全体）を加える。再生の時刻に合わせて、完了の数が増える。
  - `DESIGN_DRIFT` は、ほかの `attention[]` の項目と同じく、上部の判断待ちの帯に出す。
  - 見た目は人間が目で確かめる。テストでは、payload と HTML に、要件の ID・文・元の場所・数が入っていることを確かめる。JavaScript を実行するテストは求めない。
- [x] **STAT-12 同じ秒のイベントの順番**: STAT-4 のイベントに、ミリ秒の時刻（`ts_ms`。整数）を加える（`ts` は変えない）。
  - 時系列（STAT-5）とダッシュボードの再生（STAT-6・STAT-9）は、`ts_ms` があればそれで並べ、なければ今までどおり（`ts` と、記録ファイルの中の順番）で並べる。
  - `auto-dev demo --teams` のダッシュボードで、統合のイベント（`integration_start` など）が、すべてのチームの最後の `commit_step` より後に並ぶこと。統合の列の帯が、`integration_start` より前から始まらないこと。

### 4.12 CMP — Spec Compiler

- [x] **CMP-1 中間表現（IR）と形式の検査**: `auto-dev compile --check <IR の JSON> [--json]` を加える。IR は、設計書を Auto Developer の SPEC と PLAN に移すための中間の形で、形式は次のとおり（`schema` は `"auto-dev-ir/1"`）。
  - 最上位: `schema`（必須）、`source`（必須。1件以上。`{path, sha256}`。`sha256` はファイル全体のバイト列）、`purpose`、`terms[]`、`requirements[]`（必須。1件以上）、`acceptance[]`、`out_of_scope[]`、`decisions[]`、`steps[]`、`teams[]`、`verification`、`alternatives[]`、`open_questions[]`。
  - `requirements[]`: `id`（`^[A-Z]+-[0-9]+$`。IR の中で重ならない）、`statement`（空でない文字列）、`kind`（`"new"` または `"modify"`）、`acceptance`（`acceptance[]` の `id` の一覧）、`source`（元の場所）、`fingerprints`（任意。文字列の一覧）。
  - `acceptance[]`: `id`（`^AC-[0-9]+$`。重ならない）、`given`・`when`・`then`（文字列）、`observable`（観測できる結果。文字列の一覧）、`source`。
  - `decisions[]`: `id`（`^D-[0-9]+$`。重ならない）、`statement`、`forbids`（任意。この判断が禁じる操作や書き方を表す語句の一覧）、`source`。
  - `steps[]`: `n`（1 から始まる連番）、`title`、`requirements`（要件の `id` の一覧）、`files`（パスの一覧）。`teams[]`: `id`（重ならない）、`owns`（`teams.json` と同じ書き方の一覧）。`verification`: `commands`（文字列の一覧）、`source`。
  - 元の場所（`source`）: `{path, lines: [開始, 終了], sha256}`。行は 1 から数え、終了を含む。`sha256` は、そのファイルの開始行から終了行までのバイト列（各行の改行を含む）の SHA-256。`path` は、リポジトリの直下からの相対パスとする。`purpose`・`terms[]`・`out_of_scope[]`・`alternatives[]` の各要素も、`source` を持てる。
  - 検査すること（どれも ERROR）: JSON として読めること、`schema` の値、必須の項目と型、`id` の形と重なり、参照（要件の `acceptance`、ステップの `requirements`）が実在すること、ステップの番号が連番であること。
  - 元の場所の検査（ERROR）: `path` のファイルがあること、行の範囲がファイルの中にあること、`sha256` がその範囲と一致すること（設計書が後で変わったら検出する）。最上位の `source` の `sha256` がファイル全体と一致しない場合は WARN とする（範囲ごとの一致を優先する）。
  - 終了コード: ERROR がなければ 0（WARN だけなら 0）、ERROR があれば 1、IR のファイルが読めない・JSON でない・`schema` が `"auto-dev-ir/1"` でない場合は 2。結果は、1行に1件、`ERROR|WARN <点検の ID> <IR の中の場所>: <説明>` の形で示す。`--json` では `{"schema": "auto-dev-compile-check/1", "errors": [...], "warnings": [...]}` を出す（各件は `rule`・`location`・`message`）。形式の検査の点検の ID は `F-1` とする。
  - INV-12: `compile` は、どのファイルも作成・変更しない（標準出力だけ）。
- [x] **CMP-2 設計書の見出しの読み取り**: `auto-dev compile --outline <設計書の markdown> [--json]` を加える。設計書の見出しを、IR の要素に対応づけて示す。LLM が IR の下書きを作るときの足場と、元の場所の行番号の基準にする。
  - 対応（見出しの文字列の先頭で判定する。大文字と小文字は区別しない。先頭の番号（`1.` など）は除いて比べる）: `Overview` と `The Problem` → `purpose`、`Glossary` または `用語定義` → `terms`、`Goals` → `requirements`、`Non-Goals` → `out_of_scope`、`Architectural Decisions` → `decisions`、`Critical User Journeys` または `CUJ` → `acceptance`、`Technical Plan` と `Detailed Implementation Plan` → `steps`、`Verification Plan` → `verification`、`Alternatives Considered` → `alternatives`。見出しの後ろの括弧書き（`(Resolved Questions)` など）は無視する。
  - コードブロック（`` ``` `` と `~~~` の囲み）の中の `#` で始まる行は、見出しとして扱わない。
  - 各見出しについて、レベル、行の範囲（その見出しの行から、同じかより上のレベルの次の見出しの前の行まで）、範囲の SHA-256（CMP-1 と同じ求め方）、対応する IR の要素（なければ `null`）を示す。
  - `Goals`、`Critical User Journeys`、`Verification Plan` と、`Technical Plan` か `Detailed Implementation Plan` のどちらか、が見つからない場合は、それを「足りない節」として示し、終了コード 1 とする。そろっていれば 0。ファイルが読めない場合は 2。
  - `--json` では `{"schema": "auto-dev-outline/1", "source": {path, sha256}, "sections": [...], "missing": [...]}` を出す。INV-12: どのファイルも作成・変更しない。
- [x] **CMP-3 決定的な点検**: `compile --check` に、次の点検を加える。CLI で確信できるものだけを判定し、確信できない場合を ERROR にしない（意味の判断は、Phase 76 のスキルの査読に回す）。
  - **L-1（ERROR）**: どの要件にも、`acceptance` が1件以上あり、参照する受入条件のどれにも `observable` が1件以上ある。
  - **L-5（ERROR）**: 判断の `forbids` の語句が、要件の `statement` か、受入条件の `given`・`when`・`then` に、そのまま現れる場合は、判断と要件の食い違いとして、両方の場所を示す（大文字と小文字は区別する）。
  - **L-6（WARN）**: `--spec <SPEC.md>`（省略時は作業ディレクトリの `SPEC.md`。なければこの点検は行わない）の 4 章に同じ ID の要件があるのに `kind` が `"modify"` でない場合、または `kind` が `"modify"` なのに 4 章にその ID がない場合。
  - **L-7（ERROR）**: `teams[]` が1件以上ある場合に、2つのチームの `owns` が同じステップのファイルに当てはまる（`teams.json` の担当範囲の検査と同じ当てはめ方）、または、どのチームの `owns` にも当てはまらないステップのファイルがある。
  - **L-8（WARN）**: どの要件にもつながらないステップ、どのステップにもつながらない要件。
  - **L-9（ERROR）**: `open_questions[]` が空でない、または、要件の `statement`・受入条件の各項目・ステップの `title` に、未決の目印（`TBD`、`TODO`、`要検討`、`未定`）が含まれる。
- [x] **CMP-8 実物の設計書での確認**: `docs/designs/autonomous_pipeline_orchestrator.md` を入力にしたテストを置く。
  - `compile --outline` が、Overview・Glossary（`用語定義`）・The Problem・Goals・Non-Goals・Architectural Decisions・CUJs・Technical Plan・Alternatives Considered・Detailed Implementation Plan・Verification Plan を対応づけ、`NIGHTLY_REPORT.md` のテンプレートのコードブロックの中の `#` の行を見出しとして扱わないこと。足りない節がなく、終了コード 0 になること。
  - この設計書から作った IR をテストの資料（`tests/fixtures/compile/` の下）として置く。元の場所は、設計書の実際の行と SHA-256 を指すこと。
  - その IR に対して `compile --check` が、Decision 4（隔離ブランチへの退避。`git reset --hard` と `git clean -fd` による破壊的なロールバックを廃止する）と、Goals の Fail-Fast の要件・CUJ 2 の受入条件（どちらも `git reset --hard` / `git clean -fd` を使うと書いている）の食い違いを、L-5 の ERROR として報告し、終了コード 1 になること。
  - 食い違いを直した IR（同じ資料の別のファイル）では、終了コード 0 になること。
- [x] **CMP-5 下書きの出力**: `auto-dev compile --emit <IR> [--out-dir DIR] [--lang ja|en] [--force]` を加える。IR から、人間が SPEC・PLAN・`teams.json`・契約書に写すための下書きを作る。
  - **前提の点検**: 書き出す前に、`--check` と同じ点検（`--spec` の扱いも同じ）を行う。ERROR が1件でもあれば、何も書かずに点検の結果を示し、終了コード 1 とする。IR が読めない場合などは、`--check` と同じく終了コード 2 とする。
  - **出力先（INV-12）**: 既定は `<作業ディレクトリ>/.dev/compile/<IR のファイル名から拡張子を除いたもの>/`。`--out-dir` は、`<作業ディレクトリ>/.dev/compile/` の下（シンボリックリンクを解決した後のパスで判定する）でなければ、何も書かずに終了コード 2 とする。出力先にファイルがある場合は、`--force` がなければ何も書かずに終了コード 2 とする。`--force` のときは、このコマンドが作る名前のファイルだけを書き直し、ほかのファイルは消さない。出力先の外のファイル（SPEC.md・PLAN.md・`teams.json`・保護ファイルを含む）は、作成・変更・削除しない。
  - **決定的な出力**: 同じ IR と同じオプションからは、同じバイト列を出す。時刻など、実行ごとに変わる値を入れない。
  - **共通の見出し**: Markdown のファイルの先頭に、「Spec Compiler の下書きであること。人間が読んで承認し、人間が SPEC.md・PLAN.md に写してコミットすること（INV-12）。元の IR のファイル名と SHA-256」を書く（HTML のコメントではなく、読める文として）。
  - **`--lang`**: 下書きの見出しや決まった文言の言語（既定は `ja`）。IR の中の文（要件の文など）は、IR のとおりに写し、翻訳しない。
  - 書き出したファイルの一覧を表示し、終了コード 0 とする。
  - 書き出すファイル: `ir.json`（入力の IR と同じバイト列）、`check.json`（`--check --json` と同じ内容）、`spec_section.md`、`plan.md`、`contract.md`、`acceptance_tests.md`。IR の `teams[]` が1件以上ある場合は、`teams.json` と `plans/<チームの id>.md` も。各ファイルの中身は、CMP-9〜CMP-12 による。
- [x] **CMP-9 SPEC の節の下書き**（`spec_section.md`）:
  - `### 5.1 （フェーズの名前）` の見出しの下に、要件を IR の順に `- [ ] **<id>**: <statement>` の形で並べる。`kind` が `"modify"` の要件は、`id` の後に「（修正）」（`en` では `(modify)`）を付ける。
  - 各要件の下に、参照する受入条件を1件ずつ、Given・When・Then・観測できる結果（`observable`）とともに字下げして書く。
  - 各要件の直後に、元の場所を `<!-- source: <path>:L<開始>-L<終了> sha256:<SHA-256> -->` の1行で書く（追跡のため。CMP-7 の一部）。
  - `out_of_scope[]` があれば、「スコープ外（SPEC の 6 章に写す）」の見出しの下に並べる。
- [x] **CMP-10 PLAN の下書き**（`plan.md`、`teams.json`、`plans/<id>.md`）:
  - `## ステップ` の見出しの下に、ステップを `- [ ] <n>. <title>（<要件の id をカンマで区切る>）` の形で並べる。各ステップの下に、対象のファイルを字下げして書く。この形は、既存の計画ファイルの読み取り（`- [ ] <番号>.`）で読めること。
  - `## 既存テスト変更の理由` の見出しの下に、ステップごとに「（影響分析の後に、人間が書く。既定は「既存テストは変更しない」）」の行と、決まった3行（「このフェーズで前のステップが作ったテストの資料は、後のステップの検査に合わせて直してよい（アサーションは変えない）」「免疫テスト（`tests/immunity/**`）は変更しない」「CLI を変えたステップでは `docs/cli.md` を作り直す」）を書く。
  - `teams[]` がある場合: `teams.json` は、既存の `teams.json` と同じ形（`version` は 1、`project` は IR のファイル名から拡張子を除いたもの、各チームの `id`・`owns`・`plan`（`plans/<id>.md`）・`depends_on`（空））で、既存の `teams.json` の読み込み（`load_teams_manifest`）で読めること。`plans/<id>.md` には、ステップの対象のファイルがすべてそのチームの `owns` に当てはまる（L-7 と同じ当てはめ方）ステップだけを、上の形で並べる。複数のチームにまたがるステップは、どのチームの計画にも入れず、契約書の「人間が判断すること」に挙げる。
- [x] **CMP-11 契約書の下書き**（`contract.md`）: 次の節をこの順に置く。
  - 目的（`purpose` の文と元の場所）。
  - 人間が判断すること: `--check` の WARN のすべて、複数のチームにまたがるステップ、「影響分析（既存のテストとドキュメントへの影響）は、Phase 77 で自動化するまで人間が書く」の1行。WARN がなくても、この節は省かない。
  - 要件の一覧: ID・種別（新規／修正）・要件の文・受入条件の ID・元の場所の表。
  - 判断（`decisions[]`）: 判断の文、禁じる語句（`forbids`）、元の場所。不変条件の候補として示す。
  - 検討した代案（`alternatives[]`）。
  - 元の文書: IR の `source[]` のパスと SHA-256。
- [x] **CMP-12 受入テストの案**（`acceptance_tests.md`）: 受入条件ごとに、ID を見出しにして、Given・When・Then・観測できる結果、その受入条件を参照する要件の ID、元の場所を書く。テストのコードは書かない。
- [x] **CMP-6 スキル `/nightly-compile`**: `skills/nightly-compile/SKILL.md` を置き、`plugin.json` の `suggestedPrompts` に `/nightly-compile` を加える。スキルは、エージェントに次の流れを指示する。
  - **入力**: EGM の設計書（`docs/designs/*.md`）。または文書群（`goal.md`、`architecture.md`、`decision.md`、`todo.md` など）。文書群の場合は、v0.10-draft の 5.2 の対応で1つの設計書の形に組み立てて `.dev/compile/<name>.design.md` に置き、足りない節（受入条件にあたる CUJ、Verification Plan など）は推測で埋めず、人間に聞く。
  - **見出しの確認**: `auto-dev compile --outline <設計書>` を実行する。終了コード 1（足りない節がある）なら、止まって人間に聞く。
  - **IR の下書き**: `.dev/compile/<name>.ir.json` に、IR（`auto-dev-ir/1`）を書く。要件・受入条件・判断の文は、設計書の文に沿って書き、要約で意味を変えない。すべての要素に元の場所を付け、行の範囲は `--outline` の出力を基準にする。判断が禁じる操作は `forbids` に書く。はっきりしない点は推測で埋めず、`open_questions` に書く。
  - **点検**: `auto-dev compile --check` を、ERROR がなくなるまで実行して直す。要件や元の場所を消して ERROR を消さない。`open_questions` による ERROR（L-9）は、人間に聞いて解消する。
  - **独立した査読**: Maker（IR を書いたモデル）と別の系統のモデルに、設計書・IR・点検の結果だけを渡して査読させる。確かめること: 設計書の Goals・CUJ・Decisions がすべて IR にあること、要件の文が設計書の意味を変えていないこと、「A または B」の場合分け（L-2）、同じ対象への「行う」と「行わない」（L-3）、定義のない用語（L-4）。査読の出力は `.dev/compile/<name>.review.txt` に置き、最後に `DECISION: APPROVE` か `DECISION: REJECT` を書かせる。REJECT なら IR を直して点検からやり直す。REJECT が2回続いたら止まって人間に渡す。文書群から組み立てた場合は、この査読を省かない。
  - **書き出しと引き渡し**: `auto-dev compile --emit` を実行し、`contract.md` の場所と、「人間が判断すること」の項目を人間に伝える。SPEC.md・PLAN.md・`teams.json` は書き換えない（INV-12）。写してコミットするのは人間である。
  - スキルに書いた `auto-dev` のコマンドは、`scripts/check_doc_commands.py`（DX-14）を通ること。
- [x] **CMP-13 元の場所の補助と出力先の名前**: 次の3点を変える。
  - `auto-dev compile --anchor <path>:<開始>-<終了> [--json]` を加える。CMP-1 の元の場所の形（`{path, lines, sha256}`。SHA-256 の求め方も CMP-1 と同じ）を1件出す。ファイルがない、行の範囲がファイルの外、開始が終了より後、形が違う場合は、終了コード 2 とする。どのファイルも作成・変更しない（INV-12）。
  - `compile --emit` の既定の出力先と、`teams.json` の下書きの `project` は、IR のファイル名から `.json` を除き、さらに末尾が `.ir` なら、それも除いたものとする（`calc.ir.json` → `calc`）。
  - スキル `/nightly-compile`: 要件ごとの元の場所は `compile --anchor` で求めると書く。出力先の説明を上の規則に合わせる。要件の `fingerprints` には、その要件が変える関数・クラス・コマンド・オプションの名前や、判定の目印の文字列（例: `SANDBOX_UNAVAILABLE`）を書くと書く（影響分析 CMP-4 の検索の語になる）。このリポジトリの中の文書（v0.10-draft）への参照を外す（対応の表はスキルに書いたまま残す）。
- [x] **CMP-4 影響分析**: `auto-dev compile --impact <IR> [--json]` を加える。IR の要件が、作業ディレクトリのリポジトリのどこに影響しうるかを、決定的に洗い出す。判断はせず、候補を並べる。どのファイルも作成・変更しない（INV-12）。
  - 対象は、Git が追跡しているファイル（`git ls-files`）とする。`.dev/` の下は除く。
  - **I-1 指紋の検索**: 要件の `fingerprints` の各語を探す。Python のファイルは構文木で調べ、定義（関数・クラスの名前）、参照（名前・属性）、文字列の定数（語を含むもの）を区別する。それ以外の文字のファイル（`.md`、`.sh`、`.json`、`.toml`、`.yml` など）は、行の中の文字列の一致で調べる。テストのファイル（`tests/` の下、または `test_*.py`・`*_test.py`）とそれ以外を分けて、ファイルと行を示す。
  - **I-2 モックと呼び出しの回数**: テストの関数のうち、指紋の語を参照し、かつ、モック（`patch`、`patch.object`、`Mock`、`MagicMock`）か呼び出しの回数の確認（`assert_called*`、`call_count`）を使っているものを示す。
  - **I-3 記録を合成しているテスト**: 記録のファイルの名前（`events.jsonl`、`nightly_evidence`、`job_state.json`、`audit_stage.json`、`bundle_manifest.json`）を文字列の定数に含み、ファイルに書き込んでいる（`write_text`、`write_bytes`、書き込みの `open`、`json.dump`）テストのファイルを示す。要件ごとではなく、IR 全体に対して1回示す。
  - **I-4 入口**: README（`README*.md`）、スキル（`skills/*/SKILL.md`）、`docs/` の直下の Markdown、`if __name__ == "__main__"` を持つスクリプトのうち、指紋の語が出てくるものを示す。
  - `fingerprints` のない要件は、「指紋がないので調べていない」と示す。
  - 結果は、要件の ID の順、ファイルのパスの順、行の順に並べ、同じ入力からは同じ出力を出す。
  - 終了コード: 調べられた場合は 0（候補があってもなくても）。IR が読めない場合は 2。IR の形式の検査（CMP-1 の `F-1`）に ERROR がある場合は、調べずに 1。
  - `--json` では `{"schema": "auto-dev-impact/1", "requirements": [...], "record_tests": [...]}` を出す。
- [x] **CMP-14 下書きへの影響分析の反映**: `compile --emit` は、CMP-4 の影響分析も行い、次のように下書きに反映する。
  - 出力先に `impact.json`（`--impact --json` と同じ内容）を書く。
  - `contract.md` に「影響分析」の節を置き、要件ごとに I-1・I-2・I-4 の候補を、IR 全体の I-3 の候補とともに示す。「人間が判断すること」の「影響分析は人間が書く」の1行は、「影響分析の候補を確かめ、既存テストを変えるかどうかを決める」に変える。
  - `plan.md` の「既存テスト変更の理由」の各ステップの行の下に、そのステップの要件の I-1・I-2 で見つかったテストのファイルを「影響の候補」として並べる。候補がなければ「影響の候補なし」と書く。
- [x] **CMP-15 影響分析の細部**: `compile --impact`（CMP-4）を、次のように直す。
  - `docs/` の下のファイルは、名前が `test_*.py` でも、テストではなく文書として扱う。
  - `--json` の各要件に、I-2 の `mock_tests` を、候補がなくても空の一覧として必ず出す。

## 5. 次のリリース（v0.11.0）で追加する要件

<!-- v0.11.0 の方向は docs/contracts/v0.11-draft.md。テーマの優先順位は G（柔らかい規則を硬い関門に）→ T（チームの専門化）→ L（直した経験を規則に）→ E（Checker の質）→ S（スキル集）と決めた（2026-11-27）。
     Phase 80 は G の最初: Red-First をツールが確かめる（GATE-16）。あわせて、Phase 79 の後の監査の指摘（AUD-1006・AUD-1007）を直す。影響分析と設計の判断は docs/contracts/phase80.md を参照。 -->

### 5.1 Phase 80 — 監査の分割の修正と、Red-First の確認

- [ ] **AUDIT-8 分割した監査の欠落を防ぐ（AUD-1006）**: AUDIT-7 の分割で、次の2点を直す。
  - 範囲の中のファイルの一覧は、ファイル名が引用符で囲まれない形（`-z` または `core.quotePath=false`）で読む。日本語などを含むファイル名も、組の差分に入るようにする。
  - **欠落の検査**: 組に分けた後、すべての組の差分をつなげたものと、範囲で絞った差分の全体が、ファイルごとに一致することを確かめる（どのファイルも、ちょうど1つの組に、同じ内容で入っていること）。一致しない場合は、監査役を呼ばずに `AUDIT_PENDING` とし、欠けた・食い違ったファイルを示す（監査の入力から黙って変更が抜けることを防ぐ）。
- [ ] **AUDIT-9 失敗した監査の記録（AUD-1007、IDEA-037）**: 監査が `AUDIT_PENDING` や失敗で終わった場合も、記録（`audit_stage.json`）に、使った範囲・差分のファイルの数とバイト数・組の全体の数・実行した組と実行しなかった組を残す。報告の「監査の深さ」は、「3 組のうち 1 組目で失敗（2・3 組目は実行していない）」のように、全体の数と実行の状況を示す。
- [ ] **GATE-16 Red-First の確認の設定と対象**: `verify-step` が、そのステップの「先に失敗するテストを書く」（Red-First）を、ツールで確かめられるようにする。このステップでは、設定と対象の決め方と記録までを行い、テストの実行は GATE-17 で行う。
  - **設定**: 検証スクリプトの先頭の 20 行の中の `# auto-dev: red-first=<off|warn|enforce>` の行で決める（DX-13 と同じく、保護ファイルなので人間が書く）。行がなければ `warn` とする。値が3つのどれでもなければ、終了コード 2 とする。
  - **対象のタスク**: 計画ファイルのタスクの題名に `Red-First` または `Red:` を含むタスクだけを対象にする（題名で Red-First を宣言したタスク）。対象でないタスクは、発行する Evidence の記録に `SKIPPED`（対象外）と残すだけにし、イベントは書かない。
  - **対象のテストのファイル**: チェックポイントから今の作業ツリーまでに、追加または変更された Python のテストのファイル（`tests/` の下の `test_*.py`、または `test_*.py`・`*_test.py`。`tests/immunity/` を除く）。
  - **記録**: 対象のタスクの結果（この時点では `NO_TEST_CHANGES`、または GATE-17 の結果）を、イベント `red_first`（タスク・チェックポイント・設定・結果・対象のファイル）と、発行する Evidence の記録（`red_first`）に残す。イベントの書き込みに失敗しても、結果は変えない（INV-8）。
  - 設定が `off` のときは、何も調べず、`SKIPPED` も記録しない。
- [ ] **GATE-17 Red-First の確認の実行**: GATE-16 の対象のタスクで、対象のテストのファイルが1つ以上ある場合は、検証スクリプトの前に、それぞれのテストのファイルを、**チェックポイントの時点のコード**に対して、候補テストの隔離実行の仕組み（SBX。監査の再現テストと同じサンドボックス）で実行する。
  - 結果の分け方: どれか1つのファイルで、テストが失敗する（アサーションの失敗、例外、まだない機能の import の失敗）なら `CONFIRMED`。すべてのファイルが通るなら `NOT_RED`（チェックポイントの時点でも通るテストは、そのステップの変更を確かめていない）。構文の誤りだけで失敗する、または自明な Red（`assert False` などだけのテスト。既存の判定 `TRIVIAL_RED` を使う）なら `NOT_RED`。サンドボックスが使えない場合は `SANDBOX_UNAVAILABLE`。
  - 1つのファイルの実行の制限時間は 120 秒とする。打ち切られた場合は、そのファイルを `TIMEOUT` として扱い、`CONFIRMED` には数えない。
  - **`warn` のとき**: 結果が `CONFIRMED` 以外なら、警告（`RED_FIRST_WARNING:` で始まり、結果とファイルを示す）を出し、検証はそのまま続ける。
  - **`enforce` のとき**: 結果が `CONFIRMED` 以外（`NO_TEST_CHANGES`・`NOT_RED`・`SANDBOX_UNAVAILABLE`・`TIMEOUT` を含む）なら、検証スクリプトを実行せず、Evidence も発行せず、`RED_FIRST_NOT_CONFIRMED:` で始まるメッセージを出して終了コード 1 とする。これは検証の失敗として数える（GATE-15 の INV-4 の数え方の対象）。
  - `review-input`（GATE-5）は、出力先に `red_first.json`（設定・結果・ファイルごとの結果）を書く。スキル（`/nightly-run`）に、Checker にこのファイルも渡し、`NOT_RED` などのときは、テストがそのステップの変更を本当に確かめているかを重点的に見るよう伝えることを書く。
  - `run` / `teams run` の経路の扱いは、このフェーズでは変えない（低レベルのコマンドの経路だけ）。

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
