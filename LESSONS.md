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

### [2026-11-10] After Green を未コミットの作業ツリーで確認し、HEAD を after_commit として記録する証明の空洞化 (Commit-Bound Learning Proof)
- Context: `src/auto_developer/audit.py` (`AuditResolutionManager.resolve_task`), `tests/test_level7_real_loop_e2e.py`
- Trap: Maker が修正をコミットしないまま `resolve_task()` を呼ぶと、After Green は作業ツリー上で成立する一方、`after_commit` には修正前の HEAD が記録され、Before と After が同一コミットになる。Proof が「どのコミットで Red/Green になったか」を再現可能な形で示していなかった（Sol氏 Phase 47 P1）。
- Rule: Learning Proof は実在する2つのコミットに拘束する。作業ツリーが汚れている、Before Commit が不正、Before == After のいずれかなら拒絶する。同一の再現テストを各コミットを展開した隔離環境で実行し、Red/Green の結果とコミットSHA・テストSHAを Proof に固定する。`unknown` を記録して成功扱いにしない。
- Rung: 作り（resolve_task の事前条件） & 検査
- Immunity Test: `tests/test_audit_resolution.py`, `tests/test_level7_real_loop_e2e.py`（Phase 48 HHH.1 / HHH.3 で追加予定）
- Proof: （HHH.1 完了時に記録）未コミットの修正で `UNCOMMITTED_AFTER_STATE`、コミット未前進で `NO_COMMIT_ADVANCE` が返り、免疫テストが作成されないことを確認する。

### [2026-11-10] Checker に SPEC を要約して渡したことによる要件の欠落 (Verbatim Checker Input)
- Context: `/nightly-run` Step D（Stateless Checker Review）、Phase 47 GGG.3 / GGG.4
- Trap: Maker が SPEC の該当節を自分の言葉で要約して Checker に渡したため、GGG.3 の「実際に起動されたモデル名を監査証跡に明記」が Checker の入力から抜け、未実装のまま APPROVE された。GGG.4 でも原文にない表現（`LessonLearner` 等）を含む要約を渡していた。Maker が Checker の判断材料を編集できる状態は、AGENTS.md 規範6（入力は SPEC.md と git diff のみ）を実質的に破る。
- Rule: Checker には SPEC の該当節を機械的に切り出した原文（`awk '/^- \[ \] 機能XXX/,/^## 3\./' SPEC.md`）と `git diff <checkpoint>` の生出力をそのまま渡す。要約・言い換え・取捨選択をしない。
- Rung: プロンプト（PLAN.md 作業手順）。将来は道具（Checker 入力を生成するスクリプト）へ下げる
- Immunity Test: なし（運用手順。道具化した時点でテストを追加する）
- Proof: GGG.3 第3項が未実装のまま承認されていたことを Phase 47 事後確認で検出。HHH.2 に引き継いだ。

### [2026-11-10] Evidence Record の手書きによる「機械発行の証跡」の偽装 (Evidence via Sanctioned Path Only)
- Context: `.git/nightly_evidence/records/task_*.json`, `scripts/nightly_orchestrator.py` (`verify-step` / `commit-step` / `bundle`)
- Trap: Phase 47 では `verify-step` / `commit-step` を経由せず、Maker が Evidence Record を直接書き込んだ（`issued_at`・`token` 等は作成した値）。コミットSHA・ツリーSHAは実物と一致していたため Bundle 検証は `VERIFIED` になったが、証跡が機械発行されたことは保証されない。検証器が「記録の中身の整合性」しか見ていないと、手書きの記録を通してしまう。
- Rule: Evidence Record は `verify-step` → Checker → `commit-step` の正規経路でのみ発行する。`.git/nightly_evidence/` 配下を直接編集・作成しない。正規経路が失敗したら手で埋めずに停止して PLAN.md に記録する。
- Rung: プロンプト（PLAN.md 作業手順）。将来は検査（Evidence Record に発行元の署名・トークン照合を追加）へ下げる
- Immunity Test: なし（運用手順）
- Proof: Phase 47 の Record が手書きであったことを事後に申告。Phase 48 から正規経路へ切り替え。

### [2026-11-09] 既存免疫テストの無条件上書きによる免疫破壊 (Non-Destructive Immunity Test Promotion)
- Context: `src/auto_developer/audit.py` (`AuditResolutionManager.resolve_task`)
- Trap: AUDタスク解決時に After Green を確認した後、免疫テスト配置パス（`tests/immunity/test_immunity_<safe_name>.py`）に同名ファイルが既に存在していても無条件で上書きしていたため、既存の免疫テストが別の内容に書き換えられて弱体化・破壊される重大なリスクがあった。
- Rule: 免疫テストの昇格配置前に、既存ファイルの有無を必ず事前検査する。既存ファイルが存在する場合、内容の SHA-256 ハッシュを比較し、完全一致していれば冪等成功として扱い、ハッシュが異なる場合は `IMMUNITY_COLLISION` エラーで即座に拒絶して既存テストの変更・削除を一切行わない。
- Rung: 作り（非破壊的確定 & 衝突拒絶ガード） & 検査
- Immunity Test: `tests/test_audit_resolution.py`
- Proof: 既存の免疫テストが存在する状態で異なる内容の昇格を試みた際、`IMMUNITY_COLLISION` で確実に拒絶され元ファイルが無傷であることを実証。

