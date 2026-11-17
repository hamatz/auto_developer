# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 63: 監査サイクルの終了保証 — 重要度トリアージ・修正予算・リリース判定の分離（SPEC.md 機能WWW）
  - LESSONS.md 2026-11-16「P2モグラ叩きの底なし沼化」の Rule を、文章ではなく dispatcher / bundler / reporter の作りに置く。
  - 本フェーズ自体を通常パイプライン（Maker → verify-step → Checker → commit-step）で実装する（ドッグフーディング）。
- スコープ外: 複数Workerの同時並行Git編集、SPEC.md の自律書き換え、クラウド分散実行、メタ監査制限・予算リセット CLI・重要度自動降格（WWW スコープ外）。
- AUD-1001（P2）は本フェーズの方針に従い PLAN から BACKLOG.md へ移した。本フェーズでは着手しない。

## 作業手順（Phase 63・LESSONS.md 2026-11-15 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能WWW/,/^## 3\./' SPEC.md > .dev/checker/spec_www.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- WWW.1: 既存の監査取り込みテスト（`tests/test_immunity_and_red_acceptance.py`, `tests/test_independent_audit_runner.py` 等）で、P2 の finding が `DISPATCHED` になることを前提にしている箇所があれば、`BACKLOGGED` を期待するよう変更する。P0/P1 の挙動は変えない。
- WWW.4: `tests/test_nightly_orchestrator.py` 等で、Checker のテキスト出力 "PASS" のみで承認されることを前提にしている箇所があれば、判定行（APPROVE / REJECT）を出力する形に変更する。
- 上記以外の既存テストは変更しない。免疫テスト（`tests/immunity/**`）は変更しない（本フェーズの免疫テスト草案は `docs/proposals/phase63/` にあり、完了後に人間が配置する）。

## ステップ
- [x] 1. Checker 判定のテキスト・フォールバックを Fail-Closed にする（部分文字列 PASS による承認の廃止、判定行の一意性要求）（Red-First）（WWW.4）
- [ ] 2. 重要度トリアージ: P2 → BACKLOG.md（Red 検証なし）、INFO → AuditStore のみ、P0/P1 → 従来経路。VALID_STATUSES 拡張と監査プロンプトへの重要度定義の埋め込み（Red-First）（WWW.0 / WWW.1）
- [ ] 3. 修正予算: process_findings を監査サイクルの正規入口にし、モジュール単位の連続サイクル上限と ESCALATED 引き渡しを実装（Red-First）（WWW.2）
- [ ] 4. リリース判定の分離: find_release_blockers と verify_bundle の RELEASE_BLOCKED、Bundle への BACKLOG.md 同梱と backlog_count（Red-First）（WWW.3）
- [ ] 5. Morning Brief に DISPATCHED / BACKLOGGED / ESCALATED を分けて表示し、ESCALATED を判断待ちに列挙（Red-First）（WWW.5）

## 止まっていること・判断待ち
- なし。

## 人間が行うこと（Phase 63 のステップ完了後。Maker は実行しない）
- `docs/proposals/phase63/test_immunity_audit_budget.py` を `tests/immunity/` に配置し（`tests/immunity/__init__.py` も作成）、`bash scripts/verify_local.sh` が Green になることを確認してコミットする。
- 配置前の実装（Phase 62 時点のコミット）でこの免疫テストが Red になることを一度確認し、LESSONS.md 2026-11-16 の Immunity Test / Proof 欄を実際のパスと結果で更新する。
