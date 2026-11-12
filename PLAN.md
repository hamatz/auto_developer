# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 50: Stage 7 — 候補テストのOSサンドボックス実行・元リポジトリ改ざん検知・子孫プロセス回収 (SPEC.md 機能JJJ)
  - 候補テストを `sandbox-exec`（macOS）/ `bwrap`（Linux）の内側でのみ実行し、基盤がなければ `SANDBOX_UNAVAILABLE` で拒絶する（機能JJJ.1 / Sol氏 Phase 49 P0指摘）
  - 実行前後の元リポジトリ状態を比較し、差異があれば `REPO_TAMPER_DETECTED` で拒絶する（機能JJJ.2 / Sol氏 Phase 49 P0指摘）
  - プロセスグループ単位の回収とプロセス数上限、`test_large.bin` の削除（機能JJJ.3 / Sol氏 Phase 49 P1指摘）
  - サンドボックス下での Level 7 E2E 完走、悪意ある再現テストの拒絶、報告の正確性（機能JJJ.4）
- スコープ外（Phase 51 以降）: 異系統LLM（実API）の独立Auditor接続（まず Finding 生成のみの非破壊モード）。コンテナ / cgroup による完全な子孫プロセス管理。

## 作業手順（Phase 50 から更新・LESSONS.md 2026-11-12 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Claude 系のとき、Checker サブエージェントは `Model: "pro"`（Gemini 系）を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能JJJ/,/^## 3\./' SPEC.md > .dev/checker/spec_jjj.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- JJJ.1: 候補テストの実行経路がサンドボックス経由に変わるため、`_execute_test()` を直接・間接に使う既存テスト（`tests/test_red_verifier.py`, `tests/test_audit_resolution.py`, `tests/test_level7_real_loop_e2e.py` 等）は、サンドボックス基盤が利用可能な環境で従来どおり通ることを確認する。期待値（GENUINE_RED / ALREADY_GREEN 等）は変更しない。
- JJJ.2 / JJJ.3: 新しい拒絶理由（`REPO_TAMPER_DETECTED`、子孫プロセス残存なし）を検証するテストを追加する。
- JJJ.4: `tests/test_level7_real_loop_e2e.py` に悪意ある再現テストの拒絶経路を追加する。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [ ] 1. OSサンドボックス（macOS `sandbox-exec` / Linux `bwrap`）による候補テスト実行と `SANDBOX_UNAVAILABLE` Fail-Closed の実装、サブプロセス経由の canary 書き換え・ネットワーク接続が遮断される単体テスト（Red-First）（JJJ.1）
- [ ] 2. 実行前後の元リポジトリ状態比較による `REPO_TAMPER_DETECTED` の実装と単体テスト（Red-First）（JJJ.2）
- [ ] 3. プロセスグループ単位の子孫プロセス回収・プロセス数上限の実装と残存プロセスゼロの単体テスト（Red-First）、`test_large.bin` の削除（JJJ.3）
- [ ] 4. サンドボックス下の Level 7 E2E 更新（悪意ある再現テストの拒絶）・報告の正確性・全体検証ゲート合格・正規 Evidence による Bundle 生成（JJJ.4）

## 止まっていること・判断待ち
- 【要判断・nightly-run 前】CI（`.github/workflows/verify.yml`, ubuntu-latest）にはサンドボックス基盤（`bwrap`）が標準で入っていない。JJJ.1 の Fail-Closed により、CI 上では候補テストを実行するテストが `SANDBOX_UNAVAILABLE` となり失敗する。`.github/` は人間のコードオーナー承認が必要なため、次のいずれかを人間が決める:
  - (A) `runs-on: macos-latest` に変更する（`sandbox-exec` が標準で使える。ローカルと同じ経路を検証できる）
  - (B) ubuntu のまま `sudo apt-get install -y bubblewrap` を追加する（Ubuntu 24.04 の非特権ユーザー名前空間制限により、追加の設定が必要になる可能性があり、CI 上での動作確認が要る）
  - (C) CI 上ではサンドボックスが必要なテストをスキップする（CI での検証範囲が大きく減るため非推奨）