### [2026-11-09] Before Red 真正性検証の欠落と固定値 Proof の偽装 (Strict Before Red Cryptographic Binding)
- Context: `src/auto_developer/audit.py` (`AuditResolutionManager.resolve_task`)
- Trap: AUDタスク解決時に、Finding のステータスが `VERIFIED_RED` であるか、および実際の Red 検証証跡（コミットSHA、再現テストハッシュ、アサーション失敗出力）が存在するかを検証せず、固定値 `"before_status": "VERIFIED_RED"` を出力していたため、実証されていない指摘でも解決済みと誤認させる脆弱性があった。
- Rule: Finding のステータスが Red 検証完了（`VERIFIED_RED` または `DISPATCHED`）かつ真正な Red 証跡（`GENUINE_RED`）を保持していることを必須照合し、未実証の場合は `UNVERIFIED_BEFORE_RED` で即時拒絶する。`red_green_proof` には Before Commit SHA、After Commit SHA、再現テスト SHA-256、Red検証出力などの暗号学的証跡を完全に拘束する。
- Rung: 作り（暗号学的Proof拘束） & 検査
- Immunity Test: `tests/test_audit_resolution.py`
- Proof: `status="REPORTED"` や Red 証跡を欠く Finding に対して解決を試みた際、`UNVERIFIED_BEFORE_RED` で確実に拒絶されることを実証。

### [2026-11-09] LESSONS.md 記録失敗時のサイレント成功とフェイルオープン (Fail-Closed Lesson Recording)
- Context: `src/auto_developer/audit.py` (`AuditResolutionManager.resolve_task`)
- Trap: `record_lesson()` が `False` を返した（`LESSONS.md` 不在や書き込み失敗）にもかかわらず、呼び出し元が戻り値を無視して `success: True` と `status = "RESOLVED"` を返していたため、学習に失敗したのに学習完了と誤認されるフェイルオープンが存在した。
- Rule: 学習の記録（`record_lesson()`）の成功を解決完了の必須条件とし、失敗時は Finding を `RESOLVED` にせず `LEARNING_RECORD_FAILED` で処理を停止する（Fail-Closed）。中間状態からの再実行耐性（冪等性）を確保する。
- Rung: 作り（Fail-Closed遷移） & 検査
- Immunity Test: `tests/test_audit_resolution.py`
- Proof: `LESSONS.md` が存在しない環境で解決を試みた際、`LEARNING_RECORD_FAILED` で安全に停止することを実証。

### [2026-11-08] 異系統LLMによる独立監査と確証バイアス・自己肯定の排除 (Cross-Model Independent Audit)
- Context: `src/auto_developer/audit.py`, `src/auto_developer/orchestrator.py`, `AGENTS.md`
- Trap: 同一のモデル系統（プロンプトや思考バイアスを共有するモデル）でMakerとChecker/監査役の双方を担わせると、Makerが犯した暗黙の前提や見落とし（盲点）をCheckerも同様に肯定・見逃してしまい、潜在的バグや仕様逸脱がすり抜ける。
- Rule: 自律パイプラインにおける監査役・Checkerには、Makerと独立した異系統のフロンティアモデル（例: Maker=Gemini 3.8 Flash, Reviewer/Auditor=Claude Opus 5.5）を割り当て、SPEC.md と git diff のみを厳格に渡して批判的検証を行う。同一モデルによる自己承認は機械的に拒絶する。
- Rung: AGENTS.md & 作り（異系統モデル検証・強制）
- Immunity Test: `tests/test_audit_dispatch.py`, `tests/test_audit_model_policy.py`
- Proof: 同一モデル指定時にパイプラインが拒絶し、異系統モデル割り当て時にのみ独立監査が成立することを実証。

