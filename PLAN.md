# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
前のフェーズの PLAN は git の履歴で参照する（Phase 70 は 898fe57 時点の PLAN.md）。
-->

## 現在の目標
- Phase 71: v0.9.0 を、初めての利用者が macOS と Linux のどちらでもつまずかずに試せる状態にして、リリースする（SPEC.md 5.1: SBX-7, SBX-8, GATE-9, GATE-10, DX-9, REL-5, DX-4, DX-3, DX-1, REL-2）
  - 最初に、Phase 70 の後の CI で見つかった macOS と Ubuntu の失敗を直す（docs/contracts/phase71.md の 1.1 節）。
  - README.md（日本語）と README.en.md は、準備のコミットで人間が書いた。DX-1 は、その README に書いたコマンドを実行するテストである。README を Maker が書き換えてはならない（ずれが見つかったら止まって報告する）。
  - 要件ごとの影響分析は docs/contracts/phase71.md を参照する。「既存テスト変更の理由」は、この分析から作っている。
- スコープ外: チーム間の依存関係（`depends_on`）、統合結果の main への自動マージ。

## 作業手順（Phase 71・LESSONS.md に基づく）
- Maker と Checker は別系統のモデルにする（`commit-step` が終了コード 4 で強制する）。
  - Maker: Gemini 3.8 Flash (`gemini-3.8-flash`)
  - Checker: Claude Opus 5.5 (`claude-opus-5-5`、Anthropic API 利用。環境変数 `ANTHROPIC_API_KEY` を利用)
- **Checker の完了の通知だけでは、次に進まない。** Checker のプロセスが終わり、出力ファイルから判定が1つに決まること（GATE-5 の判定の読み取りの規則）を確かめてから記録する。通知と出力ファイルが食い違う場合は、出力ファイルを正とする。
- Checker には、指摘は要点だけにして、判定行（`DECISION: APPROVE` または `DECISION: REJECT`）を必ず最後の行に書くよう求める（出力が途中で切れて判定行がなくなるのを防ぐ）。
- Checker への入力は、要約・再構成しない。
  1. SPEC の該当節: `awk '/^### 5\.1/,/^## 6\./' SPEC.md > .dev/checker/spec_phase71.md`（原文。`record-review` が、SPEC.md の連続した一部であることを確かめる）
  2. 差分: `auto-dev review-input --task-id <n> --out-dir .dev/checker/task<n>`
  3. Checker の出力: `.dev/checker/review_p71_task<n>.txt`（やり直したときは `_v2` などを付け、上書きしない）
- Checker には、SPEC の要件のうち、例外・異常系・境界値の扱いを満たしているかを、差分とテストの両方で確かめるよう求める。
- verify-step の前に `git status --porcelain` を確認し、意図しないファイルがないことを確かめる。実験は `mktemp -d` の中で行う。
- `--checkpoint` には、参照名（`HEAD`）ではなくコミットの SHA を渡す（`git rev-parse HEAD`。ステップ 5 の DX-9 の完了までは、記録に参照名がそのまま残るため）。
- 査読記録の順序:
  1. `auto-dev verify-step --checkpoint <SHA> --task-id <n>`
  2. Checker の査読。1回の verify-step に対して査読は1回とする。
  3. 判定が APPROVE でも REJECT でも、`auto-dev record-review --task-id <n> --checker-output <出力> --diff-file <diff.patch> --spec-file <spec の切り出し> --maker-model <maker> --checker-model <checker> --reason "<理由>"` で記録する（REJECT も記録して、計測の数を正しくする）。
  4. REJECT なら commit-step を呼ばず、手直しして 1 からやり直す。
  5. APPROVE なら `auto-dev commit-step --task-id <n> --checkpoint <SHA> --maker-model <maker> --checker-model <checker> --checker-reason "<reason>"`
  6. commit-step が終了コード 3 で拒否されたら、verify-step からやり直す。再検証した後も、査読と record-review をやり直す（作業ツリーが変わっていなくても省略しない）。
  7. commit-step が終了コード 4 で拒否されたら、原因を「止まっていること・判断待ち」に書いて止まる。引数を変えて再実行しない。
