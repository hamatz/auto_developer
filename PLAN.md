# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
前のフェーズの PLAN は git の履歴で参照する（Phase 73 は 441a82e 時点の PLAN.md）。
-->

## 現在の目標
- Phase 74: 手直し（Phase 73 の振り返り）（SPEC.md 5.1: STAT-9, STAT-10, GATE-13, DX-13, RCV-6, DX-14）
  - 要件ごとの影響分析と設計の判断は docs/contracts/phase74.md を参照する。「既存テスト変更の理由」は、この分析から作っている。
  - ダッシュボードの見た目は、Phase 73 の見本 docs/designs/dashboard-prototype.html から変えない（STAT-10 の細部の修正だけ）。
- スコープ外: Spec Compiler（Phase 75 以降）、README の更新（リリースのフェーズ）、ツールが Checker を起動する低レベルのコマンド（IDEA-020 の残り）。

## 作業手順（Phase 74・LESSONS.md に基づく）
- Maker と Checker は別系統のモデルにする（`commit-step` が終了コード 4 で強制する）。使うモデルを変えたときは、この節と「記録」に書く。
  - Maker: `gemini-4-hydrogen-medium`
  - Checker: `claude-sonnet-5-5`（Anthropic API 利用。環境変数 `ANTHROPIC_API_KEY` を利用）
- **検証の制限時間**: このリポジトリの検証は macOS で約 8 分かかる。`verify-step` は、全体のオプションを使って `auto-dev --timeout 1200 verify-step --checkpoint <SHA> --task-id <n>` の形で実行する（`--timeout` はサブコマンドより前に書く）。ステップ 6（DX-13）の完了後は、検証スクリプトの先頭の行（`# auto-dev: verify-timeout=1200`）が使われるので、`--timeout` は省いてよい。
- **Checker の完了の通知だけでは、次に進まない。** Checker のプロセスが終わり、出力ファイルから判定が1つに決まること（GATE-5 の判定の読み取りの規則）を確かめてから記録する。
- Checker には、指摘は要点だけにして、判定（`DECISION: APPROVE` / `DECISION: REJECT` の行、または出力全体が1つの JSON）を必ず出力の最後に書くよう求める。Checker の出力の上限（`max_tokens`）は 16384 にする（4096 では、考える部分と合わせて出力が途中で切れることがあった）。
- Checker への入力は、要約・再構成しない。
  1. SPEC の該当節: `awk '/^### 5\.1/,/^## 6\./' SPEC.md > .dev/checker/spec_phase74.md`
  2. 差分: `auto-dev review-input --task-id <n> --out-dir .dev/checker/p74_task<n>`（やり直したときは `_v2` などを付ける）
  3. Checker の出力: `.dev/checker/review_p74_task<n>.txt` に置く（やり直したときは `_v2` などを付け、上書きしない）。
- Checker には、SPEC の要件のうち、例外・異常系・境界値の扱いを満たしているかを、差分とテストの両方で確かめるよう求める。ダッシュボードの見た目（STAT-10）は人間が目で確かめるので、JavaScript を実行するテストは求めないよう、Checker に伝える（SPEC の STAT-10 にも書いてある）。
- verify-step の前に `git status --porcelain` を確認し、意図しないファイルがないことを確かめる。実験は `mktemp -d` の中で行う。
- `--checkpoint` にはコミットの SHA を渡す（`git rev-parse HEAD`）。
- 査読記録の順序（**この順番を崩さない**）:
  1. `auto-dev --timeout 1200 verify-step --checkpoint <SHA> --task-id <n>`
  2. 検証が通った後に、`review-input` で差分を作る。**検証の後に作業ツリーを変えたら、1 からやり直す。** 検証の前に `review-input` や Checker を実行しない（Phase 73 のタスク 5 では、検証の前に査読を行い、その出力を記録しなかった）。
  3. Checker の査読。**1回の verify-step に対して、Checker は1回だけ実行する。** 出力が途中で切れて判定が読み取れない場合だけ、その出力ファイルを残したまま（上書きしない）、1回だけ実行し直してよい。そのことを「記録」に書く。
  4. Checker の出力は、判定が APPROVE でも REJECT でも、判定が読み取れなくても、必ず `auto-dev record-review --task-id <n> --checker-output <出力> --diff-file <diff.patch> --spec-file <spec の切り出し> --maker-model <maker> --checker-model <checker> --reason "<理由>"` に渡す（判定が読み取れない場合は終了コード 4 になる。ステップ 4 の完了後は、それも履歴に残る）。記録しない Checker の出力を作らない。
  5. REJECT なら commit-step を呼ばず、手直しして 1 からやり直す。
  6. APPROVE なら `auto-dev commit-step --task-id <n> --checkpoint <SHA> --maker-model <maker> --checker-model <checker> --checker-reason "<reason>"`
  7. commit-step が終了コード 3 で拒否されたら、verify-step からやり直す。再検証した後も、査読と record-review をやり直す。
  8. commit-step が終了コード 4 で拒否されたら、原因を「止まっていること・判断待ち」に書いて止まる。引数を変えて再実行しない。
