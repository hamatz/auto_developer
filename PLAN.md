# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
前のフェーズの PLAN は git の履歴で参照する（Phase 66 は 203f410 時点の PLAN.md）。
-->

## 現在の目標
- Phase 67: 利用者の経路での不具合修正（SPEC.md 5.1: CORE-10, CORE-11, TEAM-20〜24）
  - Phase 66 完了後の確認で、雛形をそのまま使って Linux で `teams run` を実行すると失敗することがわかった。その原因となった4件の欠陥を直す。
  - 雛形をそのまま使い、初期化 → 実行 → 片付け → 次の実行までを通す受入テストを追加する。
- スコープ外: README の書き直し、REL-2 / REL-3 / DX-1〜3（Phase 68）。新しい機能の追加。

## v0.9.0 の残り
- Phase 68（auto developer と人間で実施）: DX-1〜3、REL-2、README.md（日本語）の書き直しと README.en.md、docs の再編、CONTRIBUTING.md、独立監査、v0.9.0 のタグ。
  - REL-3（CI のマトリクス化）は `.github/` の変更なので人間が行う。その前に、Linux で失敗する既存の3件（`test_independent_audit_runner` の criterion_1、`test_red_verifier` の repo_tamper 2件）を、Linux での正しい期待値に直すか、明示的にスキップするかを決める。

## 作業手順（Phase 67・LESSONS.md に基づく）
- Maker と Checker は別系統のモデルにする（`commit-step` が終了コード 4 で強制する）。
  - Maker: Gemini 3.8 Flash (`gemini-3.8-flash`)
  - Checker: Claude Opus 5.5 (`claude-opus-5-5-medium`、Anthropic API 利用。環境変数 `ANTHROPIC_API_KEY` を利用)
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる。
  1. `awk '/^### 5\.1/,/^### 5\.2/' SPEC.md > .dev/checker/spec_phase67.md`
     （不変条件も必要なときは `awk '/^## 3\./,/^## 4\./' SPEC.md` を追記する）
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイルがないことを確かめる。実験は `mktemp -d` の中で行う。
- 査読記録の順序:
  1. `auto-dev verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker の査読（APPROVE / REJECT）。1回の verify-step に対して査読は1回とする。
  3. 査読の直後、commit-step の前に `auto-dev record-review --task-id <n> --decision <decision> --checker-model <checker> --maker-model <maker> --reason "<reason>"`
  4. REJECT なら commit-step を呼ばず、手直しして 1 からやり直す。手直しした後の verify-step にも、必ず査読と record-review を対応させる。
  5. APPROVE なら `auto-dev commit-step --task-id <n> --checkpoint <checkpoint> --maker-model <maker> --checker-model <checker> --checker-decision APPROVE --checker-reason "<reason>"`
- コミットした後に欠陥が見つかった場合は、`git reset` などで正規の経路の外から巻き戻さない。「止まっていること・判断待ち」に書いて止まり、修正は新しいステップとして行う。
- テストを書く途中で製品の欠陥に気づいた場合は、テスト側で回避しない。「止まっていること・判断待ち」に書いて止まる（LESSONS.md 2026-11-21）。
- E2E テストで作る一時リポジトリ・worktree は、必ず `tempfile` の中に作る。このリポジトリの `.dev/worktrees/` には作らない。
- サブプロセスを起動するテストでは、`PYTHONDONTWRITEBYTECODE` と `PYTHONPYCACHEPREFIX` を環境から外して実行し、macOS と Linux で結果が変わらないようにする。
- Bundle を生成した後に、SPEC.md や追跡ファイルを変更しない。SPEC の完了マークは、次のフェーズの準備コミットで人間が付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- ステップ 1（CORE-10）: HTML コメント内の記法例をタスクとして数えることを前提にした既存テストがあれば、新しい規則に合わせて更新してよい。この場合、変更したテスト名をコミットメッセージに列挙する。
- ステップ 5（TEAM-23）: 隔離ブランチ名 `quarantine/<team_id>-...` を前提にした既存テストは、`quarantine/<job_id>/<team_id>/...` に更新してよい。変更したテスト名をコミットメッセージに列挙する。
- 上記以外の既存テストは変更しない。`tests/test_teams_cli_e2e.py`（TEAM-19）は変更しない。免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. 計画ファイルの読み取り範囲: HTML コメント内の行を無視し、「## ステップ」見出しがあればその節だけを読む。進捗表示と状態の書き込みも同じ規則で行う（Red-First）（CORE-10）
- [x] 2. `init` / `teams init` が `.gitignore` に Auto Developer の生成物と Python の一般的な生成物を、重複させずに追記する（Red-First）（CORE-11）
- [ ] 3. 検証スクリプトの実行後・Evidence の発行前に担当範囲を検査し、違反があれば Evidence を発行しない。`verify-step` は終了コード 1 と案内、`run` は再試行・隔離の規則に従う（Red-First）（TEAM-20）
- [ ] 4. コミット直前の担当範囲違反: `run` は Evidence を破棄して隔離・復元・失敗ログ・FAILED 記録を行い、終了コード 1 で停止する。`commit-step` は Evidence を無効にして終了コード 3 で拒否し、`rollback-step` を案内する（Red-First）（TEAM-21）
- [ ] 5. 隔離ブランチ名へのジョブ ID の付与（`quarantine/<job_id>/<team_id>/...`）と、`teams status` / `brief` / `NIGHTLY_REPORT.md` での対象ジョブだけの表示・集計（Red-First）（TEAM-23）
- [ ] 6. 成功したジョブの `teams cleanup`（`--force` なし）で、Auto Developer の生成物とキャッシュ類を保全の対象外にする。削除前に、統合ジョブの保存先に Bundle があることを確認する（Red-First）（TEAM-22）
- [ ] 7. 雛形をそのまま使う受入テスト: `teams run` → `teams cleanup` → 次のジョブの `teams prepare`、および担当範囲外の変更の隔離（TEAM-24）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [ ] Phase 67 の準備コミット: SPEC.md（Phase 66 の要件を 4 章へ移し、5.1 に Phase 67 の要件を追加）、PLAN.md、LESSONS.md、AGENTS.md。
- [ ] このリポジトリの `scripts/verify_local.sh` の保護対象に `teams.json` を追加する（Phase 66 からの持ち越し。保護ファイルなので人間が行う）。
- [ ] ステップ 7 の完了後: Linux 環境（または `PYTHONDONTWRITEBYTECODE` を外した macOS）で、全テストと、雛形を使った `teams run` → `teams cleanup` を一度通す。
- [ ] Phase 66 から持ち越し: 実際のエージェント（Antigravity / Claude Code など）で `/nightly-run` のチーム経路を一度通して、動作を確認する。
