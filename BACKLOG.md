# BACKLOG.md

<!--
リリースを止めない残件（主に監査の P2）の置き場。PLAN.md の実行タスクではない（チェックボックスを付けない）。
機能WWW 実装後は AuditDispatcher が P2 をここへ自動追記する。
着手するときは人間が選んで PLAN.md のステップに移す。
-->

- [AUD-1001] This is a regression introduced by the new origin-based product-call classification: genuine Red tests using a plain dotted import of the product are rejected as TRIVIAL_RED. For `import auto_developer.audit`, check_trivial_red_ast() records the import under the full dotted key `imported_modules["auto_developer.audit"]`. When _is_product_or_causal_call() later resolves the call `auto_developer.audit.IndependentAuditRunner(...)`, it looks up only the root Name `auto_developer`. That lookup misses, and so do the var_origins, stdlib and builtin lookups. root_name, orig_mod, from_mod, root_origin and val_origin are therefore all None, and is_product is False. The product call inside the try body is not recognised. _is_preceded_by_try_with_call() returns False, and the following `self.fail()` is reported as an 'Unconditional self.fail() call without exception guard'. Before this diff, any non-assert call in the try body counted, so the test was accepted. The analyser now confidently marks an unresolved product call as non-product instead of falling to the safe side, which PPP.2 requires. Genuine auditor findings written with `import pkg.module` style imports are discarded and never reach PLAN.md. (Severity: P2, Loc: src/auto_developer/verifier.py:check_trivial_red_ast (import collection: alias_name = alias.asname or alias.name) and _is_product_or_causal_call (Attribute branch)) (Severity: P2, Loc: src/auto_developer/verifier.py:check_trivial_red_ast, Cycle: f3bd8a7b8a14384939bced5915d89910ecabf7d9)
- [IDEA-001] AuditDispatcher.dispatch_finding() を P0/P1 で直接呼ぶと修正予算（WWW.2）を経由せずに PLAN に登録される。現状の呼び出し元は process_findings のみで実害なし。公開経路を process_findings に一本化する (Severity: P2, Loc: src/auto_developer/dispatcher.py:dispatch_finding, Cycle: manual-review-2026-11-18)
- [IDEA-002] Checker と Maker のモデル系統を commit-step で照合し、同系統なら拒否する（LESSONS 2026-11-12 の「将来は commit-step で照合」を作りに置く。Phase 63 ステップ1で再発） (Severity: P2, Loc: src/auto_developer/cli.py:commit-step, Cycle: manual-review-2026-11-18)
