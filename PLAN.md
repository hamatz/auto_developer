# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
前のフェーズの PLAN は git の履歴で参照する（Phase 72 は 5ee1f2a 時点の PLAN.md）。
-->

## 現在の目標
- Phase 73: 夜の作業を再生できるダッシュボード（SPEC.md 5.1: STAT-8, DX-12, GATE-12, STAT-4, STAT-5, STAT-6, STAT-7, DX-10, DX-11）
  - デザインは、人間が承認した見本 docs/designs/dashboard-prototype.html を契約とする。見本と違う見た目にしない。違えたい場合は止まって相談する。画面の文言は英語。
  - 要件ごとの影響分析は docs/contracts/phase73.md を参照する。「既存テスト変更の理由」は、この分析から作っている。
- スコープ外: Spec Compiler（Phase 74 以降）、README の更新（リリースのフェーズ）。

## 作業手順（Phase 73・LESSONS.md に基づく）
- Maker と Checker は別系統のモデルにする（`commit-step` が終了コード 4 で強制する）。使うモデルを変えたときは、この節と「記録」に書く。
  - Maker: `gemini-4-hydrogen-medium`
  - Checker: `claude-sonnet-5-5`（Anthropic API 利用。環境変数 `ANTHROPIC_API_KEY` を利用）
- **Checker の完了の通知だけでは、次に進まない。** Checker のプロセスが終わり、出力ファイルから判定が1つに決まること（GATE-5 の判定の読み取りの規則）を確かめてから記録する。
- Checker には、指摘は要点だけにして、判定（`DECISION: APPROVE` / `DECISION: REJECT` の行、または出力全体が1つの JSON）を必ず出力の最後に書くよう求める。
- Checker への入力は、要約・再構成しない。
  1. SPEC の該当節: `awk '/^### 5\.1/,/^## 6\./' SPEC.md > .dev/checker/spec_phase73.md`
  2. 差分: `auto-dev review-input --task-id <n> --out-dir .dev/checker/task<n>`
  3. Checker の出力: `.dev/checker/review_p73_task<n>.txt` に置く（やり直したときは `_v2` などを付け、上書きしない）。ステップ 2（GATE-12）の完了後は、`record-review` が記録のたびに履歴を残す。
- Checker には、SPEC の要件のうち、例外・異常系・境界値の扱いを満たしているかを、差分とテストの両方で確かめるよう求める。
- verify-step の前に `git status --porcelain` を確認し、意図しないファイルがないことを確かめる。実験は `mktemp -d` の中で行う。
- `--checkpoint` にはコミットの SHA を渡す（`git rev-parse HEAD`）。
- 査読記録の順序:
  1. `auto-dev verify-step --checkpoint <SHA> --task-id <n>`
  2. Checker の査読。1回の verify-step に対して査読は1回とする。
  3. 判定が APPROVE でも REJECT でも、`auto-dev record-review --task-id <n> --checker-output <出力> --diff-file <diff.patch> --spec-file <spec の切り出し> --maker-model <maker> --checker-model <checker> --reason "<理由>"` で記録する。
  4. REJECT なら commit-step を呼ばず、手直しして 1 からやり直す。
  5. APPROVE なら `auto-dev commit-step --task-id <n> --checkpoint <SHA> --maker-model <maker> --checker-model <checker> --checker-reason "<reason>"`
  6. commit-step が終了コード 3 で拒否されたら、verify-step からやり直す。再検証した後も、査読と record-review をやり直す。
  7. commit-step が終了コード 4 で拒否されたら、原因を「止まっていること・判断待ち」に書いて止まる。引数を変えて再実行しない。
