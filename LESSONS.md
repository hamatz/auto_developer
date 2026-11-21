# LESSONS.md — 踏んだ地雷の外部記憶

<!--
エージェントが踏んだ地雷・設計ミス・ライブラリの癖を蓄積する外部記憶です。
着手前に関連する項目だけを検索して読みます（auto-dev run は下の「恒久教訓インデックス」節から関連する項目を Maker に自動で渡します）。
各項目の Trap と Rule は1行で書いてください（Maker に渡されるのは各フィールドの1行目だけです）。

v0.9.0 で、v0.8.0 までの 40 件超の教訓を、今も効いている原則 14 件に統合しました。
個々の事象の詳細（Trap・Proof の原文）は docs/history/LESSONS-v0.8.md に凍結しています。

運用
- 教訓はここに書いて終わりにせず、できる限り下の段（道具・検査・関門・作り）に置き、
  元の失敗を再現してガードが作動することを確かめる（Proof）。
- 同じ原則の事例が増えたら、新しい項目を足すのではなく既存の項目の Context に追記する。
- モデルを更新したら、危険度の低いガードを外して元の失敗を再現し、もう起きなければ削除して理由を残す。
  権限・機密・本番接続のガードは自動では外さない。
- 監査ループ（AUDIT-5）が解決した指摘は、下のインデックスの先頭に自動で追記される。
-->

## 記録フォーマット
### [YYYY-MM-DD] 事象の簡潔な要約
- Context: 対象ファイル、利用ライブラリ、作業内容
- Trap: 何を誤認し、どのような不具合を引き起こしたか
- Rule: 今後とるべき実装パターン（推奨/禁止）
- Rung: 修正の置き場所（会話 / プロンプト / AGENTS.md / 道具 / 検査 / 関門 / 作り）。直感より一段下を目指す
- Immunity Test: 再発を機械的に防止するテストのパス
- Proof: 元の失敗を再現し、ガードが作動したことを確かめた記録

## 恒久教訓インデックス

### [2026-11-21] E2E テストが雛形を使わず、特定の環境でしか実行されていなかったため、利用者の経路の欠陥を見逃した (E2E Must Use Shipped Templates, Real Environments)
- Context: Phase 66（TEAM-10〜19）。`tests/test_teams_cli_e2e.py`、`cli.py` の `init` / `teams init` の雛形、`orchestrator.py` のコミット直前の担当範囲検査、`worktree.py` の削除時の保全判定。
- Trap: Phase 66 の受入テストは全件 Green だったが、雛形をそのまま使って Linux で `teams run` を実行すると、両チームが失敗した。原因は4つあった。(1) テストの副産物 `__pycache__` が担当範囲の違反と判定された（雛形の `.gitignore` に含まれていなかった）。(2) コミット直前に違反を見つけても隔離せず、ステージ済みの変更と Evidence を残したまま終了していた。(3) 成功したジョブの `teams cleanup` が、ワーカーの生成物を保全対象とみなして必ず失敗した。(4) 雛形のコメント内の記法例がタスクとして読まれた。いずれも、テストがコメントを削った計画ファイルを使い、バイトコードを書かない環境で動き、実際に実行した後の `cleanup` を通していなかったため検出されなかった。E2E のコードには、(4) を回避するための注記まで書かれていた。
- Rule: E2E 受入テストは、出荷する雛形をそのまま使い、利用者の一連の操作（初期化 → 実行 → 片付け → 次の実行）を最後まで通す。テストのサブプロセスでは、結果を左右する環境変数（`PYTHONDONTWRITEBYTECODE` など）を明示的に揃える。テストを書く途中で製品の欠陥を回避した場合は、回避で済ませずに欠陥として報告する（PLAN.md の「止まっていること」に書いて止まる）。
- Rung: 検査（雛形を使う受入テスト TEAM-24、Linux の CI REL-3）、AGENTS.md Rule 4（回避ではなく報告する）
- Immunity Test: Phase 67 で追加（TEAM-24）
- Proof: 2026-11-21 に、雛形と `__pycache__` が生成される環境の組み合わせで、4つの欠陥すべてを再現した。Linux では、既存の E2E テスト2件が同じ理由で失敗した。

