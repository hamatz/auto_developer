# 契約書: Phase 71（v0.9.0 の最後のフェーズ）

<!--
SPEC.md・PLAN.md を確定する前に、人間が確認するための資料です。
作成: 2026-11-23（Phase 70 完了時点 898fe57 に対する分析）
範囲の決定（人間が承認、2026-11-23）: Phase 70 の後に CI で見つかった macOS と Linux の失敗を最初に直す。
verify-step が本物のインデックスを変える件（GATE-9）を加える。そのうえで README・サンプル・CLI リファレンス・リリースを行う。
-->

## 1. このフェーズの目的

**v0.9.0 を、初めての利用者が macOS と Linux のどちらでもつまずかずに試せる状態にして、リリースする。**

### 1.1 Phase 70 の後の CI で見つかったこと

Phase 70 の準備で、`scripts/verify_local.sh` がテストを動かす Python を「`/usr/bin/python3` を優先」から「環境変数 `PYTHON` → PATH の `python3`」に変えた。これで CI のマトリクス（macOS / Ubuntu × Python 3.10 / 3.12）の Python が、初めて実際に使われた。それまでは、macOS では Xcode の Command Line Tools の Python 3.9、Ubuntu では OS の Python 3.12 で走っていた。

- **macOS（失敗 72 件・エラー 9 件）**: 候補テストの起動が、すべて `python3: realpath: /Library/Frameworks/Python.framework/Versions/3.12/bin/: Operation not permitted` → `SANDBOX_UNAVAILABLE` で止まる。setup-python の Python は、ホームの中の `hostedtoolcache` から `/Library/Frameworks/Python.framework` へのシンボリックリンクである。サンドボックスの設定（`verifier.py` 1878〜1900 行目付近）は、リンクの先のディレクトリの中身を許可しているが、Python が起動時に自分の場所をたどる途中のパス（ホームの中のリンク、`/Library`、`/Library/Frameworks`）の読み取りを許可していない。**python.org のインストーラの Python を使う Mac の利用者も、同じ理由で動かない**（SBX-7）。
- **Ubuntu（失敗 1 件）**: `test_linux_bwrap_isolation.py` の `test_bwrap_blocks_reading_unallowed_host_paths` が、サンドボックスの中で `os.listdir("/opt")` が空であることを期待している。CI の Python は `/opt/hostedtoolcache/...` にあり、サンドボックスは Python の置き場所だけを読み取り専用でマウントする（`verifier.py` 2016 行目付近）ので、`/opt` の中に `hostedtoolcache` だけが見える。製品の挙動は正しく、テストの前提が誤っている（SBX-8）。Phase 70 の SBX-6 で、Ubuntu の失敗は 29 件から 1 件になった。

### 1.2 Phase 70 の確認で見つかったこと

- `verify-step` が作業ツリーの SHA を求めるとき（`guard.py` の `get_working_tree_sha`）に、本物のインデックスへ `git add -A` する。一時リポジトリで試すと、gitignore していない `.env` までステージされた。利用者があとで手で `git commit` すると、機密がコミットされる。Phase 70 では、承認の記録のコミットに実装が混ざった（`f51e971`、人間が作り直した）（GATE-9）。
- Phase 70 のタスク 8 以降の記録に、チェックポイントとしてコミットの SHA ではなく `HEAD` という文字列が残っている（DX-9）。
- 追跡しているファイルに、開発者のローカルの絶対パスがある（`scripts/run_pilot04_recovery_experiment.py`、`tests/test_pilot04_recovery_e2e.py`）（REL-5）。
- Phase 70 の Bundle は `verify-bundle` で INVALID になる。タスク 3 の Evidence の SPEC の SHA が、フェーズの途中の SPEC の変更（265e605、c30b6cf）より前のものだからである。タスク 1・2 は設計どおり `NO_REVIEW_EVIDENCE` として除外された。後から直す手段はなく、v0.9.0 の歴史の断絶として受け入れる。リリースの判定には Phase 71 の Bundle を使う（BACKLOG FRICTION-005 に追記）。

