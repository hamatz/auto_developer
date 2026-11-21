# 契約書: Phase 68（v0.9.0）

<!--
SPEC.md・PLAN.md を確定する前に、人間が確認するための資料です（2026-11-21 承認済み）。
要件の一覧に加えて、要件ごとの影響分析（既存のテスト・ドキュメントのどこが旧挙動を前提にしているか）を載せています。
Phase 66・67 では、この分析がなかったために、実装の途中で3回止まりました。
今回は手作業で行い、どこを機械化できるか（Spec Compiler の設計材料）を最後にまとめています。
作成: 2026-11-21（Phase 67 完了時点 b7af7f2 に対する分析）
-->

## 1. このフェーズの目的

**Linux を macOS と同格に扱える状態にし、Phase 67 の確認で見つかった欠陥を直す。**

- 開発は Mac で行う。ただし、Auto Developer の対応 OS は Mac に絞らない。
- このため Linux では、次の2つを満たすことを目標にする。
  - 全テストが通る（サンドボックスを使うテストを含め、スキップで済ませない）。
  - 利用者がつまずいたときに、何をすればよいかがわかる。

README の書き直し・サンプル・リリースは、CLI の形が固まってから行う。Phase 69 に分ける（判断 D1）。

## 2. 要件の一覧

| ID | 内容 | 種別 | 起点 |
| :--- | :--- | :--- | :--- |
| SBX-3 | サンドボックスが実際に起動できるかを確かめ、起動できなければ OS ごとの対処を示して `SANDBOX_UNAVAILABLE` にする | 修正 | 調査（bwrap があっても、AppArmor の制限で起動できない Ubuntu 24.04） |
| SBX-4 | OS 固有の手段に頼らない期待値でテストを書く。CI ではサンドボックスのテストをスキップさせない | テスト修正 | Linux で失敗する既存の3件 |
| SBX-5 | macOS で実際に実行して確かめている「機密が読めない」性質を、Linux の bwrap でも実際に実行して確かめる | テスト追加 | 調査（Linux 側は、bwrap に渡す引数の組み立てしか確かめていない） |
| BDL-3 | Bundle とレポートに入れる Evidence の記録を、対象の計画で完了したタスクのものに限る | 修正 | Phase 67 の Bundle が INVALID になった（Phase 66 の task 8 が混入） |
| TEAM-25 | `teams init` が、`base.ref` に現在のブランチを書く | 修正 | 既定のブランチが `master` だと、最初の `teams prepare` が失敗する |
| TEAM-26 | 成功したジョブの `teams cleanup` で、統合ブランチ `integration/<job_id>` も削除する（RC タグは残す） | 修正 | 統合ブランチが溜まっていく |
| RPT-5 | `brief` の「判断待ちの事項」に、単一チーム用の文言を出さない | 修正 | 表示の不整合 |
| DX-2 | 外部コマンドを指定して、モデル名を省略した `run` / `teams run` を、終了コード 2 で拒否する | 既存要件 | v0.9.0 の当初案（判断 D2） |
| REL-3 | CI を macOS / Ubuntu 24.04 × Python 3.10 / 3.12 で実行し、サンドボックスのテストをスキップさせない | 既存要件（人間が実施） | v0.9.0 の当初案（判断 D4） |

上の9件のうち、REL-3（CI）は `.github/` の変更なので人間が行う。残りの8件を、Maker が8ステップで行う。

Phase 69 に回すもの: DX-1（README の手順の実行テスト）、DX-3（`examples/two-teams/`）、DX-4（CLI リファレンスの生成）、REL-2（0.9.0 表記）、README.md と README.en.md、CONTRIBUTING.md、独立監査、タグ。

## 3. 影響分析

各要件について、旧挙動の「指紋」（文字列・終了コード・パス・関数名など）を `tests/` と、ドキュメント・スキルから grep した。

### SBX-3 サンドボックスの実働確認

