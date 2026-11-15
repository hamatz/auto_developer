# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 58: Stage 7 — 実外部モデル独立監査の本番統合・SPECコミット拘束・フォールバック自律防御とv0.7.0確定（SPEC.md 機能RRR）
  - 監査対象 SPEC のコミット拘束と監査入力ハッシュの厳格証明（機能RRR.1 / Sol氏 Phase 57 P1指摘）
  - NightlyOrchestrator への独立監査ステージの本番自動組み込み（機能RRR.2 / Sol氏 Phase 57 P1指摘）
  - モデル上限・障害時の弾力的フォールバックと実外部モデル監査・Level 7閉ループ実証（機能RRR.3 / Sol氏 Phase 57 P0指摘 & ユーザー要件）
  - 独立監査 Evidence Bundle 生成と v0.7.0 正式リリース確定（機能RRR.4）
- スコープ外: 複数Workerの同時並行Git編集、SPEC.md の自律書き換え、クラウド分散実行。

## 作業手順（Phase 58 から更新・LESSONS.md 2026-11-15 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能RRR/,/^## 3\./' SPEC.md > .dev/checker/spec_rrr.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- RRR.1: `tests/test_independent_audit_runner.py` に `git show <target_commit>:SPEC.md` からの抽出および監査入力メタデータ（`target_commit`, `target_tree_sha`, `baseline_commit`, `spec_sha256`, `diff_sha256`）の厳格拘束を検証するテストを追加。
- RRR.2: `tests/test_nightly_orchestrator.py` にジョブ完了後の独立監査ステージ（`run_audit_stage`）の自動実行、Fail-Safe 保全、真正 Finding の次期 PLAN 自動登録を検証するテストを追加。
- RRR.3: `tests/test_independent_audit_runner.py` および `tests/test_level7_real_loop_e2e.py` にモデル上限・障害時のフォールバック機構、実モデル監査入出力ハッシュ・生Finding・Proof 拘束を実証するテストを追加。
- RRR.4: `NIGHTLY_REPORT.md` を更新し、実外部モデル独立監査の実稼働および v0.7.0 正式リリース確定を記録。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. 監査対象 SPEC のコミット拘束と監査入力ハッシュの厳格証明（Red-First）（RRR.1）
- [ ] 2. NightlyOrchestrator への独立監査ステージの本番自動組み込み（Red-First）（RRR.2）
- [ ] 3. モデル上限・障害時の弾力的フォールバックと実外部モデル監査・Level 7閉ループ実証（Red-First）（RRR.3）
- [ ] 4. 独立監査 Evidence Bundle 生成と v0.7.0 正式リリース確定（RRR.4）

## 止まっていること・判断待ち
- なし。Sol 氏の Phase 57 レビュー指摘（P0: 実モデル監査 Evidence 証明、P1: Orchestrator への本番組み込み、P1: SPEC コミット拘束）およびユーザーのフォールバック考慮要件に対応する Phase 58（機能RRR）の準備完了。
