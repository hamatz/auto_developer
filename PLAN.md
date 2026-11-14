# PLAN.md

<!--
現在のタスク進捗と次の一手。会話ログではなく、ここに状態を残す。
実装エージェントは1回に1ステップだけを担当し、終わったらチェックを付けてコミットする。
セッションが切れても、SPEC.md・このファイル・git の履歴を読めば作業を再開できる状態に保つ。
-->

## 現在の目標
- Phase 52: Stage 7 — サンドボックス読み取り最小化とMulti-LLM独立監査境界の確立 (SPEC.md 機能LLL)
  - Linux `bwrap` 読み取りマウントの最小特権化（`/etc` 全体公開廃止 → `/etc/resolv.conf`, `/etc/ssl` 等の最小ホワイトリスト化、Python仮想環境パス限定、機密パス読み取り拒絶の単体テスト）（機能LLL.1 / Sol氏 Phase 51 P1指摘）
  - macOS `sandbox-exec` 読み取りプロファイルの厳格化（ホーム外機密パス・システム設定への deny 追加、単体テスト）（機能LLL.2 / Sol氏 Phase 51 P1指摘）
  - Multi-LLM 独立監査境界の確立と敵対的/不正 Finding 耐性の実証（Auditor の独立性担保、不正・敵対的 Finding の安全な `REJECTED` 処理）（機能LLL.3 / Sol氏 指摘）
  - Level 7 E2E 完走・整合性・Evidence Bundle 生成（機能LLL.4）
- スコープ外（Phase 53 以降）: 実外部LLM API（Anthropic/OpenAI）の本番接続と無人自律開発パイプライン実稼働。

## 作業手順（Phase 52 から更新・LESSONS.md 2026-11-13 の教訓に基づく）
- Maker と Checker は別系統のモデルにする（AGENTS.md Rule 6）。Maker が Gemini 系のとき、Checker サブエージェントは `Model: "pro"` を明示指定する。`inherit` は使わない。commit-step の `--checker-reason` に Checker のモデル指定を書く。
- Checker への入力は要約・再構成しない。以下をファイルに書き出し、Checker にはそのパスを渡して直接読ませる:
  1. `awk '/^- \[ \] 機能LLL/,/^## 3\./' SPEC.md > .dev/checker/spec_lll.md`
  2. `git add -N . && git diff <checkpoint> > .dev/checker/diff_task<n>.patch`（未追跡ファイルも差分に含める）
- verify-step の前に `git status --porcelain` を確認し、意図しないファイル（実験の生成物など）がないことを確かめる。実験・挙動確認は必ず `mktemp -d` で作った一時ディレクトリで行い、リポジトリ直下で実行しない。
- Evidence Record は手書きしない。各ステップで以下の正規経路のみを使う:
  1. `python3 scripts/nightly_orchestrator.py verify-step --checkpoint <checkpoint> --task-id <n>`
  2. Checker 査読（APPROVE / REJECT）
  3. `python3 scripts/nightly_orchestrator.py commit-step --task-id <n> --checkpoint <checkpoint> --checker-decision <decision> --checker-reason "<reason>"`
  `.git/nightly_evidence/` 配下のファイルを直接編集・作成しない。正規経路が失敗した場合は止まってこのファイルに記録する。
- Bundle 生成後に SPEC.md や追跡ファイルを変更しない（Evidence の spec_sha と不一致になる）。SPEC の完了マークは次フェーズの準備コミットで付ける。

## 既存テスト変更の理由 (AGENTS.md Rule 2 に準拠)
- LLL.1: Linux `bwrap` において `/etc` 全体公開を廃止し、`/etc/shadow` 等へのアクセス拒絶をテストする新規テストを追加。
- LLL.2: macOS `sandbox-exec` において `/private/etc` 等へのアクセス拒絶をテストする新規テストを追加。
- LLL.3: 不完全・敵対的な Finding に対する `AuditDispatcher` の耐性を検証するテストを追加。
- LLL.4: `tests/test_level7_real_loop_e2e.py` に新規拒絶シナリオを追加。
- 免疫テスト（`tests/immunity/**`）は変更しない。

## ステップ
- [x] 1. Linux `bwrap` 読み取りマウントの最小特権化（`/etc` 全体公開廃止・個別ファイル化・仮想環境限定・機密拒絶単体テスト）（Red-First）（LLL.1）
- [x] 2. macOS `sandbox-exec` 読み取りプロファイルの厳格化（ホーム外機密パス・システム設定への deny 追加・単体テスト）（Red-First）（LLL.2）
- [x] 3. Multi-LLM 独立監査境界の確立と敵対的/不正 Finding 耐性の実証（Auditor の独立性担保・不正 Finding の安全な REJECTED 処理）（Red-First）（LLL.3）
- [x] 4. Level 7 E2E 完走・報告書更新・全体検証ゲート合格・正規 Evidence による Bundle 生成（LLL.4）

## 止まっていること・判断待ち
- なし。Phase 51 がマージされ、P1（Linux `/etc` および仮想環境公開範囲の最小化、macOS deny リスト強化、Multi-LLM 独立境界・敵対耐性）の修正方針が確定済み。
