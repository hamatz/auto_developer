# Repository Lessons Learned (AI Operational Memory)

<!--
エージェントが踏んだ地雷・設計ミス・ライブラリの癖を蓄積する外部記憶。
プロンプトに常時全文を入れるのではなく、着手時に関連する項目だけを検索して読む。
教訓はここに書いて終わりにせず、できる限り下の段（検査・関門・作り）に置き、
元の失敗を再現してガードが作動することを確かめる（Proof）。
モデルを更新したら、危険度の低いガードを外して元の失敗を再現し、
もう起きなければ削除して理由をここに残す。権限・機密・本番接続のガードは自動では外さない。
以下の2件は記入例です。プロジェクトに合わせて書き換えてください。
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

### [2026-11-08] 同一OSユーザー環境でのファイル権限ロックダウンの限界と一時クローン隔離 (Physical Clone Isolation)
- Context: `src/auto_developer/verifier.py` (`RedTestVerifier._execute_test`)
- Trap: `0o555`/`0o444` によるOSパーミッション剥奪を行っても、テストプロセスが元リポジトリと同じOSユーザーで実行されている限り、テストメソッド内で `os.chmod()` を呼べば書き込み権限を復活させて元リポジトリを改変できてしまい、AST制限やchmodの多重化では根本解決にならなかった。
- Rule: 信頼できないコードの実行では、元リポジトリのパス上で直接実行してはならない。必ず独立した検証用一時クローン（または分離Worktree）を生成し、その閉じた環境内で実行することで、テストが内部でどんなchmodやファイル書き換えを行おうと、元リポジトリの物理的不変性を完全に保証する。
- Rung: 作り（物理的一時クローン隔離） & 検査
- Immunity Test: `tests/test_red_verifier.py`, `tests/test_immunity_and_red_acceptance.py`
- Proof: テストメソッド内で `os.chmod` を呼んで元リポジトリの改変を試みるテストを実行させても、元リポジトリが物理的に無傷であることを実証。

### [2026-11-08] パイプライン連携モジュール間でのタスク書式・IDスキーマの断絶 (Task Format Interoperability)
- Context: `src/auto_developer/dispatcher.py` (`TaskDispatcher` vs `AuditDispatcher`)
- Trap: `AuditDispatcher` が出力するタスク書式（`- [ ] Fix: [ID] ...`）が、既存の `TaskDispatcher.TASK_PATTERN`（`- [ ] <task_id>. <title>`）と一致していなかったため、PLAN.md に登録されたタスクが `parse_plan()` で認識されず、次の Maker にタスクが渡らないというパイプライン断絶が発生した。
- Rule: タスクを自動登録するコンポーネントは、下流のディスパッチャが要求する正規表現スキーマに厳格に従い、一意なタスクIDプレフィックス（`AUD-<finding_id>.`）を付与する。連携受入テストで登録から取得・ステータス更新までをエンドツーエンドで検証する。
- Rung: 作り（タスクIDスキーマ統一） & 検査（受入テスト）
- Immunity Test: `tests/test_audit_dispatch.py`
- Proof: 登録されたタスクが `TaskDispatcher.parse_plan()` および `get_next_pending_task()` で正常に抽出され、Makerに引き渡せることを実証。

### [2026-11-08] 新規免疫テスト追加による既存ガードの弛緩・モンキーパッチ汚染 (Mutual Non-Interference)
- Context: `src/auto_developer/guard.py` (`GitGuard.check_protected_files_untouched`), `verifier.py`
- Trap: 免疫テスト追加を許可する際、新規テストファイルの中身でモジュールインポート時やトップレベルでバグ関数を正しい実装にモンキーパッチ（動的差し替え）されると、Python unittest の同一プロセス実行により既存の失敗テストが通過（FAILED → OK）してしまい、「追加がガードを緩める」という重大なセキュリティホールが存在した。
- Rule: 新規免疫テストの追加に際しては、
  1. 候補追加の前後で既存テストスイートを実行し、既存テストの成否・結果が 1 件も変化しないこと（相互不干渉 Mutual Non-Interference）を機械的に検証する。
  2. AST 検査により、候補テストのトップレベルには `import` および `class`/`def` 定義のみを許可し、動的コード実行文を一切禁止する。