- Checker の判定が2回続けて承認にならなかったら（REJECT、または実行し直しても判定が読み取れない）、INV-4 に従って隔離して止まる。
- 隔離したときは、隔離ブランチの差分だけでなく、スナップショット（`.dev/quarantines/snapshot_task_<n>_*.tar.gz`）にだけある新しいファイル（テストなど）も、報告に書く（ステップ 7 の完了後は、`rollback-step` が一覧を表示する）。
- 人間の承認を PLAN.md に記録するコミットは、`git commit -m "docs(plan): ..." -- PLAN.md` の形にする。**ステップの途中（verify-step の後、commit-step の前）には PLAN.md を変えない**（Evidence の Tree SHA と合わなくなる）。
- CLI の引数を変えるステップでは、同じステップで `python scripts/gen_cli_reference.py` を実行して `docs/cli.md` を作り直す（DX-4 のテストが一致を確かめる）。
- コミットした後に欠陥が見つかった場合は、`git reset` などで正規の経路の外から巻き戻さない。「止まっていること・判断待ち」に書いて止まり、修正は新しいステップとして行う。
- テストを書く途中で製品の欠陥に気づいた場合は、テスト側で回避しない。「止まっていること・判断待ち」に書いて止まる（AGENTS.md Rule 4）。
- 「既存テスト変更の理由」に書いていない既存テストが失敗した場合は、変更せずに止まって報告する。
- E2E テストで作る一時リポジトリ・worktree は、必ず `tempfile` の中に作る。`git init` には必ず `-b <ブランチ名>` を付ける。
- サブプロセスを起動するテストでは、`PYTHONDONTWRITEBYTECODE` と `PYTHONPYCACHEPREFIX` を環境から外して実行する。
- テストでは、Python 3.10 にないモジュール（`tomllib` など）を使わない（CI は 3.10 と 3.12 で動く）。
- `sys.platform` で分岐する処理を書くときは、分岐の前で変数の型を宣言する。
- 各ステップの検証は手元の macOS で行う。Linux での確認は人間が行う。
- SPEC.md はフェーズの途中で変えない。SPEC にあいまいさや矛盾を見つけたら、止まって報告する。
- Bundle を生成した後に、SPEC.md や追跡ファイルを変更しない。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
<!-- docs/contracts/phase74.md の 4 章の影響分析から作成。ここに書いていない既存テストは変更しない。 -->
- ステップ 1（STAT-9）: `tests/test_stat2_dashboard.py` で、1秒未満の間隔のイベントを使っていて、最小の間隔のために `t` や `duration` の値が変わるものだけ、期待の値を変えてよい。10 分の境界（600 秒は圧縮しない、601 秒は圧縮する）の確認は変えない。
- ステップ 2（STAT-10）: 既存テストは変更しない。
- ステップ 3（GATE-13 の作業ツリーの一致）: `verify-step` の後に作業ツリーを変えてから `review-input` / `record-review` を呼んでいる既存テストは、その変更を `verify-step` の前に移してよい。アサーションは変えない。
- ステップ 4（GATE-13 の1回の検証に1回の査読）: 同じ検証に対して、判定の決まった `record-review` を2回以上呼んでいる既存テスト（`tests/test_dx7_model_families.py` の `test_record_review_records_family_source_and_rejects_mismatch` など）は、記録の間に `verify-step` を挟むか、テストを分けてよい。確かめる内容は変えない。判定の決まらない出力が履歴に残るようになったことで、履歴の件数の期待が変わるテストは、その件数だけ変えてよい。
- ステップ 5（GATE-13 のイベントと表示）: 既存テストは変更しない。`events.jsonl` の件数を確かめる既存テストや、スキルの文言を確かめる既存テストが失敗した場合は、止まって報告する。
- ステップ 6・7・8（DX-13・RCV-6・DX-14）: 既存テストは変更しない。打ち切りのメッセージの1行目、`rollback-step` の出力の1行目、失敗ログの先頭の2行は変えない。
- CLI を変えたステップでは `docs/cli.md` を作り直す（既存テストの変更ではない）。
- 免疫テスト（`tests/immunity/**`）は変更しない。ステップ 3・4 で免疫テストが失敗した場合は、止まって報告する。

