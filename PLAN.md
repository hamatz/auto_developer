# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
前のフェーズの PLAN は git の履歴で参照する（Phase 69 は 84a0043 時点の PLAN.md）。
-->

## 現在の目標
- Phase 70: 査読の証跡を機械で強制し、Checker に差し戻されたまま入った変更を直す（SPEC.md 5.1: SBX-6, GATE-5, GATE-6, GATE-7, BDL-4, RCV-5, DX-8, RPT-8, BDL-5, TEAM-28, CORE-12）
  - 最初に、CI の Ubuntu で候補テストのサンドボックスが起動できない欠陥（SBX-6）を直す（docs/contracts/phase70.md の 1.1 節）。Linux のデスクトップでも起きる。
  - Phase 67〜69 で、Checker の出力が REJECT なのに APPROVE として記録・コミットされたものが4件あった（docs/contracts/phase70.md の 1 章）。
  - 判定を Checker の出力ファイルから読み取り、差分・SPEC・順序に拘束する。そのうえで4件の欠陥を直す。
  - 要件ごとの影響分析は docs/contracts/phase70.md を参照する。「既存テスト変更の理由」は、この分析から作っている。
- スコープ外: README の書き直し、DX-1 / DX-3 / DX-4 / REL-2（Phase 71）。

## v0.9.0 の残り
- Phase 71: 準備コミットで README.md（日本語）と README.en.md を先に書く。クイックスタートの中心は `pip install` → `auto-dev demo`。そのうえで DX-1、DX-3、DX-4、REL-2 を行い、CONTRIBUTING.md、独立監査、v0.9.0 のタグで終える。

## 作業手順（Phase 70・LESSONS.md に基づく）
- Maker と Checker は別系統のモデルにする（`commit-step` が終了コード 4 で強制する）。
  - Maker: Gemini 3.8 Flash (`gemini-3.8-flash`)
  - Checker: Claude Opus 5.5 (`claude-opus-5-5-medium`、Anthropic API 利用。環境変数 `ANTHROPIC_API_KEY` を利用)
- **Checker の完了の通知だけでは、次に進まない。** 次の2つを確かめてから記録する（LESSONS.md 2026-11-22）。
  - Checker のプロセスが終わったこと
  - 出力ファイルに判定行がちょうど1つあること（`DECISION: APPROVE` または `DECISION: REJECT`）
  - 通知が2回届いた場合や、通知と出力ファイルの内容が食い違う場合は、出力ファイルを正とする。
- Checker への入力は、要約・再構成しない。
  1. SPEC の該当節: `awk '/^### 5\.1/,/^### 5\.2/' SPEC.md > .dev/checker/spec_phase70.md`（原文。`record-review` が、SPEC.md の連続した一部であることを確かめる）
  2. 差分:
     - ステップ 2 の完了後は `auto-dev review-input --task-id <n> --out-dir .dev/checker/task<n>` を使う（`record-review` は、この正規の差分との一致を確かめる）。
     - ステップ 2 の作業中は `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch` を使う。
  3. Checker の出力: `.dev/checker/review_p70_task<n>.txt`（やり直したときは `_v2` などを付け、上書きしない）
