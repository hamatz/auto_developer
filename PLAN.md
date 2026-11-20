# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
前フェーズ（Phase 65）の PLAN は docs/history/PLAN-phase65.md に残している。
-->

## 現在の目標
- Phase 66: マルチチーム実行の CLI・スキル接続と Bundle 形式の更新（SPEC.md 5.1 TEAM-10〜19, 5.2 REL-1）
  - `teams.json` と `plans/<team>.md` によるチーム定義。チームの文脈は、ブランチ名とコミット済みの `teams.json` から決める。
  - `auto-dev teams init / prepare / integrate / run / status / cleanup / recover`。
  - `/nightly-run` と `/nightly-setup` のチーム対応。
  - 2チーム構成の E2E 受入テスト。
- スコープ外: `depends_on` による段階実行、main への自動マージ、README の書き直し（Phase 67）。

## v0.9.0 全体の流れ
- Phase 66（本フェーズ・auto developer で実施）: 上記。
- Phase 67（auto developer と人間で実施）: DX-1〜3（README クイックスタートの実行テスト、モデル名の既定値、`examples/two-teams/`）、REL-2（0.9.0 表記）、README.md（日本語）の書き直しと README.en.md、docs の再編、CONTRIBUTING.md、リリース。
  - REL-3（CI のマトリクス化）は `.github/` の変更なので人間が行う。
  - 参考: 2026-11-20 に Linux（Ubuntu・Python 3.10・bwrap あり）で全テストを実行したところ、567 件中 3 件が失敗した（`test_independent_audit_runner` の criterion_1、`test_red_verifier` の repo_tamper 2 件）。いずれも bwrap 経路の環境依存で、期待する失敗の種類が macOS を前提にしている。REL-3 の前に、これらを「Linux での正しい期待値」に直すか、明示的にスキップするかを決める。

## 作業手順（Phase 66・LESSONS.md に基づく）
- Maker と Checker は別系統のモデルにする（`commit-step` が終了コード 4 で強制する）。
  - Maker: Gemini 3.8 Flash (`gemini-3.8-flash`)
  - Checker: Claude Opus 5.5 (`claude-opus-5-5-medium`、Anthropic API 利用。環境変数 `ANTHROPIC_API_KEY` を利用)
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる。
  1. `awk '/^## 5\./,/^## 6\./' SPEC.md > .dev/checker/spec_phase66.md`
     （不変条件も必要なときは `awk '/^## 3\./,/^## 4\./' SPEC.md` を追記する）
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイルがないことを確かめる。実験は `mktemp -d` の中で行う。
- 査読記録の順序:
  1. `auto-dev verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker の査読（APPROVE / REJECT）
  3. 査読の直後、commit-step の前に `auto-dev record-review --task-id <n> --decision <decision> --checker-model <checker> --maker-model <maker> --reason "<reason>"`
  4. REJECT なら commit-step を呼ばず、手直しして 1 からやり直す。
  5. APPROVE なら `auto-dev commit-step --task-id <n> --checkpoint <checkpoint> --maker-model <maker> --checker-model <checker> --checker-decision APPROVE --checker-reason "<reason>"`
- E2E テストで作る一時リポジトリ・worktree は、必ず `tempfile` の中に作る。このリポジトリの `.dev/worktrees/` には作らない。
- Bundle を生成した後に、SPEC.md や追跡ファイルを変更しない。SPEC の完了マークは、次のフェーズの準備コミットで人間が付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- ステップ 6（REL-1）: Bundle を検証する既存テストのうち、`format_version` のない manifest を合格として扱っているもの（`tests/test_bundler.py` など）は、新しい形式の manifest を生成するように更新してよい。この場合、変更したテスト名をコミットメッセージに列挙する。
- 上記以外の既存テストは変更しない。免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. チーム定義ファイル `teams.json` の読み込みと検証（`plan` フィールド、`depends_on` の拒否、`owns` の重なりの拒否、`TEAMS_CONFIG_ERROR` / 終了コード 2）、および `auto-dev teams init`（Red-First）（TEAM-10, TEAM-12）
- [x] 2. チーム文脈の決定: worktree 内の `run` / `pre-flight` / `verify-step` / `commit-step` / `rollback-step` が、ブランチ `team/<id>` とコミット済みの `teams.json` から `owns`・計画ファイルを決め、担当範囲外の変更を検知する。`teams.json` を保護ファイルに追加し、`run` に `--team-id` / `--owns` / `--plan` を追加する（Red-First）（TEAM-11）
- [x] 3. `auto-dev teams prepare` / `teams status` / `teams cleanup`（前回のジョブが残っているときの拒否と案内を含む）（Red-First）（TEAM-13, TEAM-16）
- [x] 4. `auto-dev teams integrate`（完了確認、Bundle の生成・保全、統合ゲート、RC タグと取り込みコマンドの表示、失敗時に worktree を残す）（Red-First）（TEAM-14）
- [x] 5. `auto-dev teams run`（`--worker-timeout`、Maker / Checker / モデル名 / `--mock` の受け渡し）と `teams recover`、チームのジョブの結果を NIGHTLY_REPORT.md と `brief` に表示（Red-First）（TEAM-15, TEAM-16, TEAM-17）
- [ ] 6. Bundle の `format_version` / `tool_version` と、旧形式の Bundle を拒否する処理（Red-First）（REL-1）
- [ ] 7. 2チーム構成の E2E 受入テスト（一括実行経路・低レベルサブコマンド経路・担当範囲外の変更の隔離・設定エラー）（TEAM-19）
- [ ] 8. スキルの改修: `/nightly-setup` のチーム初期化、`/nightly-run` のチーム経路（prepare → 各 worktree での直列ループ → integrate）（TEAM-18）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [ ] Phase 66 の準備コミット: 新しい SPEC.md / LESSONS.md / PLAN.md、docs/history/ のアーカイブ。
- [ ] このリポジトリの `scripts/verify_local.sh` の保護対象に `teams.json` を追加する（ステップ 2 の後。保護ファイルなので人間が行う）。
- [ ] ステップ 8 の完了後: 実際のエージェント（Antigravity / Claude Code など）で `/nightly-run` のチーム経路を一度通して、動作を確認する。