## 2. 要件の一覧

| ステップ | ID | 内容 | 種別 |
| :--- | :--- | :--- | :--- |
| 1 | SBX-7 | macOS で、シンボリックリンクの Python でも候補テストを起動できる | 修正（CI の macOS・python.org の Python） |
| 2 | SBX-8 | Linux の `/opt` の確認から、Python の置き場所を除く | 修正（CI の Ubuntu。テストのみ） |
| 3 | GATE-9 | `verify-step` で本物のインデックスを変えない | 修正（機密の誤コミットの防止） |
| 4 | DX-9 | チェックポイントを SHA で記録する | 修正 |
| 5 | REL-5 | 追跡しているファイルからローカルの絶対パスを除き、検査する | 公開前の清掃 |
| 6 | DX-4 | `docs/cli.md` を argparse から生成し、ずれを検査する | 新規 |
| 7 | DX-3 | `examples/two-teams/` | 新規 |
| 8 | DX-1 | README のクイックスタートをテストする | 新規 |
| 9 | REL-2 | バージョン 0.9.0、CHANGELOG、リリースノート | リリース |

順序の理由: CI を最初に緑に戻す（1・2）。README が `docs/cli.md` と `examples/two-teams/` を案内するので、DX-4・DX-3 を DX-1 より先にする。バージョンの変更は最後にする。

## 3. 影響分析

### 3.0 入口の列挙

1. `auto-dev`
2. `python -m auto_developer.cli`
3. `scripts/nightly_orchestrator.py`（免疫テストはこれを `subprocess` で呼ぶ）
4. `main([...])` の直接呼び出し
5. スキル（`skills/nightly-run/SKILL.md` ほか）
6. README（このフェーズで書き直した）

### 3.1 SBX-7（ステップ 1）

- **指紋**: `verifier.py` の `backend == "sandbox-exec"` の分岐（`py_raw`, `py_bin`, `py_prefix`, `py_base_prefix`, `toolchain_allows`, `pfx_rules`）
- **プロファイルの文字列をそのまま確かめる既存テストはない**（`tests/` に `allow file-read` を含むものはない）。
- **守るべき既存テスト（変更しない）**: macOS の `test_sandbox_blocks_reading_*`（`test_red_verifier.py`）、`test_sandbox_preflight.py`。ホーム・`~/.ssh`・元のリポジトリ・`/Library/Keychains`・`/opt` が読めないことは変えない。
- **Red の作り方**: ホームの中の一時ディレクトリに、今の Python の実体を指すシンボリックリンクを作り、そのリンクを `sys.executable` として候補テストを起動する（子プロセスでリンクの Python から検証を呼ぶ）。今の実装では `SANDBOX_UNAVAILABLE` になるはずである。**手元の Mac で Red にならない場合は、テスト側で工夫して回避せず、「止まっていること・判断待ち」に書いて止まる**（AGENTS.md Rule 4）。
- **許可の範囲**: パスそのもの（`literal`）の読み取りとメタデータの読み取りだけを足す。`subpath` を新しく足さない。

### 3.2 SBX-8（ステップ 2）

- **変更する既存テスト**: `tests/test_linux_bwrap_isolation.py` の `test_bwrap_blocks_reading_unallowed_host_paths` の `/opt` の部分だけ。一時ディレクトリの部分（`host_only_secret.txt`）は変えない。
- **新しい確かめ方**: サンドボックスに見せる Python の置き場所（`sys.prefix`, `sys.base_prefix` の実体）と、その途中のディレクトリを除いた、`/opt` の直下の実在するエントリを1つ選ぶ。それを読むと `PermissionError` か `FileNotFoundError` になる（`_assert_read_blocked` を使う）。該当するエントリがなければ `skipTest` で理由を示す。
- 手元の Linux（Python が `/opt` の下にない）では、今までどおりの確かめ方になる。CI の Ubuntu では、`/opt/az` などが対象になる。

### 3.3 GATE-9（ステップ 3）— このフェーズで最も影響が広い

