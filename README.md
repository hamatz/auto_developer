# Auto Developer: 自律型開発実行基盤 (Trustworthy Autonomous Runner)

[![Verify CI](https://github.com/hamatz/auto_developer/actions/workflows/verify.yml/badge.svg)](.github/workflows/verify.yml)
[![Antigravity Plugin](https://img.shields.io/badge/Antigravity-Plugin-brightgreen.svg)](plugin.json)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

> **Auto Developer は、人間が不在の間も、合意された仕様と客観的な検証条件に従って開発作業を進め、成功も失敗も検証可能な形で人間に引き渡す自律型開発実行基盤です。**
> Antigravity プラグイン（`/nightly-setup`, `/nightly-run`）または独立した CLI ツール（`auto-dev`）として、あらゆる既存・新規プロジェクトに導入できます。

Google Antigravity および [Agent Harness Kit 論文](https://github.com/hamatz/agent-harness-kit/blob/main/docs/paper.md) で提唱される Elephant-Goldfish Model（EGM）の設計規律に基づき、**「人間が一時的に仕事から離れること」** を開発プロセスの正常系として扱います。

---

## 🎯 なぜ必要なのか？（従来の課題と解決策）

自律型AIエージェントに複数タスクを一括して任せると、以下の問題で破綻します：
1. **会話セッション依存の脆弱性**: 長い対話セッションで全タスクを回すとトークンが逼迫し、セッション切断やクラッシュで作業がすべて蒸発する。
2. **テストコードの勝手な改ざん**: テストが通らないとき、エージェントがテスト側を弱めたり保護ファイルを書き換えて強引に通してしまう。
3. **未検証コードの混入**: 実装が完了していない、あるいは検証を通っていない変更がコミットされてしまう。
4. **全破壊ロールバックの代償**: 失敗時に `git reset --hard` すると、エージェントが数時間かけて書いた差分や中間ログまで消滅し、人間が引き継げない。

Auto Developer は、**「Job Orchestrator（親プロセスによる状態永続化）」「未追跡ファイルを含む物理防壁」「隔離退避（Quarantine Branch）」「暗号学的証跡ゲート（Evidence Gate）」「翌朝の意思決定支援（Morning Brief）」** により、人間不在でも安心して仕事を預けられる環境を提供します。

---

## 🏗️ システムアーキテクチャ

Auto Developer は、会話セッションから独立した Python オーケストレーターが親となり、AIセッション（Maker / Checker）を子プロセスとして制御します。

```mermaid
flowchart TD
    subgraph "Job Orchestrator (親プロセス / 状態永続化)"
        State[("JobState (.dev/job_state.json)<br/>status / phase / checkpoint")]
        Loop["NightlyOrchestrator 自律ループ"]
        State <--> Loop
    end

    subgraph "Runner Adapter (AIエージェント起動・監視)"
        Loop -->|"Phase A: run_maker()"| Maker["Maker 実装<br/>(Antigravity / CLI / Codex)"]
        Loop -->|"Phase D: run_checker()"| Checker["Checker 独立査読<br/>(Tracked & Untracked 差分)"]
    end

    subgraph "Deterministic Gate & Evidence (物理防壁)"
        Loop -->|"Phase B"| Guard["GitGuard: 未追跡含む保護ファイル検査"]
        Loop -->|"Phase C"| Verifier["DeterministicVerifier<br/>(1回目失敗時は Maker へ再試行)"]
        Loop -->|"Phase E"| Evidence["EvidenceStore<br/>(証跡発行 & 確定コミット)"]
    end

    subgraph "Recovery & Handoff (人間への引き継ぎ)"
        Failure["2回連続失敗 / 不変条件違反"] --> Quarantine["Quarantine Branch へ退避コミット<br/>本線は直前 checkpoint へクリーン復元"]
        Quarantine --> Brief["Morning Brief<br/>(意思決定支援レポート & CLI)"]
        Crash["プロセス中断"] -.->|"--resume"| Resume["安全な再開 (残骸クリーン復元)"]
        Resume --> Loop
    end
```

---

## 📦 インストールと外部プロジェクトでの利用

Auto Developer は、本リポジトリ内だけでなく、**「Antigravity プラグイン」** または **「スタンドアロン CLI (`auto-dev`)」** のいずれの形態でも、あなたの既存プロジェクトや新しい作業フォルダに導入して利用できます。

---

### 方式 A: Antigravity プラグインとして利用する（推奨）

Auto Developer は Antigravity プラグイン規格（`plugin.json` および `skills/`）に完全準拠しているため、**GitHub リポジトリの URL を指定して直接オンラインからインストール**するか、**ローカルでシンボリックリンクを貼る**だけで利用できます。

#### 1. オンラインから直接インストールする場合（GitHub 公開時）
```bash
# Antigravity CLI からインストール
agy plugin install https://github.com/hamatz/auto_developer

# または Antigravity IDE / デスクトップアプリの UI から：
# 「Customizations」→「Plugins」→「+ Install from URL」に上記 GitHub URL を入力
```

#### 2. ローカル開発環境から登録する場合
```bash
# 1. プラグインディレクトリへシンボリックリンクを作成（初回のみ）
mkdir -p ~/.gemini/config/plugins
ln -s /path/to/auto_developer ~/.gemini/config/plugins/auto-developer

# 2. CLI も使えるように editable インストール
pip install -e /path/to/auto_developer
```

#### Antigravity チャットでの利用フロー：
1. **プロジェクト初期化**:
   新しいプロジェクトフォルダを Antigravity で開き、チャットで以下を実行します：
   ```text
   /nightly-setup
   ```
   EGM 規律ファイル群（`PLAN.md`, `SPEC.md`, `scripts/verify_local.sh`, `.gitignore`）が自動生成されます。
2. **夜間・無人自律実行**:
   `PLAN.md` にタスクを書いたら、離席時や就寝前にチャットで以下を実行します：
   ```text
   /nightly-run
   ```
   Antigravity エージェントがオーケストレーターと連携し、**[Maker実装 → 物理保護検査 → 確定検証 → 独立Checker査読 → 証跡ゲートコミット]** の厳格なサイクルを自動で直列実行します。
3. **成果提出パッケージの生成 (Evidence Bundle)**:
   開発成果や監査ログを第三者・クライアントに提出する際、チャットで以下を実行します：
   ```text
   /nightly-bundle
   ```
   機密情報（`.env` 等）を排除した完全な提出用 ZIP アーカイブが生成されます。
4. **提出成果物の独立監査・検証**:
   受け取った ZIP アーカイブの改ざんや承認状態を第三者として監査する場合：
   ```text
   /nightly-verify <path-to-bundle.zip>
   ```
   チェックサム整合性、Git Tree SHA 拘束、独立Checker の承認ログを自動監査してレポートします。

---

### 方式 B: スタンドアロン CLI (`auto-dev`) として利用する

ターミナルや外部エージェント CLI（Codex, Claude Code など）と組み合わせてコマンドラインから直接動かす場合：

```bash
# GitHub から直接インストールする場合（PyPI 公開不要）:
pip install git+https://github.com/hamatz/auto_developer.git

# またはローカルクローンから editable インストールする場合:
# git clone https://github.com/hamatz/auto_developer.git
# pip install -e auto_developer
```

#### ターミナルでの利用フロー：
```bash
# 1. 任意の作業フォルダで初期化
cd /path/to/your-project
auto-dev init

# 2. 自律パイプラインを実行
auto-dev run

# 外部エージェント CLI を Maker / Checker に指定する場合:
# auto-dev run --maker-cmd "..." --checker-cmd "..."

# 3. 翌朝、ブリーフィングを確認
auto-dev brief
```

---

## 🔰 5分で体験するクイックスタート (Hands-on Walkthrough)

まずは実際に手を動かして、Auto Developer の自律サイクルと防壁を体験してみましょう。

### 1. 前提条件と環境確認
```bash
# Python 3.10+ と Git があればすぐに動かせます
git status
bash scripts/verify_local.sh
```

### 2. ドライラン（シミュレーション実行）
Git 作業ツリーやファイルを一切変更せずに、`PLAN.md` のタスクがどう処理されるかを安全にシミュレーションします：
```bash
python3 scripts/nightly_orchestrator.py --dry-run
```

### 3. モック自律走行を体験する
簡単なサンプルタスクを使って、オーケストレーターが自動で実装・検証・査読・コミット・レポート生成を行う一連の流れを体験します：

```bash
# 一時的なブランチを作成してテスト
git checkout -b try-auto-developer

# テスト用の PLAN.md を作成
cat << 'EOF' > PLAN.md
# PLAN.md
## ステップ
- [ ] 1. サンプル計算関数の作成
EOF

# オーケストレーターを起動（Maker と Checker のコマンドを指定）
python3 scripts/nightly_orchestrator.py \
  --maker-cmd "python3 -c \"open('calc.py', 'w').write('def add(a, b): return a + b\n')\"" \
  --checker-cmd "echo 'APPROVE: Looks good'"
```

実行すると、ターミナルに以下が出力され、自動的にコミットされます：
```text
=== Starting Autonomous Orchestrator (Job: job-..., Total Tasks: 1) ===
▶ Processing Task [1]: サンプル計算関数の作成
  [Phase A: Maker] Running Maker for task 1...
  [Phase C: Verifier] Running scripts/verify_local.sh...
  [Phase D: Checker] Running independent review...
  [Phase E: Commit] Checker approved. Issuing evidence and committing...
Execution complete! Report written to NIGHTLY_REPORT.md
```

### 4. 翌朝のブリーフィングを確認する
朝起きたら、ワンコマンドで状況を確認できます：
```bash
python3 scripts/nightly_orchestrator.py morning-brief
```
ターミナルに「何が完了し、どこが安全にコミットされたか」が瞬時に表示されます。

### 5. 障害隔離（Quarantine）と中断復旧（Resume）を体験する
もしタスクが失敗した場合、**作業成果は捨てられず `quarantine/*` ブランチに保存** され、本線は直前のチェックポイントに戻ります：
```bash
# 失敗するタスクを意図的に実行
python3 scripts/nightly_orchestrator.py \
  --maker-cmd "python3 -c \"open('broken.py', 'w').write('syntax error here')\""

# 状態を確認
python3 scripts/nightly_orchestrator.py morning-brief
```
出力された選択肢（`git diff ...` や `--resume`）に従って、人間が簡単に判断・引き継ぎできます。

体験が終わったら元のブランチに戻ります：
```bash
git checkout main
git branch -D try-auto-developer
```

---

## 🛠️ CLI リファレンス (`auto-dev` / `scripts/nightly_orchestrator.py`)

`auto-dev` CLI（または互換ラッパー `python3 scripts/nightly_orchestrator.py`）で利用できるコマンド一覧です。

### 1. プロジェクト初期化
```bash
# 任意のプロジェクトで EGM 規律ファイル群を生成
auto-dev init
```

### 2. メイン自律実行 (`auto-dev run` または引数なし)
```bash
# 通常の自律実行（未指定時は MockRunner で安全に動作）
auto-dev run

# 外部エージェント CLI（Codex, Claude, カスタムスクリプト）を指定して自律実行
auto-dev run \
  --maker-cmd "python3 my_agent.py maker --task '{task_id}' --attempt '{attempt}' --err '{prev_error}'" \
  --checker-cmd "python3 my_agent.py checker --task '{task_id}'"

# プロセス中断・クラッシュからの安全な再開
auto-dev run --resume

# ドライラン（副作用なしのシミュレーション）
auto-dev run --dry-run
```

### 3. 朝次ブリーフィング (`auto-dev brief`)
```bash
# 直近の実行結果と次のアクション選択肢をコンソール表示
auto-dev brief
# （互換コマンド: auto-dev morning-brief）
```

### 4. 低レベルサブコマンド（エージェント連携・手動ステップ実行用）
```bash
# クリーンツリー検査と次タスク取得
auto-dev pre-flight

# 不変条件検査 & 検証スクリプト実行（成功時に Evidence Token 発行）
auto-dev verify-step --checkpoint <commit_hash> --task-id <id>

# 証跡を検証してコミット（未検証の場合は拒否）
auto-dev commit-step --task-id <id> --checkpoint <commit_hash>

# 作業成果を quarantine ブランチへ退避コミットし、本線をチェックポイントへ復元
auto-dev rollback-step --checkpoint <commit_hash> --task-id <id> --reason "原因"
```

---

## 🛡️ 厳格な 5 大安全ガードレール (Safety Invariants)

1. **クリーンツリー事前検証 (Pre-flight Assertion)**
   - 起動時に `git status --porcelain` を検査。未コミット作業がある場合は起動を拒否し、作業破壊を防ぎます。
2. **外部不変条件保護 (External Invariant Check)**
   - 未追跡ファイル（Untracked files）も含めて走査。保護アセット（`.github/**`, `SPEC.md`, `scripts/verify_local.sh`, 免疫テスト）の作成・変更を検知した瞬間、テスト実行前に物理遮断します。
3. **暗号学的確定ゲート (Evidence-gated Commit)**
   - `verify-step` 成功時に発行される Evidence Token がない限り、`commit-step` は Exit 3 でコミットを拒否。未検証コードの混入を物理的に排除します。
4. **破壊的ロールバックの廃止と隔離退避 (Quarantine Preservation)**
   - 失敗時にコードを `git reset` で消し去らず、`quarantine/task-<id>-*` ブランチへ退避コミット。本線の安全性と試行錯誤ログの保存を両立します。
5. **実行状態の永続化と中断復旧 (Crash Recovery & Resume)**
   - `.dev/job_state.json` にフェーズと進捗を常時記録。プロセスがクラッシュしても `--resume` で不完全ツリーを自動復元し、安全に再開できます。

---

## 📁 ディレクトリ構成

```text
auto_developer/
├── AGENTS.md                  # エージェント憲章（行動規範）
├── SPEC.md                    # プロダクト仕様書と不変条件
├── PLAN.md                    # タスク計画・ステート管理ファイル
├── LESSONS.md                 # 過去の地雷・設計ミスの外部記憶
├── PROMPTS.md                 # Maker / Checker 無記憶プロンプト集
├── docs/designs/              # EGM 承認済みの詳細設計書
├── scripts/
│   ├── nightly_orchestrator.py # 自律型親オーケストレーター & CLI
│   └── verify_local.sh        # 不変条件検査 & 決定論的検証ゲート
├── skills/
│   └── nightly-run/SKILL.md   # Antigravity ワークスペーススキル定義
├── tests/
│   └── test_nightly_orchestrator.py # 受入テストスイート (12 tests)
└── pyproject.toml             # Python パッケージ & ツール設定
```

---

## 🧪 テストの実行

```bash
# 全検証ゲートの実行（保護ファイル検査・リンター・型検査・受入テスト）
bash scripts/verify_local.sh

# Python 受入テストスイートの実行
python3 -m unittest discover -s tests -p "test_*.py"
```

---

## 📜 ライセンス

本プロジェクトは [Apache License 2.0](LICENSE) の下で公開されています。

