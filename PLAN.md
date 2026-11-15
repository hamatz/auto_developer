# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 59: Stage 7 — 独立監査CLI完全化・厳格Fail-Closed・異系統モデル堅持・実監査実証とv0.7.0最終確定（SPEC.md 機能SSS）
  - CLI からの独立監査有効化・設定オプション追加と起動前設定バリデーション（機能SSS.1 / Sol氏 Phase 58 P1指摘）
  - audit_repository における SPEC コミット拘束の厳格化とフォールバック完全撤廃（機能SSS.2 / Sol氏 Phase 58 P1指摘）
  - モデルフォールバック時の同系統モデル排除と異系統モデル堅持（機能SSS.3 / Sol氏 Phase 58 P1指摘 & ユーザー要件）
  - 実外部異系統 LLM による監査実績の証跡記録・全検証ゲート合格・最終 Evidence Bundle 生成と v0.7.0 正式リリース確定（機能SSS.4 / Sol氏 Phase 58 P0指摘）
- スコープ外: 複数Workerの同時並行Git編集、SPEC.md の自律書き換え、クラウド分散実行。

## 作業手順（Phase 59 から更新・LESSONS.md 2026-11-15 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能SSS/,/^## 3\./' SPEC.md > .dev/checker/spec_sss.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- SSS.1: `tests/test_cli.py` および `tests/test_nightly_orchestrator.py` に `--enable-audit` 等のCLI引数、起動前バリデーション（`--auditor-cmd` 未設定時の Exit 2、同一モデル拒絶）を検証するテストを追加。
- SSS.2: `tests/test_independent_audit_runner.py` において、対象コミット内に `SPEC.md` が存在しない場合に作業ツリーへフォールバックせず `FileNotFoundError("SPEC_NOT_FOUND_IN_COMMIT")` で Fail-Closed 拒絶されることを検証するテストを追加。また `tests/test_level7_real_loop_e2e.py` で `SPEC.md` を対象コミット内に拘束するよう更新。
- SSS.3: `tests/test_independent_audit_runner.py` において、フォールバック時であっても同系統モデルが排除され、異系統モデルのみが選択されること、および適切なモデルがない場合に `AUDIT_PENDING` へ安全退避することを検証するテストを追加。
- SSS.4: `NIGHTLY_REPORT.md` を更新し、実外部モデル独立監査の実稼働および v0.7.0 正式リリース確定を記録。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. CLI からの独立監査有効化・設定オプション追加と起動前設定バリデーション（Red-First）（SSS.1）
- [x] 2. audit_repository における SPEC コミット拘束の厳格化とフォールバック完全撤廃（Red-First）（SSS.2）
- [x] 3. モデルフォールバック時の同系統モデル排除と異系統モデル堅持（Red-First）（SSS.3）
- [ ] 4. 実外部異系統 LLM による監査実績の証跡記録・全検証ゲート合格・最終 Evidence Bundle 生成と v0.7.0 正式リリース確定（SSS.4）

## 止まっていること・判断待ち
- なし。Sol 氏の Phase 58 レビュー指摘（P0: 実モデル監査 Evidence 証明、P1: CLI からの独立監査有効化・起動前設定検証、P1: SPEC コミット拘束 Fail-Closed 徹底、P1: フォールバック時の同系統モデル排除と異系統堅持）に対応する Phase 59（機能SSS）の準備完了。
