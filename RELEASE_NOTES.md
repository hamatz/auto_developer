# Auto Developer v0.7.0 Release Notes

**Version**: `v0.7.0`  
**Date**: 2026-11-16  
**Status**: General Availability (GA)  

---

## 概要 (Executive Summary)

Auto Developer v0.7.0 は、AI エージェントによる自律的なソフトウェア開発において、**「本物の異系統外部 LLM（Claude Opus 5.5 / Anthropic API）による完全無人独立監査と、改ざん不能な暗号学的証跡（Evidence Bundle）」** を実用化させた大型マイルストーンリリースです。

実装役（Maker）である Gemini と、監査役（Auditor）である Claude Opus 5.5 を物理的に分離し、139,000 トークン規模の累積差分に対する深層監査を完走。監査結果の偽装や Fail-Open を徹底的に排除する Fail-Closed 安全機構、および監査証跡を内包した配布用 Evidence Bundle の 10/10 完全性検証を実現しました。

さらに本バージョンでは、45 サイクルに及ぶ監査・修正の実動から **「AI が正しいこと（微細なエッジケース修正）をやり過ぎて全体として間違った行動（開発の停滞・エンドレスゲーム）に陥ることを防ぐ設計原則」** を獲得し、Level 7 自律開発の持続可能性を決定づける運用規範を確立しました。

---

## 主要な新機能と改善点 (Key Features & Highlights)

### 1. 外部 LLM（Claude Opus 5.5）による実動独立監査ステージ (Stage 7 Independent Audit)
- **異系統モデルの物理分離**: AGENTS.md Rule 6 に準拠し、実装役（Maker: Gemini 3.8 Flash）と監査役（Auditor: Claude Opus 5.5）の癒着・自己監査を遮断。
- **Anthropic Messages API 実連携**: `scripts/auditor_client.py` を通じ、本物の Anthropic API から `msg_...` 識別子、`stop_reason: "end_turn"`、および詳細なトークン消費情報（入力 139,361 / 出力 6,653 / 思考 5,410）を取得・記録。
- **Fail-Closed 安全停止**:
  - API キー欠落、通信障害、HTTP エラー時は絶対に `findings: []`（合格偽装）を出力せず、非ゼロ終了（Exit 1）で安全停止。
  - 出力トークン上限打ち切り（`stop_reason == "max_tokens"`）や JSON 解析不能時は `status: "AUDIT_PENDING"` またはエラーとして扱い、欠陥ゼロへのすり替えを遮断。

### 2. 構造化指摘の Red-First 取り込みと因果関係検証
- **自律ディスパッチと Red 検証**: 監査モデルが返却した構造化 Finding から再現テストコードを抽出し、隔離サンドボックス（Linux bwrap / macOS sandbox-exec）下で事前 Red（失敗）を確認した本物の欠陥のみを `PLAN.md` に登録。
- **Trivial Red 排除の強化**:
  - 実行されないラムダ定義（`lambda:`）や静的死分岐（`if False:`）内の呼出による例外ガード偽装の排除（AUD-901）。
  - 例外ハンドラ内で `raise` を再送出しテストが ERROR で終了する構成の排除（AUD-801）。
  - ヘルパー関数経由のファクトリ呼出や `self` 経由のプロダクト参照の正当な因果追跡（AUD-601, AUD-701）。

### 3. ポスト監査差分検査 (Post-Audit Diff Integrity)
- **未監査コードの混入防止**: `EvidenceBundler.verify_bundle()` において、監査対象コミット（`audit_target_commit`）と最終 HEAD の間にプロダクトコード（`src/**`, `scripts/**`）の差分が存在する場合、`AUDIT_OUTDATED` としてリリースを拒絶。監査後に密かに変更を差し込む不正を物理遮断。

### 4. 改ざん検知 Evidence Bundle (ZIP) と 10/10 完全性検証
- **単一コマンドでの検証**: `auto-dev verify-bundle <bundle.zip>` により、Git バンドル（コミット・ツリー）、SPEC SHA-256、全タスク Evidence Record、独立 Checker 承認（APPROVE）、および実 Claude 監査証跡（LIVE / msg_...）の完全性を一括検証（10/10 一致）。

---

## 獲得した設計原則：エンドレスゲームの防止 (Lessons Learned)

本リリースの開発過程において、監査モデルが AST 静的解析器の構文上の微細な回避パターン（未実行ラムダ、別名 import、dotted import 等）を無限に掘り出し、40 サイクル以上にわたって P2 修正が続く「エンドレスゲーム」が発生しました。

この経験から、Auto Developer は以下の重要な設計原則を確立しました：

1. **監査の目的は「欠陥ゼロの証明」ではなく「重大欠陥の排除」**:
   AST による静的真偽判定を完全なプログラム解析器に近づけようとする方向には自然な終点（停止性）がありません。
2. **重要度に基づくトリアージとリリース切り離し**:
   - **P0/P1（重大欠陥・Fail-Open・安全性破綻）**: 即時修正・リリース BLOCK。
   - **P2（限定条件での不具合・構文エッジケース）**: Backlog に記録し、リリースを BLOCK しない。
   - **P3（改善提案・理論的懸念）**: レポート通知のみ。
3. **自動修正回数の上限設定**:
   同一指摘への自動修正は最大 3 回、同一モジュールへの連続監査は最大 5 サイクルとし、上限到達時は自動停止して人間に引き渡す。

---

## 検証実績 (Verification Provenance)

- **自動テスト**: 471 件全件 PASS (`scripts/verify_local.sh`, Exit 0)
- **型検査**: Mypy エラー 0 件 (23 source files)
- **リンター**: Ruff エラー 0 件
- **独立査読**: Independent Checker (`Model: "pro"`) による全タスク APPROVE
- **外部監査証跡**:
  - Provider: `anthropic` (Messages API)
  - Model: `claude-opus-5.5` (`claude-opus-5-5`)
  - Provider Request ID: `msg_011Ch6ZjpRGpinfaFDWDWGv2`
  - Stop Reason: `end_turn`
  - Input Tokens: 139,361 / Output Tokens: 6,653 (Thinking: 5,410)
  - Verification: `auto-dev verify-bundle` -> **`VERIFIED` (Status: VERIFIED, Tasks Audited: 4, Audit Stage: VERIFIED)**

---

## 今後の課題とロードマップ (v0.7.1+)

- **AUD-1001 の対応**: `import pkg.module` 形式の dotted import におけるモジュール呼出の安全側判定。
- **自動修正サイクル上限・トリアージの機械的実装**: P2 指摘の自動 Backlog 化および修正上限（3回/5サイクル）のオーケストレータ組み込み。
- **コンテナ環境での bwrap 事前プローブ**: 非特権コンテナ等で bwrap が利用不可な場合の自動検出の強化。