- Rung: 作り（AST検査 & 不干渉検証） & 検査
- Immunity Test: `tests/test_immunity_guard.py`
- Proof: モンキーパッチを含む候補テストを追加しようとした際に、AST検査および不干渉検証により確実に拒絶されることを実証。

### [2026-11-08] root (UID 0) 実行環境における OS パーミッションロックダウン無効化 (Root Execution Fail-Closed)
- Context: `src/auto_developer/verifier.py` (`RedTestVerifier._lockdown_repo`)
- Trap: Docker コンテナや CI 等の root 権限（UID 0）環境では、ファイルパーミッション（`0o444`/`0o555`）が無視されるため、OS権限による書き込み禁止ロックダウンを迂回して絶対パスで本体ファイルが改変されてしまう穴が存在した。
- Rule: `os.geteuid() == 0`（root環境）での無防備な候補テスト実行を検知した場合は、Fail-Closed として即座に実行を拒否し、非rootユーザーまたは明示的な隔離サンドボックスでの実行を強制する。
- Rung: 作り（root検知ガード） & 検査
- Immunity Test: `tests/test_red_verifier.py`
- Proof: root 実行フラグまたは UID 0 環境で候補テストを実行しようとした際に、即座に拒絶されることを実証。

### [2026-11-08] 作業ディレクトリ隔離の不完全性とOS権限レベルの読取専用ロックダウン (OS Permission Lockdown)
- Context: `src/auto_developer/verifier.py` (`RedTestVerifier._execute_test`)
- Trap: 候補テストの作業ディレクトリを一時ディレクトリ（`cwd=sandbox_dir`）に変更しても、Pythonプロセスは本体リポジトリに対するファイルアクセス権限を保持しているため、絶対パスや `PYTHONPATH` 経由で本体のファイル（例: `canary.txt`）を書き換えてからアサーション失敗を起こす攻撃・副作用を遮断できなかった。
- Rule: 作業ディレクトリの変更に加えて、候補テスト実行中は対象リポジトリ（`repo_dir`）全体のディレクトリ・ファイルから書き込み権限を一時的に剥奪（`a-w`, `0o555`/`0o444`）してOS権限レベルで読取専用化し、絶対パスによる改変試行も `PermissionError` で物理遮断する。実行終了後はコンテキストマネージャ等で確実に元の権限へ復元する。
- Rung: 作り（OS権限ロックダウン） & 検査（受入テスト）
- Immunity Test: `tests/test_red_verifier.py`, `tests/test_immunity_and_red_acceptance.py`
- Proof: 候補テスト内から本体リポジトリの絶対パスへ書き込みを試行させ、`PermissionError` が発生して本体が一切汚染されないことを実証。

### [2026-11-08] 再現テスト候補における例外メッセージ偽装と文字列判定の限界 (Structured TestResult vs String Matching)
- Context: `src/auto_developer/verifier.py` (`RedTestVerifier._execute_test`)
- Trap: プロセスの標準エラー出力から `"AssertionError" in output` を文字列検索して判定していたため、`raise OSError("AssertionError: pretend failure")` のように環境例外や意図しないエラーのメッセージに `"AssertionError"` が含まれているだけで `GENUINE_RED` と誤認してしまう脆弱性があった。
- Rule: 標準出力/標準エラー出力の文字列検索による判定を廃止し、Python `unittest.TestResult` の構造化結果を直接検証する。真正なRedは `testsRun >= 1` かつ `len(failures) >= 1` かつ `len(errors) == 0`（予期せぬ例外や環境障害が0件）であることを厳格に要求する。
- Rung: 作り（構造化TestResult解析） & 検査
- Immunity Test: `tests/test_red_verifier.py` (`test_rejects_exception_spoofing_oserror_with_assertion_message`)
- Proof: `raise OSError("AssertionError: pretend failure")` を送出する候補テストを与えた際に `UNHANDLED_EXCEPTION` として確実に `False` 判定されることを実証。