- **指紋**: `_get_sandbox_backend`, `shutil.which("bwrap")`, `SANDBOX_UNAVAILABLE`
- **影響する既存テスト**: なし。`_get_sandbox_backend` をモックしているテストは5か所あるが（`test_red_verifier.py` 478, 809, 859, 926, 975 行目）、関数名と戻り値の約束（`"sandbox-exec"` / `"bwrap"` / `None`）を変えなければ影響しない。実働の確認はこの関数の中で行い、結果をキャッシュする。
- **影響するドキュメント**: README.md（対応 OS と、Linux での事前準備）。書き直しは Phase 69。

### SBX-4 OS に依存しない期待値

- **指紋**: `chflags`（macOS 専用コマンド）、`PermissionError`（macOS の `sandbox-exec` の拒否の仕方）
- **影響する既存テスト（変更が必要）**:
  - `tests/test_red_verifier.py`
    - `test_repo_tamper_detected_when_parent_repo_modified`: 改ざんを仕込む部分で `chflags` を無条件に呼び、Linux では例外になる。製品側（`verifier.py` 1324 行目）は `sys.platform == "darwin"` を確かめてから使っており、正しい。
    - `test_repo_tamper_detected_when_untracked_content_tampered_in_flight`: 同上。
    - 何もしない `chflags` を PATH に置くと、Linux でも2件とも通ることを確認した（2026-11-21）。
  - `tests/test_independent_audit_runner.py`
    - `test_criterion_1_execution_isolation`: 親リポジトリの `.env` を読むと `PermissionError` になることを期待している。Linux の bwrap はそもそも親リポジトリをマウントしないので `FileNotFoundError` になる（読めないという性質は同じで、むしろ見えない）。期待値は「機密の値が出力に現れない」「Red として受理されない」という性質で書く。
- **影響する既存テスト（スキップ・切り替えの扱い）**: `test_live_claude_audit.py` 99 行目は、サンドボックスがなければ、気づかれないままモックに切り替わる。スキップよりも気づきにくい。`AUTO_DEV_REQUIRE_SANDBOX=1` のときは、切り替えずに失敗にする。

### SBX-5 実行による確認の対称性

- **指紋**: `skipTest("macOS sandbox-exec is only available on macOS")`（`test_red_verifier.py` 876, 999, 1024 行目）、`test_bwrap_command_*`
- **現状**:
  - macOS では、`test_sandbox_blocks_reading_system_keychains_and_sensitive_etc`、`..._unallowed_host_paths_like_opt`、`..._broad_system_dirs_like_library_and_var` の3件が、実際にサンドボックスの中で読み取りを試している。
  - Linux では、`test_bwrap_command_*` / `test_bwrap_virtualenv_*` の4件が、bwrap に渡す引数を確かめるだけで、実際には実行していない。
- **影響する既存テスト**: なし（テストを追加するだけで、既存のテストは変更しない）。
- **影響するドキュメント**: なし
- **影響するドキュメント**: なし

### BDL-3 Bundle に入れる記録の選択

- **指紋**: `nightly_evidence/records`, `task_*.json`, `archive_record(`, `Tasks Audited`
- **規則（案）**:
  - Bundle を作るとき、またはレポートを作るときに、対象の計画ファイルで完了（`- [x]`）しているタスクの記録だけを選ぶ。それ以外の記録は入れず、manifest の `excluded_records` に、タスク ID と理由を記録する。
  - 対象の計画ファイルにタスクとして読める行が1つもない場合は、従来どおりすべての記録を入れる。
  - `verify-bundle` の検査（Bundle 内の記録に対する SPEC ハッシュ・Tree SHA・Checker の確認）は弱めない。