- Checker には、SPEC の要件のうち、例外・異常系・境界値の扱いを満たしているかを、差分とテストの両方で確かめるよう求める。
- verify-step の前に `git status --porcelain` を確認し、意図しないファイルがないことを確かめる。実験は `mktemp -d` の中で行う。
- 査読記録の順序:
  1. `auto-dev verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker の査読。1回の verify-step に対して査読は1回とする。
  3. 出力ファイルの判定行を確かめてから、commit-step の前に `record-review` を実行する。
     - ステップ 2 の完了前（旧来の形）: `--decision` に出力ファイルの判定をそのまま書き、`--reason` に判定行を引用する。
     - ステップ 2 の完了後（新しい形）: `auto-dev record-review --task-id <n> --checker-output <出力> --diff-file <diff.patch> --spec-file <spec の切り出し> --maker-model <maker> --checker-model <checker> --reason "<理由>"`
  4. REJECT なら commit-step を呼ばず、手直しして 1 からやり直す。手直しした後の verify-step にも、必ず査読と record-review を対応させる。
  5. APPROVE なら `auto-dev commit-step --task-id <n> --checkpoint <checkpoint> --maker-model <maker> --checker-model <checker> --checker-reason "<reason>"`（ステップ 3 の完了後は `--checker-decision` を省略してよい）
  6. commit-step が終了コード 3 で拒否されたら、verify-step からやり直す。再検証した後も、査読と record-review をやり直す（作業ツリーが変わっていなくても省略しない）。
  7. commit-step が終了コード 4 で拒否されたら、原因（査読の記録がない、判定が REJECT、出力ファイルが書き換わった、モデルが違う）を「止まっていること・判断待ち」に書いて止まる。引数を変えて再実行しない。
- コミットした後に欠陥が見つかった場合は、`git reset` などで正規の経路の外から巻き戻さない。「止まっていること・判断待ち」に書いて止まり、修正は新しいステップとして行う。
- テストを書く途中で製品の欠陥に気づいた場合は、テスト側で回避しない。「止まっていること・判断待ち」に書いて止まる（AGENTS.md Rule 4）。
- 「既存テスト変更の理由」に書いていない既存テストが失敗した場合は、変更せずに止まって報告する。
- E2E テストで作る一時リポジトリ・worktree は、必ず `tempfile` の中に作る。`git init` には必ず `-b <ブランチ名>` を付けるか、既定のブランチに依存しない書き方をする。
- サブプロセスを起動するテストでは、`PYTHONDONTWRITEBYTECODE` と `PYTHONPYCACHEPREFIX` を環境から外して実行し、macOS と Linux で結果が変わらないようにする。
- `sys.platform` で分岐する処理を書くときは、分岐の前で変数の型を宣言する（検証スクリプトが両 platform の mypy を実行する）。
- 各ステップの検証は手元の macOS で行う。Linux での確認は人間が行う。Maker は、Linux で結果が変わりうる変更をしたときは、コミットメッセージにその旨を書く。
- Bundle を生成した後に、SPEC.md や追跡ファイルを変更しない。SPEC の完了マークは、次のフェーズの準備コミットで人間が付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
<!-- docs/contracts/phase70.md の 3 章の影響分析から作成。ここに書いていない既存テストは変更しない。どのステップでも、アサーションは変えない（下に明記したものを除く）。 -->
- ステップ 1（SBX-6）: 次の4件（`tests/test_red_verifier.py`）に限り、`subprocess.Popen` のモックが返す `stdout` の先頭に `===AUTO_DEV_RUNNER_STARTED===\n` を足す変更のみ許可する（SBX-6 の開始の目印の規則による。アサーションは変えない）。
  - `test_bwrap_command_excludes_entire_etc_and_minimizes_mounts`
  - `test_bwrap_command_includes_dir_before_bind_for_tmpfs_compatibility`
  - `test_bwrap_virtualenv_in_home_mounts_only_prefix_not_home`
  - `test_bwrap_virtualenv_in_home_subdirectory_mounts_only_bin_lib_not_entire_venv`
  - また、`tests/test_sandbox_preflight.py` の `test_linux_bwrap_unlaunchable_returns_none_and_apparmor_guidance` と `test_macos_sandbox_exec_unlaunchable_returns_none_and_guidance` に限り、(1) `patch.object(self.verifier, "_compute_nproc_limit", return_value=((500, 500), "applied: limit=500 (test)"))` で上限を固定し、(2) 引数なしの `_get_sandbox_backend()` を `_get_sandbox_backend(timeout_sec=30, nproc_limits=(500, 500))` に変えて `verify_candidate_test` と同じ値の組にすることのみ許可する（`assertEqual(len(captured), 1)` を含めアサーションは変えない）。
  - また、`tests/test_red_verifier.py` の `test_repo_tamper_detected_when_parent_repo_modified` と `test_repo_tamper_detected_when_untracked_content_tampered_in_flight` に限り、`verify_candidate_test` を呼ぶ `with` に `patch.object(self.verifier, "_get_sandbox_backend", return_value=("sandbox-exec" if sys.platform == "darwin" else "bwrap"))` を足すことのみ許可する（`popen_router`、モックの出力、アサーションは変えない）。
  - 上記以外の既存テスト（特に `test_nproc_limit_computation_and_status_recording`、`test_rejects_class_without_test_methods_ran_zero`）は変更せずそのまま通ること。
- ステップ 2（GATE-5）: `record-review` を `--decision` だけで呼んでいる次の箇所を、新しい呼び方（`review-input` で作った差分、SPEC の切り出し、判定行を含む出力ファイルを渡す）に置き換えてよい。
  - `tests/test_metrics.py`（255, 272, 339 行目付近）。272 行目付近の不正な判定（`MAYBE`）の拒否は、判定行のない出力ファイルで終了コード 4 になることを確かめる形にしてよい。
  - `tests/test_dx7_model_families.py`（322, 338, 355 行目付近）
  - `tests/test_rpt7_team_report.py`（131 行目付近）
  - `tests/test_teams_cli_e2e.py`（377 行目付近）
- ステップ 3（GATE-6）: `commit-step` の前に、査読を記録する共通のヘルパー（新規ファイル `tests/review_test_helpers.py` に置く）の呼び出しを足してよい。アサーションは変えない。対象は次のファイル。
  - `tests/test_commit_gate_boundary.py`, `tests/test_dx7_model_families.py`, `tests/test_metrics.py`, `tests/test_nightly_orchestrator.py`, `tests/test_rpt7_team_report.py`, `tests/test_team27_no_changes.py`, `tests/test_team_context_and_protection.py`, `tests/test_teams_cli_e2e.py`, `tests/test_teams_integrate.py`, `tests/test_teams_report_and_brief.py`, `tests/test_teams_report_pending_decisions.py`, `tests/test_teams_run_and_recover.py`
- ステップ 4（BDL-4）: `tests/test_bundler.py` 908 行目付近と `tests/test_dx6_demo.py` 160 行目付近の `format_version` の期待値を、2 から 3 に変えてよい。
- ステップ 6（DX-8）: `tests/test_dx6_demo.py` の `VERIFIED` の部分一致による確認（85, 176 行目付近）を完全な一致の確認に変えてよい。あわせて、デモの Checker の振る舞いの変更（独自に拒否しない）に合わせて、テストの期待を変えてよい。
- ステップ 7（RPT-8）: `tests/test_rpt7_team_report.py` のうち、`base_unchanged: True` を前提にした準備（199, 276, 355 行目付近）と、記録のないモデル名をジョブの設定から補うことを前提にしたアサーションを、新しい規則（「不明」「記録なし」）に合わせて変えてよい。
- ステップ 8（BDL-5）: `tests/test_bundler.py`（1207〜1224 行目付近）と `tests/test_reporter.py`（700 行目付近）で、worktree の Evidence の置き場所を作る準備を、記録の読み取り場所の規則（`EvidenceStore` と同じ規則）に合わせて変えてよい。アサーションは変えない。
- ステップ 9（TEAM-28）: `tests/test_teams_template_e2e.py` で、検証スクリプトが使う Python を環境変数 `PYTHON` で固定し、worktree の中の `__pycache__/*.pyc` を条件なしで確かめる形に変えてよい。
- 免疫テスト（`tests/immunity/**`）は変更しない。H1 で人間が置き換える。
- どのステップでも、変更したテスト名をコミットメッセージに列挙する。

## ステップ
- [x] 1. サンドボックスの起動の上限と失敗の分類: `RLIMIT_NPROC` をスレッド単位で数えて決める。起動の確認を実際の実行と同じ資源の上限で行う。サンドボックスの機構の起動の失敗を `NO_TESTS_RAN` ではなく `SANDBOX_UNAVAILABLE`（機構のエラーと対処を表示）とする（Red: 同じユーザーのスレッドを数百本立てた状態で、`test_red_verifier.py` が CI と同じく失敗する）（SBX-6）
- [x] 2. `auto-dev review-input`（正規の差分の書き出し）と、Checker の出力ファイルから判定を読み取り、差分・SPEC の切り出しを照合して記録する `record-review`。旧来の `--decision` だけの呼び出しは終了コード 2。`commit-step` はまだ変えない（Red-First）（GATE-5）
- [ ] 3. （H1 の後）`commit-step` が、最新の検証に対応する査読の記録・判定・出力ファイルのハッシュ・モデル・正規の差分を照合する。`checker.review` を Evidence の記録に残す（Red: H1 で置いた `test_immunity_review_evidence.py`）（GATE-6）
- [ ] 4. `run` / `teams run` / `demo` の経路の査読の証跡（GATE-7）と、Bundle への査読の証跡の収録・`verify-bundle` での照合・`format_version` 3（BDL-4）（Red-First）
- [ ] 5. 失敗ログの先頭に `Failure Phase:` と `Reason:` を書く（Red-First）（RCV-5）
- [ ] 6. デモの修正: Checker は独自に拒否しない。隔離の原因が `PROTECTED_FILES` であることを確かめる。`verify-bundle --json` の `status` の完全な一致。段階ごとの失敗のテスト（Red-First）（DX-8）
- [ ] 7. チームのジョブのレポートの修正: 基点が未変更であることは確認できたときだけ表示し、それ以外は「不明」。モデル名は記録からだけ読み、なければ「記録なし」。単一チームの集計を 1de845d の時点に戻す。`./` の接頭辞の扱い（Red-First）（RPT-8）
- [ ] 8. 記録の読み取り場所を `EvidenceStore` と同じ規則に一本化し、中身からの推測をやめる。フォールバック時は `COMPLETED` だけを成果に載せる。計画ファイルの指定経路を1つにし、`teams.json` の不正の扱いを揃える（Red-First）（BDL-5）
- [ ] 9. 雛形の受入テストで、検証スクリプトの Python を固定し、worktree の中の `.pyc` を条件なしで確かめる（TEAM-28）
- [ ] 10. `skills/nightly-run/SKILL.md` の単一チーム・チームの両経路の査読の手順を、`review-input` → 原文の SPEC の切り出し → Checker の終了と判定行の確認 → `record-review` → `commit-step` にする（CORE-12）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [ ] Phase 70 の準備コミット: SPEC.md（Phase 69 の要件を 4 章へ移し、欠陥のある4件に注記。INV-10 を追加。5.1 に Phase 70、5.2 に Phase 71 の要件）、PLAN.md、LESSONS.md、BACKLOG.md、docs/contracts/phase70.md、docs/contracts/phase70/immunity/（免疫テストの案）。
- [ ] Phase 70 の準備コミットと同時に: このリポジトリの `scripts/verify_local.sh` で、テストを動かす Python を `/usr/bin/python3` の優先から、環境変数 `PYTHON` → PATH の `python3` に変える（DX-5 の雛形と同じ規則。CI の Ubuntu では、マトリクスで指定した 3.10 / 3.12 ではなく OS の Python 3.12 でテストが走っていた）。保護ファイルなので人間が行う。
- [ ] ステップ 1 の完了後: CI の Ubuntu が通ることを確かめる。
- [ ] **H1（ステップ 2 の完了後、ステップ 3 の前）**: docs/contracts/phase70/immunity/ の3つを tests/immunity/ に置いてコミットする。
  - `test_immunity_checker_policy.py` と `test_immunity_metrics_non_interference.py` は置き換え、`test_immunity_review_evidence.py` は新規に置く。
  - 置き換えた2つは通ること、新しい1つは `commit-step` の照合の部分が Red になることを確かめる。
  - Maker は、このチェックが付くまでステップ 3 に着手しない。
- [ ] ステップ 10 の完了後:
  - Linux（Ubuntu・bwrap が動く環境）で、全テストを `AUTO_DEV_REQUIRE_SANDBOX=1` 付きで実行する。
  - CI（macOS / Ubuntu × Python 3.10 / 3.12）が通ることを確かめる。
  - Phase 70 の全タスクについて、査読の記録（Evidence の `checker.review`）と `.dev/checker/` の出力ファイルを突き合わせ、食い違いが0件であることを確かめる。

## 記録
- 2026-11-22: Phase 69 の完了後、CI の Ubuntu 24.04 で `test_red_verifier.py` の29件が失敗した（大半が `NO_TESTS_RAN`、1件で `bwrap: Creating new namespace failed: Resource temporarily unavailable`）。候補テストに設定する `RLIMIT_NPROC` をプロセス数で決めていたが、Linux の `RLIMIT_NPROC` はスレッドを数えるため、スレッドの多いランナーで上限に当たっていた。手元の Linux で同じユーザーのスレッドを 300 本立てると、同じ失敗を再現できた。あわせて、CI の Ubuntu ではテストが OS の Python 3.12 で走っていた（Traceback の `^^^` の表示から）。
- 2026-11-22: Phase 69 の完了後の確認で、Phase 67〜69 の全タスクの査読の記録と Checker の出力ファイルを突き合わせた。REJECT の出力のまま APPROVE としてコミットされたもの4件（b7af7f2, 86eeea9, e5fa287, 84a0043）と、出力より先にコミットしたもの2件（判定は一致）を見つけた。原因は、Checker のバックグラウンドの完了通知が2回届き、1回目だけを見て記録したこと（Antigravity 側の分析）。
- 2026-11-22: Phase 69 の完了後、Linux で `AUTO_DEV_REQUIRE_SANDBOX=1` 付きの全テストを実行し、800 件がすべて通った。`pip install` → `auto-dev demo` は終了コード 0 で通った。
- 2026-11-22: ad-team-trial で `teams integrate --job-id job-20261122-002709-97afe0` を実行し直し、`core` が `NO_CHANGES` として扱われ、`cli` だけが統合されて RC タグができることを確かめた（TEAM-27。main には取り込んでいない）。
- 2026-11-21: Phase 68 の完了後、Linux で全テストを実行し、746 件がすべて通った。
- 2026-11-22: REL-3（CI のマトリクス化）を行った（3eac615）。Ubuntu の mypy で検出した型エラーは、Phase 69 のステップ 1 で直した。
- 2026-11-22: 実際のエージェントによる `/nightly-run` のチーム経路を、ad-team-trial で確かめた（詳細は ad-team-trial の TRIAL.md）。
