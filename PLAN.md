# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 64: 成果の計測基盤と Checker 記録の構造化（SPEC.md 機能XXX）— v0.8.0 向け
  - 「型が効いているか」を数字で判断できるようにする（イベントログ、フェーズ集計、git 履歴からのベースライン）。
  - v0.8.0 に必要な最低限の BACKLOG を取り込む: IDEA-002（commit-step での Maker/Checker 系統照合）、IDEA-003（重複再報告の専用ステータス）。
- スコープ外: Maker / Checker のコスト計測、ダッシュボード、Morning Brief への指標表示、BACKLOG の IDEA-001 / 004 / 005 と AUD-1001。

## 作業手順（Phase 64・LESSONS.md 2026-11-15 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker は異系統の Claude Opus 5.5（swarm にて model: "claude-opus-5-5-medium"）を明示指定して実行する。commit-step の `--checker-reason` に Checker のモデル指定（claude-opus-5.5）を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能XXX/,/^## 3\./' SPEC.md > .dev/checker/spec_xxx.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --maker-model <maker> --checker-model <checker> --checker-decision APPROVE --checker-reason "<reason>"`
     - `--maker-model` / `--checker-model` には実際に使ったモデル識別子を書く（例: `gemini-3.8-flash` / `claude-opus-5-5-medium`）。`pro` のような系統の分からない名前は使わない。ステップ 1 の commit-step からこの形式で呼ぶ（ステップ 1 の実装が入った作業ツリーで実行されるため）。
     - Checker が REJECT した場合は commit-step を呼ばない。
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。
- ステップ 3 の完了後は、Checker の結果を APPROVE / REJECT とも `python3 scripts/nightly_orchestrator.py record-review --task-id <n> --decision <decision> --checker-model <checker> --maker-model <maker> --reason "<reason>"` で記録する（REJECT 率の計測のため）。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- XXX.1: 既存テストで `commit-step` を `--maker-model` / `--checker-model` / `--checker-decision` なしで呼んでいる箇所に、これらの引数（例: `gemini-3.8-flash` / `claude-opus-5.5` / `APPROVE`）を追加する。検証内容（Evidence 必須、リプレイ拒否など）のアサーションは弱めない。
- XXX.2: 抑止された再報告が `rejected` に含まれることを前提にしている箇所があれば、`duplicates` を期待するよう変更する。
- 上記以外の既存テストは変更しない。免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [ ] 1. commit-step の Checker 方針検査: --maker-model / --checker-model / --checker-decision 必須化、APPROVE 以外と同系統・系統不明を終了コード 4 で拒否（Evidence 未消費・HEAD 不変）、record の checker 構造化（Red-First: tests/immunity/test_immunity_checker_policy.py が Red であることを確認してから着手）（XXX.1）
- [ ] 2. 抑止された再報告を DUPLICATE とし、rejected と分けて集計・表示する（Red-First）（XXX.2）
- [ ] 3. MetricsLog と verify_step / commit_step / rollback_step / checker_review（record-review サブコマンド）イベントの記録。書き込み失敗時もゲート結果を変えない（Red-First）（XXX.3）
- [ ] 4. 監査ステージの audit_stage イベント記録（Red-First）（XXX.3）
- [ ] 5. auto-dev metrics: イベントログからのフェーズ集計と直前フェーズとの差分（Red-First）（XXX.4）
- [ ] 6. auto-dev metrics: git 履歴からの集計（フェーズ範囲・計画外ステップ・人間コミット・所要時間）と --append-to（Red-First）（XXX.4）
- [ ] 7. METRICS.md の作成（指標の定義と判断の対応、Phase 50 以降のベースライン、Phase 64 の集計）（XXX.5）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Maker は実行しない）
- [済] XXX.1 の免疫テスト `tests/immunity/test_immunity_checker_policy.py` を Red-First で追加。
- ステップ 3 の完了後: `docs/proposals/phase64/test_immunity_metrics_non_interference.py` を `tests/immunity/` に配置し、Green であることを確認してコミットする（計測の非干渉を守る見張り役。実装後に置くため Red-First ではない）。
- ステップ 7 の完了後: METRICS.md の数字を見て、v0.8.0 で何を判断するか（Maker の変更、監査頻度など）を決める。
