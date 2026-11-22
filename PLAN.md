# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
前のフェーズの PLAN は git の履歴で参照する（Phase 68 は 1de845d 時点の PLAN.md）。
-->

## 現在の目標
- Phase 69: 初めての利用者が自分の環境とモデルで試せるようにする（SPEC.md 5.1: REL-4, TEAM-27, RPT-6, RPT-7, DX-5, DX-6, DX-7）
  - Phase 68 の CI と ad-team-trial での試行で見つかった欠陥を直す。
  - `auto-dev demo` と、モデルの系統の明示を作る。
  - 要件ごとの影響分析は docs/contracts/phase69.md を参照する。「既存テスト変更の理由」は、この分析から作っている。
- スコープ外: README の書き直し、DX-1 / DX-3 / DX-4 / REL-2（Phase 70）。

## v0.9.0 の残り
- Phase 70: 準備コミットで README.md（日本語）と README.en.md を先に書く。クイックスタートの中心は `pip install` → `auto-dev demo`。そのうえで DX-1、DX-3、DX-4、REL-2 を行い、CONTRIBUTING.md、独立監査、v0.9.0 のタグで終える。

## 作業手順（Phase 69・LESSONS.md に基づく）
- Maker と Checker は別系統のモデルにする（`commit-step` が終了コード 4 で強制する）。
  - Maker: Gemini 3.8 Flash (`gemini-3.8-flash`)
  - Checker: Claude Opus 5.5 (`claude-opus-5-5-medium`、Anthropic API 利用。環境変数 `ANTHROPIC_API_KEY` を利用)
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる。
  1. `awk '/^### 5\.1/,/^### 5\.2/' SPEC.md > .dev/checker/spec_phase69.md`
     （不変条件も必要なときは `awk '/^## 3\./,/^## 4\./' SPEC.md` を追記する）
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
  3. Checker の出力も `.dev/checker/review_task<n>.txt` に保存する（BACKLOG IDEA-007 の仕組みができるまでの手順）。
- Checker には、SPEC の要件のうち、例外・異常系・境界値の扱いを満たしているかを、差分とテストの両方で確かめるよう求める（ad-team-trial のシナリオ2で、例外が外に出る実装が APPROVE された）。
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
- `sys.platform` で分岐する処理を書くときは、分岐の前で変数の型を宣言する（REL-4。検証スクリプトが両 platform の mypy を実行する）。
- 各ステップの検証は手元の macOS で行う。Linux での確認は人間が行う（「人間が行うこと」を参照）。Maker は、Linux で結果が変わりうる変更（OS のコマンド・パス・例外の種類に依存する処理）をしたときは、コミットメッセージにその旨を書く。
- Bundle を生成した後に、SPEC.md や追跡ファイルを変更しない。SPEC の完了マークは、次のフェーズの準備コミットで人間が付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
<!-- docs/contracts/phase69.md の影響分析から作成。ここに書いていない既存テストは変更しない。 -->
- ステップ 2（DX-5）:
  - `tests/test_teams_template_e2e.py` の 149 行目付近に限り、検証スクリプトが使う Python を `/usr/bin/python3` と決め打ちしている箇所を、新しい規則（環境変数 `PYTHON`、なければ PATH の `python3`）で選ばれる Python に合わせて更新してよい。テストが確かめている内容（バイトコードが worktree の中に生成される状態で `teams run` → `teams cleanup` → 次の `teams prepare` が通ること）は変えない。
  - `tests/test_multi_team.py` の `test_run_workers_builds_correct_cli_command_order_and_arguments`（210 行目付近）に限り、`expected_cmd_prefix` の先頭要素 `"python3"` を `sys.executable` に更新してよい（他の要素・引数の順序・`team.env` の受け渡しアサーションは変更しない）。