### [2026-11-08] 再現テスト候補実行による本体作業ツリー汚染・副作用の物理隔離 (Candidate Test Isolation)
- Context: `src/auto_developer/verifier.py` (`RedTestVerifier._execute_test`)
- Trap: 監査役や外部AIから提示された未検証の再現テスト候補を `cwd=self.repo_dir` かつ現プロセスの環境変数で直接実行したため、候補テスト内のコードが本体リポジトリのファイル（例: `canary.txt`）を書き換えてからアサーション失敗を起こした場合に、本体を汚染したまま `GENUINE_RED` と判定して通過してしまう重大な脆弱性があった。
- Rule: 候補テストは本体リポジトリ内で直接実行してはならない。必ず使い捨ての一時 Worktree または隔離された独立スクラッチ環境に展開して実行し、環境変数もシークレットを除外サニタイズして本体への副作用を物理的に遮断する。
- Rung: 作り（隔離実行） & 検査（受入テスト）
- Immunity Test: `tests/test_immunity_and_red_acceptance.py` (`test_candidate_test_cannot_modify_parent_repository`)
- Proof: 意図的に本体リポジトリのファイルを改変しようとする敵対的テスト候補を実行させ、本体ファイルが一切改変されず防御されることを実証。

### [2026-11-08] unittest 0件実行 (Exit 5) および環境例外の偽Red誤判定 (Structured Red Verification)
- Context: `src/auto_developer/verifier.py` (`RedTestVerifier._execute_test`)
- Trap: `unittest discover` の終了コードが非ゼロ（`exit_code != 0`）で `ImportError` の文字列がなければ一律 `GENUINE_RED` と判定したため、テストメソッドを持たない空テスト（Exit 5: `NO TESTS RAN`）や `setUp()` での `OSError` などのテスト環境例外をバグ再現と誤認する漏れがあった。
- Rule: 単に終了コードを見るだけでなく、実行テスト件数が1件以上（`Ran N tests` where N >= 1）であること、および実際のアサーション失敗（`AssertionError` または `FAIL:`）が発生していることを構造的に検証し、0件実行や環境例外を確実に弾く。
- Rung: 作り & 検査
- Immunity Test: `tests/test_red_verifier.py` (`test_rejects_zero_tests_ran_exit_code_5`, `test_rejects_setup_environment_oserror`)
- Proof: テストメソッドのないテストクラスを与えた際に `is_reproduced=False` (`NO_TESTS_RAN`) となり、`setUp()` で `OSError` が起きた際に `SETUP_ERROR` として弾かれることを実証。

### [2026-11-08] 免疫ディレクトリへの非テスト・シンボリックリンク追加の遮断 (Strict Immunity File Validation)
- Context: `src/auto_developer/guard.py` (`GitGuard.check_protected_files_untouched`)
- Trap: `allow_immunity_additions=True` において未追跡・新規追加ファイルであれば何でも許可したため、非Pythonファイル（`not_a_test.txt`）やホスト重要ファイルを指すシンボリックリンク（`test_link.py -> /etc/hosts`）が素通りして追加されてしまう脆弱性があった。
- Rule: 免疫ディレクトリ（`tests/immunity/`）への新規追加は、「通常ファイル（`is_file() and not is_symlink()`）」かつ「テスト命名規則 `test_*.py`」を満たすファイルのみに厳格限定し、それ以外は Fail-Closed で即時遮断する。
- Rung: 作り（ガード実装） & 検査
- Immunity Test: `tests/test_immunity_guard.py` (`test_asymmetric_mode_rejects_non_test_files`, `test_asymmetric_mode_rejects_symlinks`)
- Proof: `not_a_test.txt` や `/etc/hosts` への symlink を追加しようとした際に即座に `False` で拒絶されることを実証。