## ステップ
- [x] 1. ダッシュボードの再生で、続くイベントの間を最低 1 秒空ける（同じ時刻は記録の順）。`demo --teams` の再生の長さが、イベントの数から 1 を引いた秒数以上になる（Red-First）（STAT-9）
- [x] 2. ダッシュボードの細部: 空白の印の文字が重ならないようにする。今の時系列の隔離と、それより前の隔離を区別して示す（Red-First）（STAT-10）
- [x] 3. `review-input` と `record-review` は、最新の Evidence の Tree SHA が今の作業ツリーと一致しなければ終了コード 3（Red: 検証の後に作業ツリーを変えても `review-input` が通る）（GATE-13）
- [x] 4. `record-review` は、同じ検証に対して判定の決まった査読を1回だけ記録する（2回目は終了コード 4）。判定の決まらない出力も履歴に残す（Red: REJECT の記録の後に、同じ検証に APPROVE を記録できる）（GATE-13）
- [x] 5. `review-input` がイベント `review_input` を書く。`status` が `REVIEW_NOT_RECORDED` を `attention[]` に入れる。`/nightly-run` のスキルに「1回の入力に Checker は1回、出力は必ず記録する」を書く（Red-First）（GATE-13）
- [x] 6. 検証の制限時間: `verify-step --timeout`、検証スクリプトの `# auto-dev: verify-timeout=<SEC>` の行、既定 300 秒の順で決める（`run`・`teams run` も同じ）。打ち切りのときに出所と延ばし方を示す。スキルに書く。`docs/cli.md` を作り直す（Red-First）（DX-13）
- [ ] 7. 隔離のあと、スナップショットにだけある新しいファイルの一覧と取り出すコマンドを表示し、失敗ログにも書く（Red: 新しいテストファイルを作ったタスクの `rollback-step` が、そのファイルを示さない）（RCV-6）
- [ ] 8. `scripts/check_doc_commands.py`: README とスキルの `auto-dev` のコマンドを、CLI の引数定義と突き合わせる（Red-First）（DX-14）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [ ] Phase 74 の準備コミット: SPEC.md（Phase 73 の要件を 4 章へ移し、STAT-7・STAT-8・RCV-1 の文言を直す。5.1 に Phase 74 の要件）、PLAN.md、BACKLOG.md、docs/contracts/phase74.md、`scripts/verify_local.sh`（制限時間の行）。
- [x] 準備のコミットの前に: Phase 73 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase73/` に退かせる。
- [ ] ステップ 2 の完了後: このリポジトリと `demo --teams` のダッシュボードを開き、目で確かめる（空白の印の文字、隔離の区別）。
- [ ] ステップ 8 の完了後:
  - Linux で、全テストを `AUTO_DEV_REQUIRE_SANDBOX=1` 付きで実行する。CI（macOS・Ubuntu × Python 3.10・3.12）が通ることを確かめる。
  - 査読の記録（判定の決まらない出力の履歴を含む）と Checker の出力ファイルを突き合わせ、記録されていない出力が0件であることを確かめる。
  - Bundle を作り、`verify-bundle` が VERIFIED になることを確かめる。
  - `auto-dev demo --teams --keep` のダッシュボードを `scripts/record_dashboard.py` で GIF に書き出し、30 コマ以上になることを確かめる。

## 記録
- 2026-11-25: ステップ 4（GATE-13 の1回の検証に1回の査読）の verify-step で、免疫テスト `tests/immunity/test_immunity_checker_policy.py` の3件が失敗して止まった。このテストは、拒否の後に同じ検証へ APPROVE を記録し直してコミットできることを確かめていた。これは GATE-13 が塞ぐ抜け道そのもの（`test_rejected_review_cannot_be_committed` は、REJECT の記録の後に APPROVE を記録し直していた）。契約書 4.3 の「免疫テストはどれも1回の検証に1回の記録」という分析は誤りだった。人間がテストを変えた: 拒否の後は、Evidence が消費されていないこと（`review-input` が通る）と、記録し直しが終了コード 4 で拒否されることを確かめ、検証からやり直せばコミットできることを確かめる。このコミットの時点では、ステップ 4 が入るまでこの3件は失敗する（Red）。ステップ 4 は作業ツリーに残したまま、新しい HEAD を checkpoint にして verify-step からやり直す。
- 2026-11-25: Phase 74 の準備。Spec Compiler の前に、Phase 73 の振り返りの手直し（BACKLOG の IDEA-020〜025 の一部）を1フェーズで行うことを人間が決めた。Spec Compiler は Phase 75 以降。SPEC の Phase 73 の要件を 4 章へ移すときに、フェーズの途中で決めた解釈（STAT-8 の `OK` 以外はすべて注意、STAT-7 の1つのコマンドにつき1回の警告）を文言に入れ、RCV-1 の文言を実際の挙動（追跡しているファイルの変更だけを隔離ブランチに、それ以外はスナップショットに）に合わせた。
- 2026-11-24: Phase 73 の完了後の確認。9 ステップすべて完了。ステップ 3・6・7 は REJECT が2回続いて隔離され、隔離から取り込んでやり直した。ステップ 2 以外はすべて1回目が REJECT だった。保護ファイルの変更なし。Linux で `AUTO_DEV_REQUIRE_SANDBOX=1` 付きの全テスト 934 件のうち、失敗は既知の SIGKILL の2件（IDEA-011）と、`tests/test_dx11_record_dashboard.py` が Python 3.10 にない `tomllib` を読み込む1件。後者は人間が直した（3.10 ではその1件を飛ばす。CI の 3.12 で確かめる）。Bundle は VERIFIED（`AUDIT_OUTDATED` の警告はリリース前の監査で解消する）。`auto-dev demo --teams --keep` は終了コード 0 で、2チーム・検証の失敗1回・統合・RC タグまで通った。ただし全体が約1秒で終わり、時刻が秒単位なので、再生は1秒、GIF は3コマにしかならない（IDEA-023）。このリポジトリの記録からは 64 コマの GIF が書き出せた。
- 2026-11-24: 査読の記録の突き合わせで食い違いが1件。タスク 5 の2回目の Checker の出力 `review_p73_task5_v2.txt`（REJECT）が記録されず、同じ差分（`diff.patch` が一致）に対して約2時間後に実行し直した `v2b`（APPROVE）が記録されてコミットされた（d09edc4）。`v2` は2回目の `verify-step` より前に実行されていて、「検証結果が空」も理由の1つだった。手順の誤り（査読を検証より先に行った）と、記録しない査読があったことが重なった。後続のステップ 6〜9 が依存しているので、コミットは戻さない。`v2` の指摘 2〜4 は、ステップ 6 以降の実装と人間の確認で問題がないことを確かめた。再発の防止は IDEA-020。タスク 6 の `v2`・`v2b` は出力が途中で切れて判定の行がなかった（IDEA-013 と同じ）。
- 2026-11-24: フェーズの途中の判断: `verify-step` のタイムアウトは、全体のオプション `auto-dev --timeout 1200 verify-step ...` で回避した（IDEA-021）。ステップ 1 の `AUDIT_OUTDATED` は、`check_audit_diff_status` が `"OK"` 以外ならすべて注意として出すと解釈した。ステップ 7 の「1回だけ警告」は、利用者が打った1つのコマンドにつき1回と解釈した。どちらも Phase 74 の準備で SPEC の文言を直す。
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