- Checker の判定が2回続けて承認にならなかったら（REJECT、または判定が読み取れない）、INV-4 に従って隔離して止まる。
- 人間の承認を PLAN.md に記録するコミットは、`git commit -m "docs(plan): ..." -- PLAN.md` の形にする。
- CLI にサブコマンドを足すステップでは、同じステップで `python scripts/gen_cli_reference.py` を実行して `docs/cli.md` を作り直す（DX-4 のテストが一致を確かめる）。
- コミットした後に欠陥が見つかった場合は、`git reset` などで正規の経路の外から巻き戻さない。「止まっていること・判断待ち」に書いて止まり、修正は新しいステップとして行う。
- テストを書く途中で製品の欠陥に気づいた場合は、テスト側で回避しない。「止まっていること・判断待ち」に書いて止まる（AGENTS.md Rule 4）。
- 「既存テスト変更の理由」に書いていない既存テストが失敗した場合は、変更せずに止まって報告する。
- E2E テストで作る一時リポジトリ・worktree は、必ず `tempfile` の中に作る。`git init` には必ず `-b <ブランチ名>` を付ける。
- サブプロセスを起動するテストでは、`PYTHONDONTWRITEBYTECODE` と `PYTHONPYCACHEPREFIX` を環境から外して実行する。
- `sys.platform` で分岐する処理を書くときは、分岐の前で変数の型を宣言する。
- 各ステップの検証は手元の macOS で行う。Linux での確認は人間が行う。
- SPEC.md はフェーズの途中で変えない。SPEC にあいまいさや矛盾を見つけたら、止まって報告する。
- Bundle を生成した後に、SPEC.md や追跡ファイルを変更しない。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
<!-- docs/contracts/phase73.md の 4 章の影響分析から作成。ここに書いていない既存テストは変更しない。 -->
- ステップ 1（STAT-8）: `tests/test_stat1_status_single.py` の準備で `audit_stage.json` を書く場所を、`.dev/` から Evidence の記録の場所（`.git/nightly_evidence/records/`）に変えてよい。アサーションは変えない。
- ステップ 2（GATE-12）: 既存テストは変更しない。`tests/test_gate5_review_evidence.py`、`tests/immunity/test_immunity_review_evidence.py`、`tests/test_bundler.py` はそのまま通ること。
- ステップ 3・4（STAT-4）: 既存テストは変更しない。`events.jsonl` の件数や中身を確かめる既存テストが、`run` の経路のイベントが増えたことで失敗した場合は、変更せずに止まって報告する。
- ステップ 5（STAT-5）: `tests/test_stat1_status_single.py` の `schema` の期待を `auto-dev-status/2` に変えてよい。
- ステップ 6（STAT-6）: `tests/test_stat2_dashboard.py` を、新しいデザインの構造に合わせて書き直してよい。ただし、1枚で完結すること（外部の読み込みがない）、文字列のエスケープ、`attention[]` が最上部にあること、`schema` の不一致の表示、`--watch` での書き直しと終了、の確認は残す。
- ステップ 7（STAT-7）: 既存テストは変更しない。`tests/immunity/test_immunity_metrics_non_interference.py` はそのまま通ること。スキルの文言を確かめている既存テストが失敗した場合は、止まって報告する。
- ステップ 8・9（DX-10・DX-11）: 既存テストは変更しない。`tests/test_dx6_demo.py` と DX-8 のテストはそのまま通ること。
- CLI を変えたステップでは `docs/cli.md` を作り直す（既存テストの変更ではない）。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. `status` の修正: 独立監査の記録を Evidence の記録の場所から読み、`AUDIT_OUTDATED` を `verify-bundle` と同じ関数で判定する。「なし」だけの判断待ちを数えない。隔離の重複をなくす。チームの失敗の数と原因を示す。`examples/two-teams/.gitignore` に CORE-11 の行をすべて入れる（Red-First）（STAT-8, DX-12）
- [x] 2. `record-review` が、記録のたびに写しと記録を `reviews/task_<id>/history/<連番>/` に残す（上書きしない。最新の記録と Bundle の中身は変えない）（Red-First）（GATE-12）
- [x] 3. `run` / `teams run` の経路で `verify_step` / `checker_review` / `commit_step` / `rollback_step` を書く。`task_start` を加える（`run` は Maker の起動の直前、低レベルの経路は `pre-flight`）。worktree のイベントにチームの ID を入れる（Red: `teams run` の後に `events.jsonl` がない）（STAT-4）
- [x] 4. 統合のイベント（`integration_start` / `integration_merge` / `integration_reverify` / `rc_tag` / `integration_failed`）を書く（Red-First）（STAT-4）
- [x] 5. スナップショットに `timeline[]`（行数つき）と `backlog` と始まり・終わりの時刻を加え、`schema` を `auto-dev-status/2` にする（Red-First）（STAT-5）
- [x] 6. `auto-dev dashboard` を、見本 docs/designs/dashboard-prototype.html と同じ構成・動きの再生できる HTML にする（単一チームの形、空の時系列、10 分を超える空白の圧縮を含む）（Red-First）（STAT-6）
- [ ] 7. `auto-dev dashboard --enable / --disable`（`.dev/dashboard.json`）、`run` / `teams run` の `--dashboard`、イベントを書くたびの作り直し（失敗は警告だけ）、`/nightly-run` と `/nightly-setup` のスキルの選択肢。`docs/cli.md` を作り直す（Red-First）（STAT-7）
- [ ] 8. `auto-dev demo --teams [--keep]`: デモ用の Maker と Checker で2チームを並行して実行し（一方のタスクで検証に一度失敗して2回目で直る）、統合して RC タグを作り、ダッシュボードの場所を表示する。`docs/cli.md` を作り直す（Red-First）（DX-10）
- [ ] 9. `scripts/record_dashboard.py`: ダッシュボードを再生して GIF に書き出す開発用のスクリプト。必要なライブラリがなければ、入れ方を表示して終了コード 2（Red-First）（DX-11）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [x] Phase 73 の準備コミット: SPEC.md（Phase 72 の要件を 4 章へ移し、4.11 STAT を新設。5.1 に Phase 73 の要件）、PLAN.md、BACKLOG.md、docs/contracts/phase73.md、docs/designs/dashboard-prototype.html。
- [x] 準備のコミットの前に: Phase 72 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase72/` に退かせる。
- [ ] ステップ 6 の完了後: このリポジトリと `examples/two-teams` のダッシュボードを開き、見本と比べて目で確かめる。
- [ ] ステップ 9 の完了後:
  - Linux で、全テストを `AUTO_DEV_REQUIRE_SANDBOX=1` 付きで実行する。CI が通ることを確かめる。
  - 査読の記録（履歴を含む）と Checker の出力を突き合わせ、食い違いが0件であることを確かめる。
  - Bundle を作り、`verify-bundle` が VERIFIED になることを確かめる。
  - `auto-dev demo --teams` を実行し、`scripts/record_dashboard.py` で GIF を書き出せることを確かめる。

## 記録
- 2026-11-23: Phase 72 の完了後の確認。5 ステップすべて完了し、止まったステップはなかった。査読の記録の突き合わせで食い違いは0件。Linux で 874 件が通った（既知の不安定な2件を除く）。Bundle は VERIFIED（`AUDIT_OUTDATED` は 11/23 の監査の後のコードのため）。実際に使うと、`status` が独立監査の記録を誤った場所から読む（監査が「ない」と表示される）、判断待ちの「なし。」を HIGH として数える、などの不具合があった（STAT-8）。Checker の出力は、今回も Antigravity の作業領域に置かれた（GATE-12 で履歴を残す形にした）。
- 2026-11-23: ダッシュボード v2 の見本（docs/designs/dashboard-prototype.html）を、`examples/two-teams` で実際に `teams run` を実行した記録から作り、人間が承認した（backlog のチケットが減り、コミットが積み上がる流れを含む。画面は英語）。見本を作る途中で、`run` / `teams run` の経路がイベントを書いていないこと（STAT-4）と、サンプルの `.gitignore` の不足（DX-12）が見つかった。
- 2026-11-23: v0.9.0 をリリースした（タグ `v0.9.0`）。v0.10 の方針を docs/contracts/v0.10-draft.md にまとめ、7 章の5点を提案どおりに決めた。
- 2026-11-23: リリース前の独立監査（`claude-opus-5-5`、LIVE、起点 7ea39c0、対象 f98c251、入力約 25 万トークン）。P0・P1 なし、P2 の2件（AUD-1004、AUD-1005）を BACKLOG に入れた。監査の後の Bundle（`bundle_job-1795428778.zip`）は VERIFIED、警告なし（`audit_stage_verified: true`）。
- 2026-11-23: Phase 71 の完了後の確認。10 ステップすべて完了し、止まったステップはなかった。査読の記録と出力の写しの突き合わせで食い違いは0件（Checker は JSON の判定で返した）。Linux で `AUTO_DEV_REQUIRE_SANDBOX=1` 付きの全テスト 866 件が通った（既知の不安定な2件を除く）。Bundle は VERIFIED（警告は `AUDIT_OUTDATED` のみ）。Maker は `gemini-4-hydrogen-medium`、Checker は `claude-sonnet-5-5` だった（PLAN の記載と違う）。Checker の出力は `.dev/checker/` ではなく Antigravity の作業領域に置かれた（正規の写しは `.git/nightly_evidence/reviews/`）。
- 2026-11-23: Phase 71 の後の CI は、macOS・Ubuntu とも DX-1 の4件だけが失敗した（`auto-dev: command not found`）。CI がパッケージをインストールしていなかったため。`.github/workflows/verify.yml` に `pip install -e .` を加えた（人間の変更）。SBX-7・SBX-8 の失敗は解消した。
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