- 上記以外の既存テストは変更しない。免疫テスト（`tests/immunity/**`）は変更しない。
- 特に次の既存テストは、新しい規則でも変更せずに通ること（守るべき挙動）。
  - TEAM-27: `tests/test_teams_integrate.py` の `test_integrate_rejects_missing_evidence_audit_failure_and_keeps_worktrees`
  - DX-7: `tests/immunity/test_immunity_checker_policy.py`、`tests/test_audit_model_policy.py` の不明な系統の拒否
- どのステップでも、変更したテスト名をコミットメッセージに列挙する。

## ステップ
- [x] 1. 型検査の両 platform 対応: `verifier.py` の `sys.platform` の分岐の前で型を宣言し、`mypy --platform linux` / `--platform darwin` の両方を通す（Red: 検証スクリプトの Linux 側の mypy が失敗している）（REL-4）
- [x] 2. 雛形の検証スクリプトの Python の選択（環境変数 `PYTHON` → PATH の `python3`）と、`src/` があれば `PYTHONPATH` に加える処理。ワーカーの既定の起動を `sys.executable` にする（Red-First）（DX-5）
- [x] 3. `auto-dev run` で、成功時・失敗時とも、レポートを書いてから Bundle を作る（Red-First）（RPT-6）
- [ ] 4. 基点からのコミットが0件のチームを `NO_CHANGES` として統合の対象から外す。全チームが `NO_CHANGES` なら RC タグを作らずに終了コード 0。`teams run` も同じ規則に従う（Red-First）（TEAM-27）
- [ ] 5. チームのジョブのレポートと `brief` に、チームごとのタスク・Checker の判定とモデル・`NO_CHANGES` を表示し、RC コミットと main が変更されていないことを示す（Red-First）（RPT-7）
- [ ] 6. モデルの系統の明示（`--maker-family` / `--checker-family` / `--auditor-family`）、食い違いの拒否、組み込みの判定への `mistral` / `llama` / `qwen` / `deepseek` の追加、`family_source` の記録（Red-First）（DX-7）
- [ ] 7. `auto-dev demo`: 一時ディレクトリで、正常な2タスク・保護ファイルの改変による隔離・`brief`・Bundle の検証を見せる。`--keep` で残す。デモ用のモデル名と系統は DX-7 の明示を使う（Red-First）（DX-6）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [ ] Phase 69 の準備コミット: SPEC.md（Phase 68 の要件を 4 章へ移し、5.1 に Phase 69、5.2 に Phase 70 の要件）、PLAN.md、LESSONS.md、BACKLOG.md、docs/contracts/phase69.md、docs/contracts/phase68.md への追記、scripts/verify_local.sh（両 platform の mypy）。
- [ ] ステップ 7 の完了後:
  - ad-team-trial で `auto-dev teams integrate --job-id job-20261122-002709-97afe0` を実行し直し、`core` が `NO_CHANGES` として扱われることを確かめる（`cli` の成果物には欠陥があるので、main には取り込まない）。
  - Linux（Ubuntu・bwrap が動く環境）で全テストを `AUTO_DEV_REQUIRE_SANDBOX=1` 付きで実行する。
  - GitHub Actions の CI（macOS / Ubuntu × Python 3.10 / 3.12）が通ることを確かめる。

## 記録（Phase 68 から）
- 2026-11-21: Phase 68 の完了後、Linux（Ubuntu・bwrap あり）で `AUTO_DEV_REQUIRE_SANDBOX=1` 付きの全テストを実行し、746 件がすべて通った（スキップ 9 件は、ローカルにしかない実験結果の ZIP と macOS 専用のテスト）。
- 2026-11-22: REL-3（CI のマトリクス化）を行った（3eac615）。Ubuntu の mypy で `verifier.py:1560` の型エラーを検出した。ステップ 1 で直す。
- 2026-11-22: Phase 66 から持ち越していた、実際のエージェントによる `/nightly-run` のチーム経路の確認を、ad-team-trial で行った（シナリオ 1 は成功。シナリオ 2 で TEAM-27 と Checker の見逃しを見つけた。詳細は ad-team-trial の TRIAL.md）。
