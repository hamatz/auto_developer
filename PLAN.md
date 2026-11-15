# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 57: Stage 7 — 実外部モデルによる独立第三者監査の実稼働と Level 7 完全自己改善実証（SPEC.md 機能QQQ）
  - 実外部異系統モデル独立監査パイプラインの構成とベースライン固定（機能QQQ.1）
  - 外部監査役による未知欠陥探索と Auto Developer 防壁による自動選別実証（機能QQQ.2）
  - 真正指摘の自律修正・After Green 検証・免疫昇格・教訓記録（Level 7 Closed Loop）完走実証（機能QQQ.3）
  - 独立監査 Evidence Bundle 生成と v0.7.0 本格リリース確定（機能QQQ.4）
- スコープ外: 複数Workerの同時並行Git編集、SPEC.md の自律書き換え、クラウド分散実行。

## 作業手順（Phase 57 から更新・LESSONS.md 2026-11-15 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能QQQ/,/^## 3\./' SPEC.md > .dev/checker/spec_qqq.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- QQQ.1: `tests/test_independent_audit_runner.py` に実外部モデル独立監査のパイプライン構成とベースラインコミット固定（`98c213a`）を検証するテストを追加。
- QQQ.2: `tests/test_independent_audit_runner.py` および `tests/test_red_verifier.py` に未知のエッジケース・境界値・敵対的指摘の投入と防壁による自動選別（Trivial Red/機密読み取りの遮断、GENUINE_RED の受理）を実証するテストを追加。
- QQQ.3: `tests/test_level7_real_loop_e2e.py` に真正指摘に対する自律修正、After Green 検証、非破壊免疫昇格、LESSONS 記録、次回教訓注入までの Level 7 クローズドループ完走テストを追加。
- QQQ.4: `NIGHTLY_REPORT.md` を更新し、実外部独立監査完了および v0.7.0 本格リリース確定を記録。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. 実外部異系統モデル独立監査パイプラインの構成とベースライン固定（Red-First）（QQQ.1）
- [x] 2. 外部監査役による未知欠陥探索と Auto Developer 防壁による自動選別実証（Red-First）（QQQ.2）
- [ ] 3. 真正指摘の自律修正・After Green 検証・免疫昇格・教訓記録（Level 7 Closed Loop）完走実証（Red-First）（QQQ.3）
- [ ] 4. 独立監査 Evidence Bundle 生成と v0.7.0 本格リリース確定（QQQ.4）

## 止まっていること・判断待ち
- なし。Sol 氏の Phase 56 PASS レビューおよびユーザー承認を受け、実外部モデル独立監査の実稼働と v0.7.0 本格リリース確定を行う Phase 57（機能QQQ）の準備完了。