- **指紋**: `guard.py` の `get_working_tree_sha`（`add -A` → `write-tree`）と `commit_task`（ステージ済みのインデックスをそのままコミットする）。
- **呼び出し元**: `cli.py` 1224（verify-step）, 1467（commit-step）、`orchestrator.py` 691, 737, 811。
- **今の設計の前提**: `commit-step` は、直前の `get_working_tree_sha` が本物のインデックスに置いたツリーを、そのままコミットしている。つまり、**ステージは副作用ではなく、コミットする内容そのもの**である。インデックスを使わない形にするなら、`commit_task` が「同じ `GitGuard` で最後に求めた Tree SHA」を本物のインデックスに読み込んで（`read-tree`）からコミットする、などの置き換えが要る。
- **守るべき既存テスト（変更しない）**:
  - `tests/test_nightly_orchestrator.py` 1420 行目付近: `get_working_tree_sha()` の後に作業ツリーへ加わったファイルが、`commit_task(task)` でコミットされないこと（`commit_task must NOT restage unverified files!`）。`commit_task` の引数は変えずにこれを満たす。
  - `tests/test_commit_gate_boundary.py` 146〜151 行目付近: 隔離の後に、作業ツリーとインデックスがきれいであること。
  - 隔離（`guard.py` 623 行目付近の `add -u`、`recovery.py` 617 行目付近）がステージする挙動は変えない。
- **注意が要る箇所**: `git status --porcelain` を解析している箇所が 25 か所ある（`multi_team.py` 7、`guard.py` 6 など）。これまで `verify-step` の後は新しいファイルが `A ` と表示されていたが、GATE-9 の後は `?? ` のままになる。境界の検査（`boundary.py`）や未追跡ファイルの扱いで、`A ` を前提にしている箇所がないか確かめる。
- **新しいテスト**: `verify-step` の前後で `git status --porcelain` が同じこと。gitignore していない `.env` が `verify-step` でステージされないこと。`commit-step` の後の HEAD のツリーが検証したツリーと一致すること。

### 3.4 DX-9（ステップ 4）

- **指紋**: `verify-step` の `--checkpoint`（`cli.py` 649 行目付近）、Evidence の `checkpoint`、査読の記録の `checkpoint`、イベントの `checkpoint`。
- `"checkpoint": "HEAD"` を期待している既存テストはない。参照名を渡すテストは、記録の値が SHA になっても、比較していなければ影響しない。

### 3.5 REL-5（ステップ 5）

- **変更する既存テスト**: `tests/test_pilot04_recovery_e2e.py`（18 行目の `ZIP_PATH`）。環境変数（例: `AUTO_DEV_PILOT04_ZIP`）で渡し、指定がなければ `skipTest` する。アサーションは変えない。
- `scripts/run_pilot04_recovery_experiment.py` の 19〜21 行目も、環境変数か引数で渡す形にする。
- **検査のテスト**: `git ls-files` の全ファイルを読み、`/Users/<名前>/` と `/home/<名前>/` のうち、例示用の名前（`runner`, `user`, `alice`, `bob`, `<名前>` など）でないものがあれば失敗する。今の時点で該当するのは上の2ファイルだけ（`git grep` で確認済み）。

### 3.6 DX-4・DX-3・DX-1（ステップ 6〜8）

- 既存のテストで、リポジトリの README を読むものはない（`README.md` を書くテストは、一時リポジトリの中のものだけ）。README の書き直しは既存テストに影響しない。
- DX-1 の実行の対象は、README の中の `<!-- readme-test: run -->` の直後のコードブロックだけとした（SPEC DX-1）。README の書き手（人間）が、テストされる手順を明示する。
- DX-3 のサンプルは、`examples/` の下に `teams.json` と `scripts/verify_local.sh` を置く。保護ファイルの検査は、リポジトリの直下の `teams.json` と `scripts/verify_local.sh` だけを対象にしている（パスの先頭一致）ので、`examples/two-teams/teams.json` は保護ファイルにならない。**`verify_local.sh` の保護ファイルの検査の正規表現（`^(\.github/|SPEC\.md|scripts/verify_local\.sh|tests/immunity/|teams\.json$)`）は先頭一致なので、`examples/two-teams/SPEC.md` なども対象外**である。ただし `examples/two-teams/tests/immunity/` は置かない（紛らわしいため）。
- DX-3 のサンプルの中の `scripts/verify_local.sh` は、DX-5 の雛形と同じ Python の選び方にする。

