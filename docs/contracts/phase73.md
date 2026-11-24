# 契約書: Phase 73（v0.10.0）— 再生できるダッシュボード

<!--
SPEC.md・PLAN.md を確定する前に、人間が確認するための資料です。
作成: 2026-11-23（Phase 72 完了時点 5ee1f2a に対する分析）
範囲の決定（人間が承認、2026-11-23）: Phase 73 をダッシュボード v2（再生できるダッシュボード）にし、Spec Compiler は Phase 74 以降にずらす。
画面の文言は英語だけとする。Antigravity のプラグインの `/nightly-run` からもダッシュボードを有効にできるようにする。
デザインは docs/designs/dashboard-prototype.html（人間が承認した見本）を契約とする。
-->

## 1. このフェーズの目的

**「使ってみたい」と思わせる、夜の作業を再生できるダッシュボードを作る。**

ダッシュボードは、夜間に動かす道具では普段は見られないかもしれない。それでも、README の冒頭や紹介の投稿に貼る動画は、この道具が何をしてくれるかを数秒で伝える一番強い手段になる。Phase 72 のダッシュボードは正しい情報を出すが、見た目は表形式のレポートに近かった。

見本（docs/designs/dashboard-prototype.html）の流れ: 左に積まれた backlog のチケットが各チームに投入されて減っていき、チームの中で Maker と Checker が入れ替わりながら作業し、検証に失敗すれば新しい Maker がやり直し、右側に検証と査読を通ったコミットが積み上がり、最後に統合の関門で RC タグができる（main は変更されない）。下の時系列の帯で、夜全体を見渡し、好きな時点へ移動できる。

## 2. 要件の一覧

| ステップ | ID | 内容 | 種別 |
| --- | --- | --- | --- |
| 1 | STAT-8, DX-12 | 状態の表示の修正（監査の場所、「なし。」、重複、チームの失敗の原因）、サンプルの `.gitignore` | 修正 |
| 2 | GATE-12 | `record-review` が査読の記録の履歴を残す | 新規 |
| 3 | STAT-4 | `run` / `teams run` の経路のイベント、`task_start`、チームの ID | 新規 |
| 4 | STAT-4 | 統合のイベント | 新規 |
| 5 | STAT-5 | 時系列と backlog をスナップショットに加える（`auto-dev-status/2`） | 新規 |
| 6 | STAT-6 | 再生できるダッシュボード（見本どおり） | 新規 |
| 7 | STAT-7 | 自動更新（`--enable`、`--dashboard`、スキルの選択肢） | 新規 |
| 8 | DX-10 | `auto-dev demo --teams` | 新規 |
| 9 | DX-11 | `scripts/record_dashboard.py`（GIF の書き出し。開発用） | 新規 |

## 3. 見本を作る途中で見つかったこと

見本は、`examples/two-teams` に対して、決まった動きをする Maker と Checker で実際に `teams run` を実行した記録から作った。その途中で、次の2つが見つかった。

- **`run` / `teams run` の経路は、検証・査読・コミットのイベントを1件も書いていない**（STAT-4）。イベントを書くのは、エージェントが低レベルのコマンドを呼ぶ経路だけだった。見本では、Evidence の記録（`issued_at`、`consumed_at`、試行の記録）とコミットの時刻から、各段階の時刻を組み立て直した。
- **`examples/two-teams` で `teams run` を最後まで実行すると、統合で失敗する**（DX-12）。サンプルの `.gitignore` に `NIGHTLY_REPORT.md` がなく、worktree が汚れていると判定される。README のテスト（DX-1）は `teams prepare` までしか確かめていなかった。

あわせて、Phase 72 の `status` の不具合（STAT-8）と、Checker の出力の置き場所の問題（GATE-12）を直す。Checker の出力の置き場所は、PLAN.md で2フェーズ続けて指定したのに守られなかった。最初は「作業ディレクトリの外の出力を拒否する」案を考えたが、免疫テストを含む既存テストの多くが、作業ディレクトリの外に出力を置いている（3.2）。そこで、置き場所を縛るのではなく、`record-review` が記録のたびに写しを履歴として残す形にした。目的（やり直しの履歴を後から確かめられること）は、これで満たせる。

## 4. 影響分析

### 4.0 入口の列挙

1. `auto-dev`（`status`, `dashboard`, `demo`, `run`, `teams run`, `pre-flight`, `record-review`）
2. `python -m auto_developer.cli`
3. `scripts/nightly_orchestrator.py`
4. `main([...])` の直接呼び出し
5. スキル（`skills/nightly-run/SKILL.md`、`skills/nightly-setup/SKILL.md`）
6. `docs/cli.md`（DX-4。サブコマンドや引数を変えたステップでは、同じステップで作り直す）
7. README（このフェーズでは変えない。リリースのフェーズで、`demo --teams` と GIF を載せる）

### 4.1 STAT-8・DX-12（ステップ 1）

- **指紋**: `status.py` の `_read_audit_stage`（`.dev/audit_stage.json` を読んでいる）、判断待ちの節の読み取り、`attention[]` の組み立て、チームの失敗の表示。
- **変更する既存テスト**: `tests/test_stat1_status_single.py` は、準備で `audit_stage.json` を `.dev/` に書いている（不具合をそのまま前提にしている）。準備で書く場所を、Evidence の記録の場所（`.git/nightly_evidence/records/`）に変える。アサーションは変えない。
- **DX-12**: サンプルの `.gitignore` が CORE-11 の行（`cli.py` の `DEFAULT_GITIGNORE_ENTRIES`）をすべて含むことを確かめるテストを置く。`tests/test_dx3_two_teams_example.py` は変えない。

### 4.2 GATE-12（ステップ 2）