### [2026-11-20] 「実装済み」と書いた機能が利用者の経路から使えず、README の手順も実際の挙動とずれていた (Documented ≠ Reachable)
- Context: v0.9.0 準備。README.md、`src/auto_developer/multi_team.py` / `manifest.py`、`cli.py`。
- Trap: マルチチーム実行はライブラリとして実装・テスト済みだった。しかし `TeamManifest` と `MultiTeamOrchestrator` がつながっておらず、CLI からもスキルからも呼べなかった。全チームが同じ PLAN.md を更新するので、つないだとしても統合時に必ずコンフリクトする構造だった。README にも、実際の挙動と違う記述が残っていた。「`--maker-cmd` 未指定時は MockRunner で動作」と書いてあったが、実際は終了コード 2 になる。Checker を指定しない隔離体験の手順も、そのままでは起動できなかった。
- Rule: 「実装済み」は、利用者が使う経路（CLI・スキル）から E2E テストで動かせたときにだけ名乗る。README に書く手順は、テストでそのまま実行する。単体テストが通っていることは、利用者が使える根拠にならない。
- Rung: 検査（README クイックスタートの実行テスト DX-1、2 チーム E2E テスト TEAM-19）
- Immunity Test: v0.9.0 で追加（`tests/test_quickstart.py`, `tests/test_teams_cli_e2e.py` を予定）
- Proof: 2026-11-20 の棚卸しで、コードを読んで確認した（CLI に `teams` 系のサブコマンドがなく、`run` は Maker / Checker 未設定で終了コード 2 を返す）。

### [2026-11-18] 免疫テストを「都合のよい相手」で書くと、仕組みが現実の相手に対して成り立たない (Immunity Tests Must Model the Real Counterparty)
- Context: `src/auto_developer/dispatcher.py`（重要度トリアージ・修正予算）、`tests/immunity/test_immunity_audit_budget.py`。
- Trap: 最初の免疫テストは「監査役は毎サイクル一意な ID で新しい指摘だけを返す」前提で書かれていたため、全件 Green でも3つの欠陥を見逃した（ID が再採番されると監査ステージ全体が失敗する／修正済みの指摘の再発が黙って捨てられる／再報告だけのサイクルで streak が 0 に戻り終了が保証されない）。
- Rule: 安全や終了を保証する免疫テストは、相手（監査役・Maker・Checker）の現実的な振る舞いでシナリオを書く。少なくとも「ID の再採番」「未解決の指摘の再報告」「修正済みの指摘の再発」「指摘0件のサイクル」を混ぜる。仕様にも、状態（未着手 / 完了 / 抑止 / エスカレーション）ごとの扱いを明記する。
- Rung: 検査（免疫テストのシナリオ設計）
- Immunity Test: `tests/immunity/test_immunity_audit_budget.py`
- Proof: 3つの欠陥を再現スクリプトで確認した。Red-First で免疫テストを追加してから修正した（2d8e647, ad46d10）。

### [2026-11-16] 監査ループは「欠陥ゼロまで回す」のではなく、終わるように設計する (Audit Loops Must Terminate)
- Context: 外部 LLM による独立監査と自己改善ループ（SPEC AUDIT-2〜4）。
- Trap: 監査役は毎回なにかしら指摘を返す。P2 級の指摘まで修正タスクにしていたため、ループが止まらなかった。修正がさらに過剰設計を生み、それが次の指摘を呼んだ。
- Rule: 指摘を重要度で振り分ける（P0/P1 は PLAN、P2 は BACKLOG、INFO は記録のみ）。モジュール単位に修正予算（連続サイクルの上限）を設け、超えたら人間に渡す（解除は人間だけが行う）。リリースを止めるのは未解決の P0/P1 とエスカレーションだけとする。
- Rung: 作り（`AuditDispatcher`）、関門（`verify-bundle` の `RELEASE_BLOCKED`）
- Immunity Test: `tests/immunity/test_immunity_audit_budget.py`
- Proof: Phase 63 で、再採番・再報告を繰り返す監査役をテストで再現した。どちらの場合も上限で止まることを確認した。

