# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 62: Stage 7 — 実Claude監査の完全性検証・stop_reason拘束・Red取り込み環境耐性・ポスト監査差分拘束およびv0.7.0最終リリース確定（SPEC.md 機能VVV）
  - 監査応答の終了理由検証 (stop_reason)・max_tokens打ち切り検知およびJSON解析失敗のFail-Closed化（機能VVV.1 / Sol氏 Phase 61 P1指摘）
  - `test_live_claude_audit.py` の環境差異解消および Linux bwrap パス修正（機能VVV.2 / Sol氏 Phase 61 P1指摘）
  - 監査対象コミット拘束およびポスト監査差分検査 (Post-Audit Diff Integrity)（機能VVV.3 / Sol氏 Phase 61 考察）
  - 最新HEADに対する実Claude Opus 5.5再監査・完全性検証済みEvidence Bundle ZIP生成・v0.7.0正式リリース確定（機能VVV.4 / Sol氏 Phase 61 結論）
- スコープ外: 複数Workerの同時並行Git編集、SPEC.md の自律書き換え、クラウド分散実行。

## 作業手順（Phase 62・LESSONS.md 2026-11-15 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能VVV/,/^## 3\./' SPEC.md > .dev/checker/spec_vvv.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- VVV.1: `tests/test_audit_provenance_and_release.py` 等において、`_extract_findings_from_text` が有効な JSON を含まない場合に `[]` ではなくエラー（または FAILED）となること、および `stop_reason == "max_tokens"` での `AUDIT_PENDING` 安全停止・証跡記録を検証するテストを追加。
- VVV.2: `tests/test_live_claude_audit.py` において、Linux bwrap の `--tmpfs /tmp` マウント順序対応、および OS サンドボックスが未導入・利用不可な環境（非特権コンテナ等）でも構造化指摘取り込みが確実に検証されるテストを追加。
- VVV.3: `tests/test_bundler.py` において、`audit_target_commit` と Bundle `head_commit` 間にプロダクトコード（`src/**`, `scripts/**`）の差分が存在する場合に未監査として拒絶（`AUDIT_OUTDATED`）するテストを追加。
- VVV.4: `NIGHTLY_REPORT.md` を更新し、最新 HEAD に対する実 Claude Opus 5.5 監査証跡（`stop_reason: "end_turn"` 拘束）を内包した 10/10 一致の Evidence Bundle 生成と v0.7.0 正式リリース確定を記録。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. 監査応答の終了理由検証 (stop_reason)・max_tokens打ち切り検知およびJSON解析失敗のFail-Closed化（Red-First）（VVV.1）
- [x] 2. `test_live_claude_audit.py` の環境差異解消および Linux bwrap パス修正（Red-First）（VVV.2）
- [x] 3. 監査対象コミット拘束およびポスト監査差分検査 (Post-Audit Diff Integrity)（Red-First）（VVV.3）
- [ ] 4. 最新HEADに対する実Claude Opus 5.5再監査・完全性検証済みEvidence Bundle ZIP生成・v0.7.0正式リリース確定（VVV.4）

- [x] AUD-AUD-001. Fix: [AUD-001] IndependentAuditRunner.parse_response() still takes only the FIRST ```json``` code block when the output is not raw JSON. The auditor_client.py change addressed the case where an empty format example (```json [] ```) comes before a later block with real findings. This parser did not get the same fix. With such output, the example block's `[]` is returned as zero findings and the real findings are silently discarded. This is the 'zero defects' disguise that VVV.1 forbids. (Severity: P1, Loc: src/auto_developer/audit.py:279)
- [x] AUD-AUD-002. Fix: [AUD-002] parse_response() checks for a non-COMPLETED status only when has_prov_metadata is true, i.e. when mode, execution_id, provider_request_id or provider is present. An envelope such as {"status": "FAILED", "error": "...", "findings": []} has none of those keys. It therefore skips every check and is accepted as a successful audit with zero findings. A failed audit is turned into 'no defects' (fail-open). (Severity: P1, Loc: src/auto_developer/audit.py:296)
- [x] AUD-AUD-004. Fix: [AUD-004] parse_response() treats mixed findings arrays differently depending on input shape. When the whole output is a single JSON envelope (Step 1), its findings are filtered with isinstance(item, dict) only. A mixed array is therefore accepted and the non-dict entries are silently discarded. The code-block path (Step 2) rejects the same mixed array with has_decode_error. (Severity: P2, Loc: src/auto_developer/audit.py:300)
- [x] AUD-AUD-005. Fix: [AUD-001] Step 3 non-provider dict scan and provider scan fail open on error objects preceded by log line (e.g. {"error": "...", "findings": []} returns []) in audit.py and auditor_client.py (Severity: P1, Loc: src/auto_developer/audit.py:430)
- [x] AUD-AUD-006. Fix: [AUD-002] parse_response() and _extract_findings_from_text() drop malformed raw JSON findings when valid code block exists (Severity: P2, Loc: src/auto_developer/audit.py:455)
- [x] AUD-AUD-007. Fix: [AUD-003] check_trivial_red_ast() in verifier.py flags self.fail() preceded by try-except with product call as unconditional fail without exception guard (Severity: P2, Loc: src/auto_developer/verifier.py:166)
## 止まっていること・判断待ち
- なし。Sol 氏の Phase 61 レビュー指摘（P1: stop_reason 検証・max_tokens打ち切り検知・JSON解析失敗のFail-Closed化、P1: test_live_claude_audit.py の環境差異解消と Linux bwrap 修正、考察: 監査対象コミットと最終HEADの差分整合性拘束）に対応する Phase 62（機能VVV）の準備完了。
