# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 60: Stage 7 — 実外部モデル独立監査の実証・完全ハッシュ証跡・Bundle完全収録とv0.7.0最終確定（SPEC.md 機能TTT）
  - 外部監査役実行による監査完走とプロバイダ実行メタデータの完全取得（機能TTT.1 / Sol氏 Phase 59 P0指摘）
  - audit_stage.json の確実な永続化と EvidenceBundler による Bundle 包含・Manifest ハッシュ登録（機能TTT.2 / Sol氏 Phase 59 P1指摘）
  - NIGHTLY_REPORT.md のプレースホルダー完全排除と実測 64文字 SHA-256 の自動展開（機能TTT.3 / Sol氏 Phase 59 P1指摘）
  - 実監査証跡（evidence/audit_stage.json）を含む最終 Evidence Bundle ZIP の生成・独立検証（10/10 整合性）および v0.7.0 正式リリース確定（機能TTT.4 / Sol氏 Phase 59 結論）
- スコープ外: 複数Workerの同時並行Git編集、SPEC.md の自律書き換え、クラウド分散実行。

## 作業手順（Phase 60・LESSONS.md 2026-11-15 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能TTT/,/^## 3\./' SPEC.md > .dev/checker/spec_ttt.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- TTT.1: `tests/test_audit_provenance_and_release.py` 等において、外部モデルプロバイダ実行メタデータ（Provider ID, Execution ID, Prompt/Response SHA 等）の取得・記録を検証するテストを追加。
- TTT.2: `tests/test_bundler.py` および `tests/test_audit_provenance_and_release.py` において、`evidence/audit_stage.json` が bundle 内および `bundle_manifest.json` に確実に包含・ハッシュ登録されることを検証するテストを追加。
- TTT.3: `tests/test_morning_reporter.py` 等において、`NIGHTLY_REPORT.md` 内に仮文字列ではなく 64文字完全 SHA-256 が出力されることを検証するテストを追加。
- TTT.4: `NIGHTLY_REPORT.md` を更新し、10/10 一致の Evidence Bundle 生成と v0.7.0 正式リリース確定を記録。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [ ] 1. 外部監査役実行による監査完走とプロバイダ実行メタデータの完全取得（Red-First）（TTT.1）
- [ ] 2. audit_stage.json の確実な永続化と EvidenceBundler による Bundle 包含・Manifest ハッシュ登録（Red-First）（TTT.2）
- [ ] 3. NIGHTLY_REPORT.md のプレースホルダー完全排除と実測 64文字 SHA-256 の自動展開（Red-First）（TTT.3）
- [ ] 4. 実監査証跡（evidence/audit_stage.json）を含む最終 Evidence Bundle ZIP の生成・独立検証（10/10 整合性）および v0.7.0 正式リリース確定（TTT.4）

## 止まっていること・判断待ち
- なし。Sol 氏の Phase 59 レビュー指摘（P0: 実外部モデル監査実証、P1: audit_stage.json の Bundle 包含、P1: NIGHTLY_REPORT の実測 64文字 SHA-256 出力）に対応する Phase 60（機能TTT）の準備完了。