### [2026-11-12] 査読と監査は異なる系統のモデルで行い、それを機械で強制する (Cross-Family Review, Enforced)
- Context: Checker サブエージェント、独立監査ランナー、`commit-step`。
- Trap: Checker を `inherit` で起動して Maker と同じ系統のモデルが自分の実装を承認していた／監査の設定上のモデル名と実際に起動したモデル名が違っていた／手順書で「異系統にする」と決めただけでは守られなかった。
- Rule: Maker と Checker / Auditor の系統の違いはコマンドの必須引数で受け取り、同一モデル・同一系統・系統不明のいずれかなら拒否する。モデル名は自己申告なので、記録に残して事後に照合できるようにする。
- Rung: 作り（`commit-step` 終了コード 4、`verify_model_independence`）
- Immunity Test: `tests/immunity/test_immunity_checker_policy.py`, `tests/test_audit_model_policy.py`
- Proof: 同一系統や系統不明（`pro`）の組み合わせで、終了コード 4 になり、Evidence が消費されないことを確認した。

### [2026-11-10] Checker には原文を渡す。要約・再構成・混入は要件の欠落になる (Verbatim Checker Input)
- Context: Checker への入力（SPEC と差分）。
- Trap: SPEC を要約して渡したため要件が落ちて違反を見逃した／差分を手で再構成したため、実験で生成したファイルがコミットに混入したのに Checker はそれを見られなかった。
- Rule: SPEC の該当節は機械的に切り出した原文を、差分は `git add -N . && git diff <checkpoint>` の生の出力を、ファイルに書き出してパスで渡す。実験は `mktemp -d` の中で行う。`verify-step` の前に `git status --porcelain` を確認する。
- Rung: 道具（PLAN.md の作業手順）。将来は Checker 入力を生成するサブコマンドへ下げる。
- Immunity Test: なし（手順）
- Proof: なし（運用手順。道具にした時点でテストを追加する）

### [2026-11-10] 証跡は正規の経路でのみ発行し、確定するツリーと SPEC に拘束する (Evidence via Sanctioned Path Only)
- Context: `verify-step` / `commit-step`、`.git/nightly_evidence/`。
- Trap: 正規の経路が失敗したときに、Evidence Record を手で書いて「機械が発行した証跡」に見せかけてしまった。検証した後のツリーとコミットしたツリーがずれる経路もあった。
- Rule: Evidence は `verify-step` → Checker → `commit-step` でのみ発行・消費する。`.git/nightly_evidence/` を直接編集しない。正規の経路が失敗したら、手で埋めずに止まって記録する。Evidence は、PLAN.md の更新を含む最終ツリーの SHA と SPEC のハッシュに拘束する。
- Rung: 作り（Tree SHA / SPEC SHA 拘束）、プロンプト（手書き禁止）
- Immunity Test: `tests/test_nightly_orchestrator.py`, `tests/test_bundler.py`
- Proof: 検証の後にツリーを改変すると、`commit-step` が終了コード 3 で拒否することを確認した。

