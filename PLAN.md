# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
前のフェーズの PLAN は git の履歴で参照する（Phase 67 は b7af7f2 時点の PLAN.md）。
-->

## 現在の目標
- Phase 68: Linux を macOS と同格に扱う・Phase 67 確認で見つかった欠陥の修正（SPEC.md 5.1: SBX-3, SBX-4, SBX-5, BDL-3, TEAM-25, TEAM-26, RPT-5, DX-2, REL-3）
  - 開発は Mac で行うが、対応 OS を Mac に絞らない。Linux でも全テストが通り（サンドボックスのテストをスキップで済ませない）、利用者がつまずいたときに対処がわかる状態にする。
  - 要件ごとの影響分析は docs/contracts/phase68.md を参照する。「既存テスト変更の理由」は、この分析から作っている。
- スコープ外: README の書き直し、DX-1 / DX-3 / DX-4 / REL-2（Phase 69）。新しい機能の追加。

## v0.9.0 の残り
- Phase 69: 準備コミットで README.md（日本語）と README.en.md を先に書く。そのうえで DX-1（README の手順の実行テスト）、DX-3（`examples/two-teams/`）、DX-4（CLI リファレンスの生成）、REL-2（0.9.0 表記）を行い、CONTRIBUTING.md、独立監査、v0.9.0 のタグで終える。