- **影響する既存テスト**:
  - 記録を直接作るテストは11ファイルある。
    - ほとんどは計画ファイルが `- [x] Task 1` のような番号のない書き方で、タスクとして読まれない。逃げ道の規則によって、従来どおりの挙動になる。
  - 要確認（番号つきのタスクと記録が混在する）:
    - `test_multi_team.py`（638 行目、`- [ ] 1.`）
    - `test_teams_integrate.py`（75〜79 行目、チームの計画）
    - `test_reporter.py`（396, 413 行目）
    - `test_nightly_orchestrator.py`
  - 上記以外が失敗した場合や、上記を変更する必要が出た場合は、Maker が止まって報告する。
- **影響するドキュメント**: `skills/nightly-bundle/SKILL.md`（Bundle の中身の説明に、除外の規則を追記）

### TEAM-25 `teams init` の基点ブランチ

- **指紋**: `"ref": "main"`（`cli.py` 360 行目）、ブランチを指定しない `git init`
- **影響する既存テスト**:
  - `teams.json` を自分で書いて `"ref": "main"` を指定しているテストが8ファイルある。これらは雛形を使わないので影響しない。
  - `test_plan_parsing_scope.py`（203, 255, 316 行目）は `git init` にブランチを指定していない。今は既定が `main` の環境でしか通らないが、修正後は環境によらず通るようになる（テストの変更は不要）。
  - `test_teams_config_and_init.py` には、`ref` の値を確かめるアサーションがない。
- **影響するドキュメント**: `skills/nightly-setup/SKILL.md`（`teams init` の説明）

### TEAM-26 統合ブランチの片付け

- **指紋**: `integration/`
- **影響する既存テスト**:
  - `test_prepare_rejects_uncompleted_integration_branch` は、未完了の統合ブランチがあると `prepare` が拒否されることを確かめている。成功したジョブだけを対象にすれば影響しない。
  - `test_teams_prepare_status_cleanup.py` で、統合ブランチが残ることを確かめているアサーションはない。
- **影響するドキュメント**: なし（README の書き直しで触れる）

### RPT-5 `brief` の文言

- **指紋**: 「単一チーム実行の隔離タスクはありません」（`reporter.py` 405 行目）
- **影響する既存テスト**: この文言を確かめているテストはない。
- **影響するドキュメント**: なし

### DX-2 モデル名の既定値

- **指紋**: `default="gemini-3.8-flash"`, `default="claude-opus-5.5"`（`cli.py` 451, 454, 463, 493, 494, 497 行目）
- **影響する既存テスト（変更が必要）**: 外部コマンドを指定して、モデル名なしで `run` / `teams run` を呼んでいる箇所が14か所ある。変更は、モデル名の引数を足すことだけに限る。
  - `test_team_context_and_protection.py`: 354, 365, 377, 394, 405, 415, 434, 446 行目
  - `test_teams_run_and_recover.py`: 286, 317 行目
  - `test_teams_cli_e2e.py`: 307 行目（`teams run` の設定エラーの検査）
  - `test_multi_team.py`: 690, 695 行目（ワーカーの引数 `extra_args`）
  - `test_cli.py`: 31 行目は終了コード 2 を期待しているので、変更は不要。
  - `NightlyOrchestrator` を直接作るテスト（47か所）は、CLI の既定値を通らないので影響しない。DX-2 は CLI だけに適用する。
- **影響するドキュメント**: README.md（Phase 69 で書き直す）。`skills/nightly-run/SKILL.md` の `teams run` の例は、すでにモデル名を含むので変更は不要。

## 4. 判断していただきたい点

> **決定（2026-11-21、人間が承認）**: D1〜D4 はすべて案A を採用する。Phase 69 への分割も承認した。

- **D1 フェーズの分割**:
  - 案A（推奨）: Phase 68 では上の9件、Phase 69 では README・サンプル・リリースを行う。README は CLI が固まった後に、準備コミットで先に書く（「契約」の一部にする）。DX-1 のテストは、その README に対して Red から始める。
  - 案B: すべてを Phase 68 で行う。
- **D2 DX-2 の厳しさ**:
  - 案A（推奨）: モデル名を省略したら、終了コード 2 で拒否する。実際に使っていないモデル名が監査レコードに残るのを防ぐ。既存テストの14か所を、機械的に変更する必要がある。
  - 案B: 既定値を記録する代わりに `model_source: "default"` を残し、警告を出す。既存テストへの影響はないが、誤った記録が残る余地がある。