### 3.7 REL-2（ステップ 9）

- **変更する既存テスト**: `tests/test_version_consistency.py` の期待値（`0.8.0` → `0.9.0`、`## [0.8.0]` → `## [0.9.0]`、`docs/releases/v0.8.0.md` → `v0.9.0.md`）。
- `tests/test_bundler.py` の `v0.8.0` の案内（949〜992 行目付近）は、v0.8 以前の Bundle の形式についての案内なので変えない。

## 4. 判断済みの点（2026-11-23）

- CI の macOS を一時的に `/usr/bin/python3` に戻して緑にすることはしない（不具合が隠れる）。
- GATE-9（`verify-step` のインデックス）を Phase 71 の要件に入れる。
- Phase 70 の Bundle の INVALID（タスク 3 の SPEC の食い違い）は受け入れ、記録に残す。

## 5. 人間が行うこと

- 準備のコミット: SPEC.md、PLAN.md、README.md、README.en.md、BACKLOG.md、LESSONS.md、この契約書。
- 準備のコミットの前に: Phase 70 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase70/` に退かせる。
- ステップ 1・2 の後: CI（macOS / Ubuntu × 3.10 / 3.12）が通ることを確かめる。
- ステップ 9 の後: Linux で `AUTO_DEV_REQUIRE_SANDBOX=1` 付きの全テスト、CI、査読の記録と Checker の出力の突き合わせ、Bundle の検証（VERIFIED になること）、独立監査、CONTRIBUTING.md、`v0.9.0` のタグ。

## 6. 影響分析の手順メモ（Phase 70 からの改善）

Phase 70 では、影響分析の漏れで、既存テストの承認のために 10 回近く止まった。漏れた種類と、次から探すものを残す。

- **判定に使う目印をモックで作っているテスト**: SBX-6 で「開始の目印」を加えたとき、`subprocess.Popen` のモックが `===AUTO_DEV_TEST_RESULT===` だけを返すテスト（4件）を見落とした。判定の規則を変えるときは、その判定が読む目印・出力の形を、テストの中で作っている箇所を検索する。
- **キャッシュや呼び出し回数を確かめるテスト**: 起動の確認のキャッシュのキーを変えたとき、`len(captured) == 1` を確かめるテスト（2件）を見落とした。キャッシュのキーを変えるときは、呼び出し回数のアサーションを検索する。
- **範囲の広すぎるモック**: `"sandbox" / "-c"` を含む `Popen` をすべて偽物に流すテスト（2件）が、新しく走るようになった起動の確認を巻き込んだ。新しい内部の呼び出しを加えるときは、その呼び出しを巻き込むモックの条件を検索する。
- **記録を合成しているテスト**: BDL-4 で Bundle の形式を変えたとき、Evidence の記録を合成して Bundle を作るテストが、見積もりの2ファイルから9ファイルに増えた。記録や Bundle の形式を変えるときは、`nightly_evidence`、`records`、`create_bundle` を検索して、すべて数える。
- **フェーズの途中の SPEC の変更**: SPEC を直すと、それより前のタスクの Evidence は Bundle の検証で SPEC が食い違う（FRICTION-005）。途中で SPEC を直す必要が出たら、そのことを Bundle の結果とあわせて記録に残す。
- **製品の副作用に依存した手順**: `verify-step` のステージ（GATE-9）は、エージェントの手順（承認の記録のコミット）を壊した。手順書を書くときは、コマンドが本物のインデックスや作業ツリーを変えるかどうかを確かめる。