## 作業手順（Phase 68・LESSONS.md に基づく）
- Maker と Checker は別系統のモデルにする（`commit-step` が終了コード 4 で強制する）。
  - Maker: Gemini 3.8 Flash (`gemini-3.8-flash`)
  - Checker: Claude Opus 5.5 (`claude-opus-5-5-medium`、Anthropic API 利用。環境変数 `ANTHROPIC_API_KEY` を利用)
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる。
  1. `awk '/^### 5\.1/,/^### 5\.2/' SPEC.md > .dev/checker/spec_phase68.md`
     （不変条件も必要なときは `awk '/^## 3\./,/^## 4\./' SPEC.md` を追記する）
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイルがないことを確かめる。実験は `mktemp -d` の中で行う。
- 査読記録の順序:
  1. `auto-dev verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker の査読（APPROVE / REJECT）。1回の verify-step に対して査読は1回とする。
  3. 査読の直後、commit-step の前に `auto-dev record-review --task-id <n> --decision <decision> --checker-model <checker> --maker-model <maker> --reason "<reason>"`
  4. REJECT なら commit-step を呼ばず、手直しして 1 からやり直す。手直しした後の verify-step にも、必ず査読と record-review を対応させる。
  5. APPROVE なら `auto-dev commit-step --task-id <n> --checkpoint <checkpoint> --maker-model <maker> --checker-model <checker> --checker-decision APPROVE --checker-reason "<reason>"`
  6. commit-step が終了コード 3（Evidence の不一致）で拒否されたら、verify-step からやり直す。再検証した後も、査読と record-review をやり直す（作業ツリーが変わっていなくても省略しない）。
- コミットした後に欠陥が見つかった場合は、`git reset` などで正規の経路の外から巻き戻さない。「止まっていること・判断待ち」に書いて止まり、修正は新しいステップとして行う。
- テストを書く途中で製品の欠陥に気づいた場合は、テスト側で回避しない。「止まっていること・判断待ち」に書いて止まる（AGENTS.md Rule 4）。
- 「既存テスト変更の理由」に書いていない既存テストが失敗した場合は、変更せずに止まって報告する。
- E2E テストで作る一時リポジトリ・worktree は、必ず `tempfile` の中に作る。`git init` には必ず `-b <ブランチ名>` を付けるか、既定のブランチに依存しない書き方をする。
- サブプロセスを起動するテストでは、`PYTHONDONTWRITEBYTECODE` と `PYTHONPYCACHEPREFIX` を環境から外して実行し、macOS と Linux で結果が変わらないようにする。
- 各ステップの検証は手元の macOS で行う。Linux での確認は人間が行う（「人間が行うこと」を参照）。Maker は、Linux で結果が変わりうる変更（OS のコマンド・パス・例外の種類に依存する処理）をしたときは、コミットメッセージにその旨を書く。
- Bundle を生成した後に、SPEC.md や追跡ファイルを変更しない。SPEC の完了マークは、次のフェーズの準備コミットで人間が付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
<!-- docs/contracts/phase68.md の影響分析から作成。ここに書いていない既存テストは変更しない。 -->
- ステップ 1（SBX-4）: 次の3件に限り、OS に依存しない期待値に更新してよい。
  - `tests/test_red_verifier.py` の `test_repo_tamper_detected_when_parent_repo_modified` と `test_repo_tamper_detected_when_untracked_content_tampered_in_flight`: `chflags` の呼び出しを、`shutil.which("chflags")` が見つかる場合だけに限る。それ以外のアサーションは変更しない。
  - `tests/test_independent_audit_runner.py` の `test_criterion_1_execution_isolation`: `assertIn("PermissionError", result.output)` を、次の2つの確認に置き換える。それ以外のアサーションは変更しない。
    - 機密の値（`secret_12345`）が `result.output` に現れないこと。
    - `PermissionError` または `FileNotFoundError` のどちらかが出力に含まれること。
- ステップ 1（SBX-4）: サンドボックスが使えないときにスキップする、またはモックに切り替えるテスト（`tests/test_live_claude_audit.py` 99 行目のモックへの切り替えなど）について、その判定を `AUTO_DEV_REQUIRE_SANDBOX=1` のときは失敗にする共通の仕組みに置き換えてよい。テストの本体は変更しない。
- ステップ 4（BDL-3）: 次のテストは、番号つきのタスクと記録が混在するため影響する可能性がある。失敗した場合に限り、新しい規則（計画で完了したタスクの記録だけが対象）に合わせて、テストの準備（計画ファイルの内容）だけを更新してよい。アサーションは変更しない。
  - `tests/test_multi_team.py`
  - `tests/test_teams_integrate.py`
  - `tests/test_reporter.py`
  - `tests/test_nightly_orchestrator.py`
- ステップ 8（DX-2）: 外部コマンドを指定し、モデル名なしで `run` / `teams run` を呼んでいる次の箇所に限り、`--maker-model gemini-3.8-flash --checker-model claude-opus-5-5-medium` を追加してよい。それ以外の引数やアサーションは変更しない。
  - `tests/test_team_context_and_protection.py`: 354, 365, 377, 394, 405, 415, 434, 446 行目付近の呼び出し
  - `tests/test_teams_run_and_recover.py`: 286, 317 行目付近の呼び出し
  - `tests/test_teams_cli_e2e.py`: 307 行目付近の `teams run` の呼び出し（設定エラーの検査。終了コード 2 の理由がモデル名の省略に変わらないようにするため）
  - `tests/test_multi_team.py`: 690, 695 行目付近の `extra_args`
  - `tests/test_nightly_orchestrator.py`: `test_external_project_workflow`（758 行目付近の `run` 呼び出し）
- 上記以外の既存テストは変更しない。免疫テスト（`tests/immunity/**`）は変更しない。
- どのステップでも、変更したテスト名をコミットメッセージに列挙する。

## ステップ
- [x] 1. OS に依存しない期待値: Linux で失敗する既存の3件を、守るべき性質で書き直す。`AUTO_DEV_REQUIRE_SANDBOX=1` のときに、サンドボックスのテストのスキップやモックへの切り替えを失敗にする共通の仕組みを用意する（SBX-4）
- [x] 2. サンドボックスの実働確認: 最小のコマンドで実際に起動できるかを判定してキャッシュし、起動できなければ OS ごとの対処を含む `SANDBOX_UNAVAILABLE` を返す（Red-First）（SBX-3）
- [x] 3. Linux の bwrap の中で実際に実行し、ホストの機密ファイル・許可していないパス・ホームディレクトリ・元のリポジトリが読めないことを確かめるテストを追加する（macOS では、理由を示してスキップしてよい）（SBX-5）
- [x] 4. Bundle とレポートに入れる記録を、対象の計画で完了したタスクのものに限り、対象外の記録を manifest の `excluded_records` に残す（Red-First）（BDL-3）
- [x] 5. `teams init` が `base.ref` に現在のブランチ名を書く（コミットのないリポジトリ・detached HEAD を含む）（Red-First）（TEAM-25）
- [x] 6. 統合に成功したジョブの `teams cleanup` で `integration/<job_id>` を削除する（RC タグは残し、失敗・未完了のジョブでは削除しない）（Red-First）（TEAM-26）
- [x] 7. チームのジョブの「判断待ちの事項」に、そのジョブで判断すべきことだけを表示する（Red-First）（RPT-5）
- [ ] 8. 外部コマンドを指定してモデル名を省略した `run` / `teams run` を終了コード 2 で拒否する（`--mock` は従来どおり）（Red-First）（DX-2）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [ ] Phase 68 の準備コミット: SPEC.md（Phase 67 の要件を 4 章へ移し、5.1 に Phase 68、5.2 に Phase 69 の要件）、PLAN.md、LESSONS.md、docs/contracts/phase68.md。
- [ ] ステップ 3 の完了後とステップ 8 の完了後: Linux（Ubuntu・bwrap が動く環境）で全テストを `AUTO_DEV_REQUIRE_SANDBOX=1` 付きで実行し、結果をここに記録する。
- [ ] ステップ 8 の完了後: `.github/workflows/verify.yml` を macOS / Ubuntu 24.04 × Python 3.10 / 3.12 のマトリクスにし、Linux で bubblewrap の AppArmor プロファイルを準備する（REL-3。変更の案は docs/contracts/phase68.md の 5 章）。GitHub Actions で通ることを確認してから、必須チェックに設定する。
- [ ] Phase 66 から持ち越し: 実際のエージェント（Antigravity / Claude Code など）で `/nightly-run` のチーム経路を一度通して、動作を確認する。
- [ ] フェーズの準備のたびに: 前のフェーズの Evidence の記録が Bundle に混ざっていないことを確認する（BDL-3 の実装が終わるまでの暫定措置。Phase 67 では Phase 66 の task 8 を `.git/nightly_evidence/archive/phase66/` へ退避した）。