- **D3 BDL-3 の規則**:
  - 案A（推奨）: 上記の、計画で完了したタスクの記録だけを入れる規則。
  - 案B: 記録にフェーズ名を持たせる。フェーズ名を使わない利用者には効かない。
  - 案C: フェーズの準備で記録を退避する手順を、人間の作業として残す。OSS の利用者は必ず踏む。
- **D4 CI の Linux 環境**（人間が `.github/` を変更する。次の 5 章を参照）:
  - 案A（推奨）: bwrap 用の AppArmor プロファイル（`bwrap-userns-restrict`）を読み込む。影響が bwrap に限られる。
  - 案B: `sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` で制限そのものを外す。手順は簡単だが、制限を全体で外すことになる。

## 5. 人間が行うこと

- Phase 68 の準備コミット: SPEC.md、PLAN.md、LESSONS.md、この契約書。
- Phase 68 の完了後（REL-3）: `.github/workflows/verify.yml` を、OS と Python のマトリクスにする。変更の案（D4 は案A の場合）:

  ```yaml
  jobs:
    verify:
      strategy:
        fail-fast: false
        matrix:
          os: [macos-latest, ubuntu-24.04]
          python-version: ['3.10', '3.12']
      runs-on: ${{ matrix.os }}
      env:
        AUTO_DEV_REQUIRE_SANDBOX: '1'
      steps:
        - uses: actions/checkout@v4
          with:
            fetch-depth: 0
        - uses: actions/setup-python@v5
          with:
            python-version: ${{ matrix.python-version }}
        - name: Enable bubblewrap sandbox (Linux)
          if: runner.os == 'Linux'
          run: |
            sudo apt-get update
            sudo apt-get install -y bubblewrap apparmor-profiles
            sudo install -m 0644 /usr/share/apparmor/extra-profiles/bwrap-userns-restrict /etc/apparmor.d/bwrap-userns-restrict
            sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
            bwrap --unshare-net --ro-bind / / true
        # 以降（依存のインストール・ruff・mypy・verify_local.sh・gitleaks）は現行のまま
  ```

  - 最後の `bwrap ... true` は、サンドボックスが実際に起動できることを最初に確かめるための行。
  - GitHub Actions 上で一度実行して、通ることを確かめてから、必須チェックに設定する。

## 6. 影響分析の手順メモ（Spec Compiler の設計材料）

今回、手作業で行ったこと:

1. 要件ごとに、旧挙動の指紋を書き出す。指紋は、文字列・終了コード・ブランチ名やパスのパターン・関数名・既定値である。
2. 指紋を `tests/` とドキュメント・スキルから grep する。
3. ヒットした箇所を読み、3つに分類する。
   - 影響しない（モックの対象が変わらない、テストが自分で値を書いている など）
   - 変更が必要（変更してよい範囲を、行単位で書く）
   - 要確認（実装してみないとわからない。失敗したら止まる）
4. 呼び出しの形が条件になる指紋は、grep では足りない。たとえば「`run` を呼び、`--maker-cmd` があり、`--maker-model` がない」がそうだ。この場合は、小さなスクリプトで呼び出しの塊を探した（DX-2）。
5. 実環境で再現できるものは、再現して原因を確定する（SBX-4 で、何もしない `chflags` を PATH に置いた実験）。

機械化できそうなこと:

- 1 の指紋を SPEC の要件に「旧挙動」として書かせれば、2 と 4 は決定論的に実行できる。
- 3 の分類と、5 の実験の設計には、LLM と人間の判断が要る。
- この契約書の 3 章の形（指紋 / 影響する既存テスト / 影響するドキュメント）を、PLAN.md の「既存テスト変更の理由」に直接流し込めるようにすると、今回のような停止を準備の段階で防げる。