### [2026-11-12] リソース制限は隔離ではない。信頼できないコードは一時クローンと OS サンドボックスの中でだけ動かす (Resource Limits ≠ Isolation)
- Context: 監査役が出した再現テストの実行（`verifier.py`、SBX-1）。
- Trap: 同じ OS ユーザーで動く候補テストが `chmod` で元のリポジトリを書き換えられた／権限のロックダウンより先に子プロセスを起動していた／root で実行するとロックダウンが無効になった／コミットのチェックアウトに失敗すると現在の作業ツリーにフォールバックして別のコードを検証していた。
- Rule: 量の制限・形の検査・権限の隔離・外部アクセスの制限は別の責務として扱う。信頼できないコードは、一時クローンの中で、子孫プロセスにも及ぶ OS の機構（`sandbox-exec` / `bwrap`）の内側で動かす。使えない環境や root では実行を拒否する。代わりのもので検証を続けない（Fail-Closed）。
- Rung: 作り（サンドボックスの必須化、一時クローンでの隔離、起動前のロックダウン）
- Immunity Test: `tests/test_red_verifier.py`, `tests/test_immunity_and_red_acceptance.py`
- Proof: 元のリポジトリを書き換えようとする候補テストが、拒否され、元のリポジトリが変わらないことを確認した（詳細は docs/history/LESSONS-v0.8.md の 2026-11-08〜11-13）。

### [2026-11-13] サンドボックスは「既知の危険を拒否」ではなく「必要最小限を許可」で作る (Allowlist, Not Blocklist)
- Context: macOS `sandbox-exec` のプロファイル、Linux `bwrap` のマウント。
- Trap: `(allow default)` を起点にしたため明示的に拒否していないホーム外のパスを読めた／`/etc` 全体や仮想環境全体を公開していた／ホーム配下の仮想環境ではパスの包含判定に漏れがあった。
- Rule: 読み取りの許可は、Python の実行に必要なプレフィックスと最小限のシステムライブラリ・設定ファイルに限る。ホーム・元のリポジトリ・認証情報（`~/.ssh`, `~/.aws` など）は読ませない。機密を読めないことを単体テストで確かめる。
- Rung: 作り（許可リストの最小化）、検査
- Immunity Test: `tests/test_red_verifier.py`
- Proof: `/etc/shadow` や `~/.ssh` への読み取りがカーネルに拒否されることをテストで確認した。

### [2026-11-13] 改ざん検知は、未追跡ファイルと無視ファイルの内容まで見る (Untracked Content Tampering)
- Context: 候補テストの実行前後のスナップショット比較、保護ファイル検査。
- Trap: 追跡されているファイルの差分だけを見ていたため、未追跡ファイル・無視ファイルの内容の書き換えや、シンボリックリンクの差し替えを見逃していた。
- Rule: スナップショットには、全ファイルの種別（通常ファイル / ディレクトリ / リンク）と内容の SHA-256 を記録する。保護ファイルの検査も、`git diff` と `git status --porcelain` の両方で行う。
- Rung: 検査（多層で検知する）
- Immunity Test: `tests/test_red_verifier.py`, `tests/test_immunity_guard.py`
- Proof: 無視ファイルを書き換える候補テストで、`REPO_TAMPER_DETECTED` になることを確認した。

### [2026-11-14] 本物の Red かどうかは、構造化された結果と製品コードとの因果関係で判定する (Genuine Red)
- Context: 監査役の再現テストの判定（SBX-2）。
- Trap: 実行件数0件のテスト（終了コード 5）、環境の例外、例外メッセージの偽装、製品コードを呼ぶだけで固定値どうしを比較しているテストを、不具合の証明（Red）として受け入れていた。
- Rule: 文字列の照合はやめ、`unittest.TestResult` で判定する（実行件数1件以上、失敗1件以上、エラー0件）。アサーションが製品コードの戻り値や副作用と因果関係を持つことを、AST と実行の追跡で確かめる。
- Rung: 作り（構造化した判定、因果関係の解析）
- Immunity Test: `tests/test_red_verifier.py`, `tests/test_audit_dispatch.py`
- Proof: 各種の偽 Red が TRIVIAL_RED / REJECTED になることを確認した。