- Checker の判定が2回続けて承認にならなかったら（REJECT、または判定が読み取れない）、INV-4 に従って隔離して止まる。
- **人間の承認を PLAN.md に記録するコミットは、必ず `git commit -m "docs(plan): ..." -- PLAN.md` の形にする。** ステップ 3（GATE-9）の完了までは、`verify-step` が本物のインデックスにステージするため、`git add PLAN.md && git commit` では実装が混ざる（Phase 70 の f51e971）。
- コミットした後に欠陥が見つかった場合は、`git reset` などで正規の経路の外から巻き戻さない。「止まっていること・判断待ち」に書いて止まり、修正は新しいステップとして行う。
- テストを書く途中で製品の欠陥に気づいた場合は、テスト側で回避しない。「止まっていること・判断待ち」に書いて止まる（AGENTS.md Rule 4）。
- 「既存テスト変更の理由」に書いていない既存テストが失敗した場合は、変更せずに止まって報告する。
- E2E テストで作る一時リポジトリ・worktree は、必ず `tempfile` の中に作る。`git init` には必ず `-b <ブランチ名>` を付けるか、既定のブランチに依存しない書き方をする。
- サブプロセスを起動するテストでは、`PYTHONDONTWRITEBYTECODE` と `PYTHONPYCACHEPREFIX` を環境から外して実行し、macOS と Linux で結果が変わらないようにする。
- `sys.platform` で分岐する処理を書くときは、分岐の前で変数の型を宣言する（検証スクリプトが両 platform の mypy を実行する）。
- 各ステップの検証は手元の macOS で行う。Linux での確認は人間が行う。Maker は、Linux で結果が変わりうる変更をしたときは、コミットメッセージにその旨を書く。
- SPEC.md はフェーズの途中で変えない（変えると、それより前のタスクの Evidence が Bundle の検証で食い違う。Phase 70 のタスク 3）。SPEC にあいまいさや矛盾を見つけたら、止まって報告する。
- Bundle を生成した後に、SPEC.md や追跡ファイルを変更しない。SPEC の完了マークは、次のフェーズの準備コミットで人間が付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
<!-- docs/contracts/phase71.md の 3 章の影響分析から作成。ここに書いていない既存テストは変更しない。どのステップでも、アサーションは変えない（下に明記したものを除く）。 -->
- ステップ 1（SBX-7）: 既存テストは変更しない。特に macOS の `test_sandbox_blocks_reading_*`（`tests/test_red_verifier.py`）と `tests/test_sandbox_preflight.py` は、そのまま通ること。
- ステップ 2（SBX-8）: `tests/test_linux_bwrap_isolation.py` の `test_bwrap_blocks_reading_unallowed_host_paths` のうち、`/opt` を確かめる部分だけを、SBX-8 の規則（Python の置き場所とその途中のディレクトリに当たらない `/opt` の直下のエントリを選び、読めないことを `_assert_read_blocked` で確かめる。該当するエントリがなければ理由を示してスキップ）に変えてよい。一時ディレクトリを確かめる部分は変えない。
- ステップ 3（GATE-9）: 既存テストは変更しない。特に `tests/test_nightly_orchestrator.py` 1420 行目付近（`commit_task must NOT restage unverified files`）と `tests/test_commit_gate_boundary.py` 146〜151 行目付近は、`commit_task` の引数を変えずに、そのまま通ること。
- ステップ 4（GATE-10）: 既存テストは変更しない。特に `tests/test_init_gitignore.py`（CORE-11 の行が1回ずつ入ること）、`tests/test_nightly_orchestrator.py` 1217 行目付近と `tests/test_recovery.py` 1202 行目付近（隔離に `.env` や鍵を入れないこと）、`tests/test_multi_team_foundation.py` 305〜420 行目付近（`.env` のある worktree を消さないこと）は、そのまま通ること。
- ステップ 5（DX-9）: 既存テストは変更しない。
- ステップ 6（REL-5）: `tests/test_pilot04_recovery_e2e.py` の `ZIP_PATH`（18 行目付近）を、環境変数で渡し、指定がなければ理由を示してスキップする形に変えてよい。アサーションは変えない。
- ステップ 7〜9（DX-4・DX-3・DX-1）: 既存テストは変更しない。README.md と README.en.md は変更しない。
- ステップ 10（REL-2）: `tests/test_version_consistency.py` の期待値を、`0.8.0` から `0.9.0` に（`## [0.9.0] - `、`docs/releases/v0.9.0.md`、`v0.9.0` を含む）変えてよい。`tests/test_bundler.py` の `v0.8.0` の案内は変えない。
- 免疫テスト（`tests/immunity/**`）は変更しない。
- どのステップでも、変更したテスト名をコミットメッセージに列挙する。