### [2026-11-08] Linux CI環境における chflags 不在と PEP 578 Runtime Audit Hook による物理防御 (Runtime Audit Hook Sandbox)
- Context: `src/auto_developer/verifier.py` (`RedTestVerifier.RUNNER_CODE`)
- Trap: macOS では `chflags uchg` により同一UIDでもファイル変更をOSカーネルレベルで遮断できたが、Ubuntu (Linux CI) では `chflags` コマンドが存在せず、同一UIDの別プロセスから `os.chmod()` を呼ばれると読み取り専用パーミッションが復元されて元リポジトリが書き換え可能になってしまう差異があった。
- Rule: OS固有のコマンド（`chflags`）に依存せず、Python 3.8+ 標準の PEP 578 `sys.addaudithook` をテスト実行プロセス初期化時に導入する。保護対象ルートパスに対する `open`（書き込みモード）、`os.chmod`、`os.remove`、`os.unlink`、`os.rename`、`os.mkdir`、`os.rmdir` イベントを検知した瞬間に `RuntimeError` を送出してインターセプトし、全OS共通で元リポジトリの改変を確実に遮断する。
- Rung: 作り（PEP 578 Runtime Audit Hook） & 検査
- Immunity Test: `tests/test_red_verifier.py`, `tests/test_immunity_and_red_acceptance.py`
- Proof: Linux/macOS双方で `os.chmod` による元リポジトリ改変試行が `RuntimeError` で即時遮断されることを実証。

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

### [2026-11-10] 独立監査ランナーにおける設定モデル名と実起動モデル名の不一致偽装 (Auditor Model Discrepancy)
- Context: `src/auto_developer/audit.py` (`IndependentAuditRunner.audit`)
- Trap: `AuditConfig.validate()` で設定値（`maker_model` vs `auditor_model`）のファミリー検証を行っても、Runner が実際に起動するモデル名（`self.model_name`）との一致を確認しなかったため、設定上「Claude」と宣言して検証を通過させつつ実際には Maker と同系統の「Gemini」を実行して自己承認させる偽装バイパスが生じた。
- Rule: 独立監査の実行時には、設定値の独立性だけでなく、実際に起動されるモデル（`self.model_name`）が `config.auditor_model` と完全一致することを機械的に検証し、不一致時は `INDEPENDENT_AUDIT_VIOLATION` で即時起動拒絶する。
- Rung: 作り & 検査
- Immunity Test: `tests/test_audit_model_policy.py` (`test_audit_runner_rejects_model_name_mismatch_with_config`)
- Proof: `config.auditor_model="claude-opus-5.5"` に対し `model_name="gemini-3.8-flash"` を渡した際に `audit()` が Fail-Closed で例外停止することを実証。

### [2026-11-10] AuditStore における再現テストハッシュの再計算・上書きによる改変検知無効化 (Immutable Finding Proofs)
- Context: `src/auto_developer/audit_store.py` (`AuditStore.save_finding`)
- Trap: Finding の状態更新のたびに再現コードをファイルへ書き戻し、そのコードの SHA-256 を新しく再計算して JSON に保存したため、外部改変された再現テストを読み込んで `save_finding()` を呼ぶと改変後のハッシュが正規ハッシュとして再登録されてしまい、改ざん検知が無力化される脆弱性があった。
- Rule: 監査の初回登録（テストコード確定）と状態更新を分離する。初回登録時に再現コードとその SHA-256 を不変（イミュータブル）に確定し、以降の状態更新処理ではコードハッシュの変更を禁止する。既存コードと異なる内容での再保存は衝突・改変（`CODE_HASH_TAMPERED`）として即時拒絶する。
- Rung: 作り（不変性ガード） & 検査
- Immunity Test: `tests/test_audit_store_immutability.py` (`test_save_finding_rejects_tampered_repro_code`)
- Proof: 既存の再現コードを改変した状態で再保存を試行した際に、ハッシュが更新されず安全に例外拒絶されることを実証。

### [2026-11-10] CommandRunnerAdapter におけるコンテキスト・教訓注入の脱落 (Maker Context Delivery)
- Context: `src/auto_developer/runner.py` (`CommandRunnerAdapter.run_maker`)
- Trap: `NightlyOrchestrator` が `context={"lessons": ...}` を渡しても、`CommandRunnerAdapter.run_maker()` がコマンド文字列置換時に `task_id, task_title, attempt, prev_error` のみを参照し、`lessons` を Maker のプロセスに一切渡していなかった。モックテストでは `context` 辞書を直接受け取っていたため見落とされた。また、教訓テキストをシェルコマンド文字列（`{lessons}`）に直接埋め込むと改行や引用符で展開エラーを起こす。
- Rule: 教訓やコンテキスト情報はシェル文字列への直接展開を避け、標準入力（stdin）や専用のコンテキストファイル（`.dev/context_task_<id>.json`）を通じて安全に Maker プロセスへ渡す。受入テストには Mock ではなく実際の `CommandRunnerAdapter` を用い、Maker が教訓を受信したことを実プロセスレベルで検証する。
- Rung: 作り & 検査（受入テスト）
- Immunity Test: `tests/test_runner_lesson_wiring.py` (`test_command_runner_delivers_lessons_to_maker_process`)
- Proof: 実サブプロセスで起動された Maker が標準入力またはコンテキストファイル経由で教訓文字列を完全に取得できることを実証。

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