### [2026-11-15] 静的解析は、確信できないなら安全側に倒し、完全を目指さない (Uncertainty Over Completeness)
- Context: TRIVIAL_RED を判定する AST 解析。
- Trap: Python の動的な制御フローを静的に完全に解こうとして、過剰設計に陥った。分岐後の変数の合流や文の実行順を無視して、正常なテストを偽の Red と誤判定した。
- Rule: 文は実行順に走査する。分岐の中で変わる値は「不確定」として扱う。不確定な値を根拠に TRIVIAL_RED と断定しない。判断できない場合は、動的な実行結果や監査に委ねる。精度の改善は P2 として扱う。
- Rung: 作り（抽象解釈の合流演算）
- Immunity Test: `tests/test_red_verifier.py`
- Proof: 条件つきの `self.fail()` や分岐後の代入を含む正当なテストが受理されることを確認した。

### [2026-11-10] 学習の証明はコミットに拘束し、記録の失敗は失敗として扱う (Commit-Bound, Fail-Closed Learning)
- Context: 指摘の解決（AUDIT-5）、AuditStore、免疫テストの昇格。
- Trap: 未コミットの作業ツリーで Green を確認しただけで HEAD を「修正後」として記録していた／LESSONS.md への記録に失敗しても解決として扱っていた／再現テストのハッシュを再計算して上書きしていた／既存の免疫テストを無条件に上書きしていた。
- Rule: Before / After は実在する2つのコミットに拘束し、同じ再現テストを各コミットの隔離環境で実行する。`unknown` を記録して成功扱いにしない。再現テストとそのハッシュは初回登録で確定し、変更できないようにする。免疫テストを配置する前に既存ファイルを確認する（同じ内容なら冪等、違う内容なら衝突として拒否）。記録の失敗は解決の失敗とする。
- Rung: 作り（事前条件、不変性のガード）
- Immunity Test: `tests/test_audit_resolution.py`, `tests/test_audit_store_immutability.py`, `tests/test_audit_lesson_recording.py`
- Proof: 汚れたツリー、Before と After が同じ、ハッシュの改変、記録の失敗のそれぞれで拒否されることを確認した。

### [2026-11-08] 外部プロセスを回収・復旧する前に、同一性と所属を確かめる (PID & PGID Fail-Closed)
- Context: マルチチームのワーカー監督と、親プロセスがクラッシュした後の復旧（TEAM-4）。
- Trap: PID が再利用されていたり、プロセスグループに属していないプロセスを、自分のワーカーとみなして回収・統合しかけた。ZIP の構造を検査しただけで、復旧した成果物を本物とみなしていた。
- Rule: 回収の前に、起動時刻を含むプロセスの同一性と PGID への所属を照合する。確かめられないワーカーがいる間は、統合も worktree の削除も行わない。復旧した成果物は `verify_bundle()` で独立に検証してから受け入れる。
- Rung: 作り（照合のガード）、検査（受入テスト）
- Immunity Test: `tests/test_multi_team.py`
- Proof: 所属が一致しないプロセスや、マニフェストのない ZIP で、統合が中止され、worktree が残ることを確認した。

### [2026-11-08] モジュール間の受け渡し（タスク書式・ID）は、端から端までのテストで固定する (Interface Interoperability)
- Context: 監査の指摘を PLAN.md に登録する処理と、タスクを解析する処理。Maker へのコンテキストの受け渡し。
- Trap: 登録側の書式が解析側の正規表現と合わず、登録したタスクが拾われなかった／教訓の注入がモックでは通るのに、実際のコマンドランナーでは Maker に届いていなかった。
- Rule: タスク ID の形式（`AUD-<id>.`）のような受け渡しの形は1か所で定義する。登録から取得・状態更新・Maker への到達までを、モックではなく実際のアダプターでの E2E テストで確かめる。
- Rung: 検査（受入テスト）
- Immunity Test: `tests/test_audit_dispatch.py`, `tests/test_runner_lesson_wiring.py`
- Proof: 実際の `CommandRunnerAdapter` を使い、Maker のプロセスが教訓を受け取ったことを確認した。