- **指紋**: `evidence.py` の `record_review`（`reviews/task_<id>/` に写しを置き、`reviews/task_<id>.json` を上書きしている）。
- 最新の記録の場所と形式は変えない。GATE-6（`commit-step` の照合）と BDL-4（Bundle に入れる写し）は、最新の記録だけを使う。履歴は `reviews/task_<id>/history/<連番>/` に足すだけにする。
- **Bundle への影響**: Bundle には、今までどおり最新の写し（3つのファイル）だけを入れる。履歴のディレクトリを Bundle に入れないことを確かめる（`bundler.py` が `reviews/task_<id>/` の下をまとめて写していないか確かめる）。
- **守るべき既存テスト（変更しない）**: `tests/test_gate5_review_evidence.py`、`tests/immunity/test_immunity_review_evidence.py`、`tests/test_bundler.py` の BDL-4 の部分。

### 4.3 STAT-4（ステップ 3・4）

- **指紋**: `orchestrator.py` の Maker・検証・Checker・コミット・隔離の各段階、`multi_team.py` と `integration_gate.py` の統合の各段階、`metrics.py` の `MetricsLog.record_event`、`cli.py` の `pre-flight`。
- **計測への影響**: `metrics` の集計は、イベントを数える。`run` の経路でイベントを書くようになると、`run` で実行したフェーズの計測の値が変わる（今まで0だったものが数えられる）。低レベルのコマンドの経路で二重に数えないことを確かめる（`run` の経路は低レベルのコマンドを呼ばないので、重ならないはず）。
- **既存テストへの影響の見込み**: `events.jsonl` の件数や中身を確かめているテストがあると、`run` の経路でイベントが増えた分だけ食い違う。検索すること: `events.jsonl` を読むテスト、`record_event` をモックしているテスト。**見つかって失敗した場合は、止まって報告する**（件数の期待を変えてよいかは、人間が判断する）。
- `task_start` は、`pre-flight` が呼ばれるたびに書かれる。同じタスクについて何度も書かれうるので、時系列（STAT-5）では「直前の記録の後の最初の `task_start`」を開始とみなす。

### 4.4 STAT-5（ステップ 5）

- **変更する既存テスト**: `tests/test_stat1_status_single.py` の `schema` の期待（`auto-dev-status/1` → `auto-dev-status/2`）。
- 行数は `git show --numstat` で求める。マージのコミットは行数を数えない。
- 「最新のジョブ」の決め方: チームでは `.dev/integration_jobs/` の最新のジョブ、単一チームでは計画ファイルのタスクに対応する記録。

### 4.5 STAT-6（ステップ 6）

- **契約**: `docs/designs/dashboard-prototype.html`。実装は、スナップショット（STAT-5）から、見本と同じ構成の HTML を作る。見本の JavaScript をひな形として使ってよい。
- **変更する既存テスト**: `tests/test_stat2_dashboard.py` は、新しいデザインの構造に合わせて書き直してよい。ただし、次の確認は残す: 1枚で完結すること（外部の読み込みがない）、文字列のエスケープ、`attention[]` が最上部にあること、`schema` の不一致の表示、`--watch` での書き直しと終了。
- **新しいテスト**: 見本の主な要素（backlog、チームのノード、統合の関門と「main は変更されない」の表示、コミットの積み上がり、出来事の記録、時系列の帯、再生の操作）が出力にあること。単一チームの形。時系列が空のとき。`<script>` を含む題名がエスケープされ、埋め込んだ JSON が `</script>` で途切れないこと。

### 4.6 STAT-7（ステップ 7）

- **指紋**: `MetricsLog.record_event` の書き込みの後（STAT-4 のイベントの出口）。
- イベントを書くたびに HTML を作り直すと、`run` の速さに影響しうる。作り直しは、イベントの書き込みと同じ処理の中で、失敗を握りつぶさず警告にとどめる。テストでは、有効のとき作り直されること、無効のとき作られないこと、作成の失敗が `commit-step` の終了コードを変えないことを確かめる（INV-8 の免疫テスト `test_immunity_metrics_non_interference.py` は変えずに通ること）。
- チームの worktree からは、元のリポジトリの `.dev/dashboard.json` を読む（worktree の `.dev/` ではない）。
- スキル: `skills/nightly-run/SKILL.md` と `skills/nightly-setup/SKILL.md` の変更。スキルの内容を確かめている既存テスト（`tests/test_skills_teams.py` など）が、文言の一致を確かめていないかを確認する。失敗した場合は止まって報告する。

### 4.7 DX-10・DX-11（ステップ 8・9）

- DX-10 は、DX-6（`demo`）の作りを使う。デモ用の Maker と Checker は、製品の中のデモ専用の実装とし、外部のコマンドを呼ばない。既存の `demo` の挙動とテスト（`tests/test_dx6_demo.py`、`tests/test_dx8_*.py`）は変えない。
- DX-11 は開発用のスクリプトで、製品のパッケージに含めない。テストは、必要なライブラリがない環境で、入れ方を表示して終了コード 2 になることだけを確かめる（CI にブラウザを入れない）。

## 5. 人間が行うこと

- 準備のコミット: SPEC.md、PLAN.md、BACKLOG.md、この契約書、docs/designs/dashboard-prototype.html。
- 準備のコミットの前に: Phase 72 の Evidence の記録と査読の記録を `.git/nightly_evidence/archive/phase72/` に退かせる（済み）。
- ステップ 6 の後: このリポジトリと `examples/two-teams` のダッシュボードを開き、見本と比べて目で確かめる。
- ステップ 9 の後: Linux での全テスト、CI、査読の記録の突き合わせ（今回からは履歴も含めて）、Bundle の検証、`auto-dev demo --teams` の実行と GIF の書き出しの確認。
