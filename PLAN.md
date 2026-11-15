# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 61: Stage 7 — 実Claude/Anthropic API独立監査の実稼働・Fail-Closed安全停止・実プロバイダID証跡拘束・Red構造化取り込み実証およびv0.7.0正式リリース確定（SPEC.md 機能UUU）
  - 実Anthropic APIクライアントの実装・Fail-Closed安全停止・本物provider_request_id取得（機能UUU.1 / Sol氏 Phase 60 P0指摘）
  - audit_stage.json の mode: LIVE / MOCK 分離・実プロバイダID拘束・EvidenceBundler / verify_bundle 厳格化（機能UUU.2 / Sol氏 Phase 60 P1指摘）
  - 実Claudeによるコード監査実行・意図的Red指摘の構造化取り込み実証（機能UUU.3 / Sol氏 Phase 60 受け入れ条件 #3）
  - 実Claude監査証跡を内包した最終 Evidence Bundle ZIP の生成・独立検証・v0.7.0 正式リリース確定（機能UUU.4 / Sol氏 Phase 60 結論）
- スコープ外: 複数Workerの同時並行Git編集、SPEC.md の自律書き換え、クラウド分散実行。

## 作業手順（Phase 61・LESSONS.md 2026-11-15 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能UUU/,/^## 3\./' SPEC.md > .dev/checker/spec_uuu.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- UUU.1: `tests/test_audit_provenance_and_release.py` 等において、Anthropic API 実呼出し・`provider_request_id`（`msg_...`）の取得、および API キー未設定やエラー時に `findings: []` ではなく `status: "FAILED"` / `"AUDIT_PENDING"` となる Fail-Closed 挙動を検証するテストを追加。
- UUU.2: `tests/test_bundler.py` および `tests/test_audit_provenance_and_release.py` において、`mode: LIVE` と `MOCK` の区別、および `verify_bundle` が `mode == "LIVE"` かつ `provider_request_id` を検証することをテスト。
- UUU.3: `tests/test_live_claude_audit.py` を追加し、Claude Opus / Sonnet の実監査レスポンスによる Finding の構造化取り込みと Red 検証をテスト。
- UUU.4: `NIGHTLY_REPORT.md` を更新し、`mode: LIVE` の監査証跡を含む 10/10 一致の Evidence Bundle 生成と v0.7.0 正式リリース確定を記録。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. 実Anthropic APIクライアントの実装・Fail-Closed安全停止・本物provider_request_id取得（Red-First）（UUU.1）
- [ ] 2. audit_stage.json の mode: LIVE / MOCK 分離・実プロバイダID拘束・EvidenceBundler / verify_bundle 厳格化（Red-First）（UUU.2）
- [ ] 3. 実Claudeによるコード監査実行・意図的Red指摘の構造化取り込み実証（Red-First）（UUU.3）
- [ ] 4. 実Claude監査証跡を内包した最終 Evidence Bundle ZIP の生成・独立検証・v0.7.0 正式リリース確定（UUU.4）

## 止まっていること・判断待ち
- なし。Sol 氏の Phase 60 レビュー指摘（P0: 実外部モデル呼出しの実体化とFail-Closed、P1: mode: LIVE / MOCK 分離と実プロバイダID拘束、条件#3: 意図的Red指摘の取り込み実証）に対応する Phase 61（機能UUU）の準備完了。