### [2026-11-08] 外部プロセス自律回収時の同一性・所属関係照合と統合遮断 (PID & PGID Fail-Closed)
- Context: `src/auto_developer/multi_team.py` (`recover_multi_team_job`, `WorkerRegistry`)
- Trap: PID の OS 開始識別子照合だけを行い、Registry の PGID の正当性を検証せずに `os.killpg(w.pgid)` を送ったため、改ざんや誤設定で別プロセスの PGID が指定された場合に無関係なプロセスグループを誤停止する脆弱性があった。また、照合に失敗して停止を保留した未確認ワーカー（`unverified_processes`）が残っているのに、Evidence が揃っているだけで統合ゲートや Worktree 削除に進めてしまう漏れがあった。
- Rule:
  1. プロセスグループ停止前に `os.getpgid(w.pid) == w.pgid` で指定 PID が指定 PGID に実在・所属していることを必須照合し、不一致または照合不能な場合はシグナル送信を絶対に保留する。
  2. 未確認ワーカーが 1 つでも残っている場合は、統合処理（`IntegrationGate`）への進行を物理遮断して `RECOVERY_REQUIRED` に遷移させ、当該ワーカーの Worktree も削除せず保全する。
- Rung: 作り（ガード実装） & 検査（受入テスト）
- Immunity Test: `tests/test_multi_team.py` (`test_recover_multi_team_job_skips_when_pid_does_not_belong_to_pgid`, `test_recover_multi_team_job_aborts_integration_when_unverified_processes_exist`, `test_recover_multi_team_job_preserves_worktree_for_unverified_processes`)
- Proof: 意図的に PID と PGID の所属関係を狂わせた実プロセスを用いたテストで、シグナル送信・統合呼び出し・Worktree 削除がすべて遮断され、期待通り防御されることを実証済み。

### [2026-11-08] 復旧成果物の真正性・改ざん検証 (ZIP 構造検査 vs Evidence 独立監査)
- Context: `src/auto_developer/multi_team.py`, `bundler.py`
- Trap: `zipfile.is_zipfile()` や `testzip()` のチェックサム確認だけでは、ZIP 書式が正常であることしか分からず、`bundle_manifest.json` の欠如、Tree SHA 不一致、Checker 承認レコードの有無といった真正性・改ざんの検証が素通りしてしまう。
- Rule: Evidence 成果物の受け入れ・復旧には必ず `EvidenceBundler.verify_bundle()` を適用し、`bundle_manifest.json`、全ファイル SHA-256、Git Tree、SPEC ハッシュ、Checker 承認レコードのすべてが `VERIFIED` であることを独立監査する。
- Rung: 作り & 検査
- Immunity Test: `tests/test_multi_team.py` (`test_recover_multi_team_job_rejects_zip_without_bundle_manifest`)
- Proof: マニフェストを持たないダミー ZIP が `INVALID_EVIDENCE` として厳格に拒絶されることを実証済み。

### [記入例] 非同期処理とトランザクションのスコープ
- Context: IndexedDB 操作全般
- Trap: transaction のスコープ内で外部の fetch を await したため、トランザクションが自動コミットされ例外が発生した
- Rule: トランザクション内に非同期I/Oを混在させない。必要なデータを取得し終えてから書き込む
- Rung: 検査
- Immunity Test: tests/storage_transaction.test.ts
- Proof: 修正前のコードでこのテストが失敗することを確認済み

### [記入例] 縦組み紙面で図版がページをまたぐ
- Context: 紙面モード（ページ送り）の組版。図版・コード図版を本文に回り込ませる処理
- Trap: 図版の前に余白を足して次のページへ押したところ、本文がその余白まで避け、別の図版が境目をまたいだ。
  スクリーンショットで確認しながら直したため、直した崩れが別の修正で崩れ、一部の判型でだけ直っていることもあり、20回以上往復した
- Rule: 図版は余白で押さず、本文中のページの切れ目まで運ぶ。運べないときだけ詰め物を挿む。
  合否は目視ではなく位置の検査で決め、全判型・全組方向（8通り）に掛ける
- Rung: 検査（CI の必須チェック）
- Immunity Test: examples/compositor/tests/verify_layout.mjs、verify_matrix.mjs
- Proof: 8通りすべてで、壊した版が Exit 1 になることを確認済み（examples/compositor/tests/selftest.mjs）