## ステップ
- [x] 1. macOS で、Python の実行ファイルがシンボリックリンクでも候補テストを起動できるようにする。リンクの先までの途中のパスについて、リンクの読み取りとメタデータの読み取りだけを `literal` で許可する（Red: ホームの中に実際の Python を指すリンクを作り、それを使うと `SANDBOX_UNAVAILABLE` になる）（SBX-7）
- [x] 2. Linux の `/opt` の確認から、Python の置き場所とその途中のディレクトリを除く（テストのみ。製品の挙動は変えない）（SBX-8）
- [x] 3. `verify-step`（`run` の経路を含む）で、作業ツリーの Tree SHA を一時的なインデックスで求め、本物のインデックスを変えない。`commit-step` は最後に求めた Tree SHA のツリーをそのままコミットする（Red: `verify-step` の後に、gitignore していない `.env` がステージされている）（GATE-9）
- [x] 4. `init` / `teams init` の `.gitignore` に `.env`・`.env.*`・`!.env.example` を加える。`verify-step` と `commit-step`（`run` の経路を含む）で、チェックポイントから追加・変更された `.env*`（例示用の3つを除く）・鍵ファイル・SSH の鍵の名前のファイルを `SECRET_FILE_IN_COMMIT:` で拒否する（Red: gitignore していない `.env` が `commit-step` でコミットされる）（GATE-10）
- [x] 5. `verify-step --checkpoint` に参照名を渡しても、Evidence・査読の記録・イベントに解決した SHA を記録する（Red-First）（DX-9）
- [x] 6. 追跡しているファイルからローカルの絶対パスを除き、環境変数で渡す形にする。追跡しているファイルを検査するテストを置く（Red: 検査のテストが、今の2ファイルで失敗する）（REL-5）
- [x] 7. `scripts/gen_cli_reference.py` で argparse から `docs/cli.md` を生成し、ずれを検出するテストを置く（Red-First）（DX-4）
- [x] 8. `examples/two-teams/`（2チームの電卓）を置き、一時ディレクトリに写して `teams prepare` まで通ることを確かめる（Red-First）（DX-3）
- [x] 9. README.md と README.en.md の `` のブロックを一時ディレクトリで実行するテストと、日英のブロックの一致の検査（Red: テストがまだない／README のコマンドが通らなければ止まって報告）（DX-1）
- [ ] 10. バージョンを 0.9.0 に揃え、CHANGELOG の `## [0.9.0]` 節と `docs/releases/v0.9.0.md` を置く（Red: `test_version_consistency.py` の新しい期待値）（REL-2）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [x] Phase 71 の準備コミット: SPEC.md（Phase 70 の要件を 4 章へ移し、5.1 に Phase 71 の要件）、PLAN.md、README.md、README.en.md、LESSONS.md、BACKLOG.md、docs/contracts/phase71.md。
- [x] 準備のコミットの前に: Phase 70 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase70/` に退かせる。
- [ ] ステップ 2 の完了後: CI（macOS / Ubuntu × Python 3.10 / 3.12）が通ることを確かめる。
- [ ] ステップ 10 の完了後:
  - Linux（Ubuntu・bwrap が動く環境）で、全テストを `AUTO_DEV_REQUIRE_SANDBOX=1` 付きで実行する。
  - CI が通ることを確かめる。
  - Phase 71 の全タスクについて、査読の記録と `.dev/checker/` の出力ファイルを突き合わせ、食い違いが0件であることを確かめる。
  - Bundle を作り、`verify-bundle` が VERIFIED になることを確かめる。
  - 最新の HEAD に対する独立監査を行い、`AUDIT_OUTDATED` / `RELEASE_BLOCKED` がないことを確かめる。
  - CONTRIBUTING.md を書く。
  - `v0.9.0` のタグを付ける。

## 記録
- 2026-11-23: 準備のコミット（d93b66d）の後、BACKLOG IDEA-014（gitignore していない `.env` が `commit-step` でコミットされる）を GATE-10 として加え、ステップ 4 とした（以降のステップの番号を1つずつ繰り下げた。どのステップにも着手する前）。
- 2026-11-23: Phase 70 の完了後の確認。11 ステップすべて完了。保護ファイルを変えたのは人間のコミットだけ。査読の記録と Checker の出力ファイルの突き合わせで、REJECT のままコミットされたものは0件（タスク 3〜11 は機械で照合、タスク 1・2 は最後の出力が APPROVE）。Linux で `AUTO_DEV_REQUIRE_SANDBOX=1` 付きの全テスト 849 件が通った（時間に依存する3件が、負荷の高い1回目だけ失敗し、単独では通る）。`auto-dev demo` は終了コード 0、Bundle は VERIFIED。
- 2026-11-23: Phase 70 の Bundle（`bundle_job_1795414157.zip`）は `verify-bundle` で INVALID。タスク 3 の Evidence の SPEC の SHA が、フェーズの途中の SPEC の変更（265e605、c30b6cf）より前のもの。タスク 1・2 は `NO_REVIEW_EVIDENCE` で除外。後から直す手段はなく、v0.9.0 の歴史の断絶として受け入れる。リリースの判定には Phase 71 の Bundle を使う。
- 2026-11-23: Phase 70 の後の CI で、macOS は 72 件の失敗と 9 件のエラー（`realpath: ... Operation not permitted` → `SANDBOX_UNAVAILABLE`）、Ubuntu は 1 件の失敗（`/opt` の確認）。どちらも、Phase 70 の準備で検証スクリプトがマトリクスの Python を使うようになって表に出た（docs/contracts/phase71.md の 1.1 節）。
- 2026-11-23: ステップ 4 の前に、SPEC に2点を足した。(1) BDL-4: Phase 70 のタスク 1・2 は GATE-6 より前の経路でコミットしたため査読の証跡がなく、後から記録できない。これらは `excluded_records`（`NO_REVIEW_EVIDENCE`）として扱い、Phase 70 の Bundle は INTEGRITY_ONLY になる（v0.9.0 の歴史の断絶として受け入れる）。(2) GATE-8: ステップ 2 の査読で指摘された、正規の差分の機密（INV-5）。番号がずれないよう、最後のステップ 11 とした。
- 2026-11-23: `verify-step` が作業ツリーの SHA の計算で `git add -A` を実行し、本物のインデックスにステージする（`guard.py` の `get_working_tree_sha`）。承認の記録のコミットに実装が混ざった（f51e971、人間が作り直した）。承認の記録は `git commit -m "..." -- PLAN.md` の形でコミットする。製品の修正は BACKLOG へ。
- 2026-11-22: Phase 69 の完了後、CI の Ubuntu 24.04 で `test_red_verifier.py` の29件が失敗した（大半が `NO_TESTS_RAN`、1件で `bwrap: Creating new namespace failed: Resource temporarily unavailable`）。候補テストに設定する `RLIMIT_NPROC` をプロセス数で決めていたが、Linux の `RLIMIT_NPROC` はスレッドを数えるため、スレッドの多いランナーで上限に当たっていた。手元の Linux で同じユーザーのスレッドを 300 本立てると、同じ失敗を再現できた。あわせて、CI の Ubuntu ではテストが OS の Python 3.12 で走っていた（Traceback の `^^^` の表示から）。
- 2026-11-22: Phase 69 の完了後の確認で、Phase 67〜69 の全タスクの査読の記録と Checker の出力ファイルを突き合わせた。REJECT の出力のまま APPROVE としてコミットされたもの4件（b7af7f2, 86eeea9, e5fa287, 84a0043）と、出力より先にコミットしたもの2件（判定は一致）を見つけた。原因は、Checker のバックグラウンドの完了通知が2回届き、1回目だけを見て記録したこと（Antigravity 側の分析）。
- 2026-11-22: Phase 69 の完了後、Linux で `AUTO_DEV_REQUIRE_SANDBOX=1` 付きの全テストを実行し、800 件がすべて通った。`pip install` → `auto-dev demo` は終了コード 0 で通った。
- 2026-11-22: ad-team-trial で `teams integrate --job-id job-20261122-002709-97afe0` を実行し直し、`core` が `NO_CHANGES` として扱われ、`cli` だけが統合されて RC タグができることを確かめた（TEAM-27。main には取り込んでいない）。
- 2026-11-21: Phase 68 の完了後、Linux で全テストを実行し、746 件がすべて通った。
- 2026-11-22: REL-3（CI のマトリクス化）を行った（3eac615）。Ubuntu の mypy で検出した型エラーは、Phase 69 のステップ 1 で直した。
- 2026-11-22: 実際のエージェントによる `/nightly-run` のチーム経路を、ad-team-trial で確かめた（詳細は ad-team-trial の TRIAL.md）。
