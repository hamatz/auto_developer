# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
前のフェーズの PLAN は git の履歴で参照する（Phase 71 は 12140dd 時点の PLAN.md）。
-->

## 現在の目標
- Phase 72: 機密の検査の修正と、記録だけから作る状態のスナップショット・ダッシュボード（SPEC.md 5.1: GATE-11, STAT-1, STAT-2）
  - v0.10.0 の目的は、Auto Developer を「良い SPEC を書ける人」以外にも使えるようにすること（docs/contracts/v0.10-draft.md）。このフェーズは、その土台となる状態の表示を作る。
  - 要件ごとの影響分析は docs/contracts/phase72.md を参照する。「既存テスト変更の理由」は、この分析から作っている。
- スコープ外: Spec Compiler（Phase 73〜74）、README の更新（v0.10.0 のリリースのフェーズ）。

## 作業手順（Phase 72・LESSONS.md に基づく）
- Maker と Checker は別系統のモデルにする（`commit-step` が終了コード 4 で強制する）。使うモデルを変えたときは、この節と「記録」に書く。
  - Maker: `gemini-4-hydrogen-medium`
  - Checker: `claude-sonnet-5-5`（Anthropic API 利用。環境変数 `ANTHROPIC_API_KEY` を利用）
- **Checker の完了の通知だけでは、次に進まない。** Checker のプロセスが終わり、出力ファイルから判定が1つに決まること（GATE-5 の判定の読み取りの規則）を確かめてから記録する。
- Checker には、指摘は要点だけにして、判定（`DECISION: APPROVE` / `DECISION: REJECT` の行、または出力全体が1つの JSON）を必ず出力の最後に書くよう求める。
- Checker への入力は、要約・再構成しない。
  1. SPEC の該当節: `awk '/^### 5\.1/,/^## 6\./' SPEC.md > .dev/checker/spec_phase72.md`
  2. 差分: `auto-dev review-input --task-id <n> --out-dir .dev/checker/task<n>`
  3. Checker の出力: **`.dev/checker/review_p72_task<n>.txt`** に置く（Antigravity の作業領域ではなく、このリポジトリの中。やり直したときは `_v2` などを付け、上書きしない）。
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
<!-- docs/contracts/phase72.md の 3 章の影響分析から作成。ここに書いていない既存テストは変更しない。 -->
- どのステップでも、既存テストは変更しない。
  - ステップ 1（GATE-11）: `tests/test_gate10_secret_commit.py`、`tests/test_gate8_canonical_diff_redaction.py`、`tests/test_gate5_review_evidence.py`、`tests/immunity/test_immunity_review_evidence.py` は、そのまま通ること。
  - ステップ 2〜5（STAT-1・STAT-2）: `docs/cli.md` の作り直しは既存テストの変更ではない。`tests/test_dx4_cli_reference.py` は、作り直した `docs/cli.md` でそのまま通ること。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. 機密の検査で、Git のファイル名を引用符で囲まれない形（`-z` または `core.quotePath=false`）で読み、日本語などを含むファイル名も判定する（GATE-10 の `SECRET_FILE_IN_COMMIT`。GATE-8 の省略の表示も元のファイル名にそろえる）。種類の変更（`T`）も `SECRET_FILE_IN_COMMIT` の対象にする（Red: `鍵.pem` が `verify-step` を通る。`review-input` の省略の表示が8進数の形になる。`.env` のシンボリックリンクを通常のファイルに置き換えると通る）（GATE-11）
- [x] 2. `auto-dev status [--json]` の単一チームの部分: 計画のタスクと状態、タスクごとのイベント、計画と記録の食い違い、隔離ブランチ、独立監査、`attention[]`。既存の読み取りの関数を使う。`docs/cli.md` を作り直す（Red-First）（STAT-1）
- [x] 3. `auto-dev status [--json]` のチームの部分: ジョブの状態、チームごとの worktree・ブランチ・進み具合・`NO_CHANGES`・隔離、統合の結果（`integration/<job_id>`、`rc/<job_id>`）。worktree の記録も読む（Red-First）（STAT-1）
- [ ] 4. `auto-dev dashboard [--out FILE]`: スナップショットを埋め込んだ1枚の HTML。`attention[]` を先頭に、エスケープ、外部の読み込みなし、`schema` の確認。`docs/cli.md` を作り直す（Red-First）（STAT-2）
- [ ] 5. `auto-dev dashboard --watch [SECONDS]`: 一定の間隔でスナップショットと HTML を作り直し、HTML は自分で再読み込みする。サーバーは起動しない（Red-First）（STAT-2）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [x] Phase 72 の準備コミット: SPEC.md（Phase 71 の要件を 4 章へ移し、GATE-9 の文を直し、INV-11 と 5.1 に Phase 72 の要件）、PLAN.md、BACKLOG.md、docs/contracts/phase72.md、docs/contracts/v0.10-draft.md。
- [x] 準備のコミットの前に: Phase 71 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase71/` に退かせる。
- [ ] ステップ 1 の完了後: CI（macOS / Ubuntu × Python 3.10 / 3.12）が通ることを確かめる。
- [ ] ステップ 5 の完了後:
  - Linux で、全テストを `AUTO_DEV_REQUIRE_SANDBOX=1` 付きで実行する。CI が通ることを確かめる。
  - 査読の記録と `.dev/checker/` の出力ファイルを突き合わせ、食い違いが0件であることを確かめる。
  - Bundle を作り、`verify-bundle` が VERIFIED になることを確かめる。
  - このリポジトリと `examples/two-teams` で `auto-dev dashboard` を開き、表示を目で確かめる。

## 記録
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
