from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

VALID_SEVERITIES: frozenset[str] = frozenset({"P0", "P1", "P2", "INFO"})
VALID_STATUSES: frozenset[str] = frozenset(
    {
        "REPORTED",
        "VERIFIED_RED",
        "DISPATCHED",
        "RESOLVED",
        "REJECTED",
        "BACKLOGGED",
        "NOTED",
        "ESCALATED",
    }
)


def detect_model_family(model_name: str) -> str:
    """モデル識別子から大分類ファミリ（gemini, claude, openai, mock等）を推定する。"""
    if not model_name:
        return "unknown"
    name = model_name.strip().lower()
    if "mock" in name:
        return "mock"
    if "gemini" in name:
        return "gemini"
    if any(k in name for k in ("claude", "opus", "sonnet", "haiku", "anthropic")):
        return "claude"
    if any(k in name for k in ("gpt", "o1", "o3", "o4", "openai")):
        return "openai"
    return "unknown"


def verify_model_independence(
    maker_model: str,
    auditor_model: str,
    allow_same_model_override: bool = False,
    strict: bool = True,
) -> tuple[bool, str]:
    """MakerモデルとAuditor/Checkerモデルの独立性を検証する (AGENTS.md Rule 6 / EEE.1 / FFF.3)。"""
    if not maker_model or not maker_model.strip():
        return False, "INDEPENDENT_AUDIT_VIOLATION: maker_model cannot be empty or whitespace"
    if not auditor_model or not auditor_model.strip():
        return False, "INDEPENDENT_AUDIT_VIOLATION: auditor_model cannot be empty or whitespace"

    m_norm = maker_model.strip()
    a_norm = auditor_model.strip()

    if allow_same_model_override:
        return True, f"Override enabled: Maker='{m_norm}', Auditor='{a_norm}'"

    if m_norm.lower() == a_norm.lower():
        msg = (
            f"INDEPENDENT_AUDIT_VIOLATION: maker_model '{m_norm}' and auditor_model '{a_norm}' are identical. "
            "AGENTS.md Rule 6 forbids self-audit by the identical model."
        )
        return False, msg

    m_fam = detect_model_family(m_norm)
    a_fam = detect_model_family(a_norm)

    if strict and (m_fam == "unknown" or a_fam == "unknown"):
        msg = (
            f"INDEPENDENT_AUDIT_VIOLATION: Unrecognized model family (Maker='{m_norm}' -> '{m_fam}', "
            f"Auditor='{a_norm}' -> '{a_fam}'). Cannot guarantee cross-model independence."
        )
        return False, msg

    if m_fam != "mock" and m_fam != "unknown" and m_fam == a_fam:
        msg = (
            f"INDEPENDENT_AUDIT_VIOLATION: maker_model '{m_norm}' and auditor_model '{a_norm}' "
            f"belong to the same family '{m_fam}'. AGENTS.md Rule 6 requires cross-model audit "
            "from distinct families (e.g., Gemini Maker vs Claude Opus Auditor)."
        )
        return False, msg

    return (
        True,
        f"Independent audit policy satisfied: Maker family '{m_fam}' ({m_norm}) vs Auditor family '{a_fam}' ({a_norm}).",
    )


@dataclass
class AuditConfig:
    """独立監査の設定とモデル独立性検証を保持する設定モデル。"""

    maker_model: str
    auditor_model: str
    allow_same_model_override: bool = False
    baseline_commit: str | None = None

    def validate(self) -> bool:
        ok, reason = verify_model_independence(
            self.maker_model,
            self.auditor_model,
            self.allow_same_model_override,
        )
        if not ok:
            raise ValueError(reason)
        return True


@dataclass
class AuditFinding:
    """独立監査役（外部または自動）によって報告された指摘モデル。"""

    finding_id: str
    severity: str
    spec_reference: str
    location: str
    description: str
    reproduction_test_code: str
    status: str = "REPORTED"
    red_verification_reason: str | None = None
    red_verification_output: str | None = None
    before_commit_sha: str | None = None
    code_hash: str | None = None
    auditor_model: str | None = None
    baseline_commit: str | None = None
    target_commit: str | None = None
    target_tree_sha: str | None = None
    spec_sha256: str | None = None
    diff_sha256: str | None = None
    raw_prompt_hash: str | None = None
    raw_response_hash: str | None = None
    fallback_occurred: bool = False
    fallback_from: str | None = None
    # NOTE: auditor_model は監査ランナー設定で渡されたモデル識別子の記録値であり、
    # 外部プロバイダーの実行時同一性を暗号学的に証明するものではない (HHH.2)。

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditFinding:
        return cls(
            finding_id=data["finding_id"],
            severity=data["severity"],
            spec_reference=data["spec_reference"],
            location=data["location"],
            description=data["description"],
            reproduction_test_code=data["reproduction_test_code"],
            status=data.get("status", "REPORTED"),
            red_verification_reason=data.get("red_verification_reason"),
            red_verification_output=data.get("red_verification_output"),
            before_commit_sha=data.get("before_commit_sha"),
            code_hash=data.get("code_hash"),
            auditor_model=data.get("auditor_model"),
            baseline_commit=data.get("baseline_commit"),
            target_commit=data.get("target_commit"),
            target_tree_sha=data.get("target_tree_sha"),
            spec_sha256=data.get("spec_sha256"),
            diff_sha256=data.get("diff_sha256"),
            raw_prompt_hash=data.get("raw_prompt_hash"),
            raw_response_hash=data.get("raw_response_hash"),
            fallback_occurred=data.get("fallback_occurred", False),
            fallback_from=data.get("fallback_from"),
        )


class AuditRunner:
    """外部監査結果または静的・動的解析指摘を受け取り、構造化された AuditFinding を生成・検証する。"""

    def parse_findings(self, records: list[dict[str, Any]]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        required_fields = {
            "finding_id",
            "severity",
            "spec_reference",
            "location",
            "description",
            "reproduction_test_code",
        }

        for record in records:
            missing = required_fields - record.keys()
            if missing:
                raise ValueError(f"Record is missing required fields: {sorted(missing)}")

            severity = record["severity"]
            if severity not in VALID_SEVERITIES:
                raise ValueError(
                    f"Invalid severity '{severity}'. Must be one of {sorted(VALID_SEVERITIES)}"
                )

            repro_code = record["reproduction_test_code"]
            if not repro_code or not repro_code.strip():
                raise ValueError("reproduction_test_code must not be empty or whitespace")

            status = record.get("status", "REPORTED")
            if status not in VALID_STATUSES:
                raise ValueError(
                    f"Invalid status '{status}'. Must be one of {sorted(VALID_STATUSES)}"
                )

            findings.append(
                AuditFinding(
                    finding_id=record["finding_id"],
                    severity=severity,
                    spec_reference=record["spec_reference"],
                    location=record["location"],
                    description=record["description"],
                    reproduction_test_code=repro_code,
                    status=status,
                )
            )

        return findings

    def run_audit(self, audit_input: Any) -> list[AuditFinding]:
        """監査入力を受け取り、構造化された指摘リストを返す。"""
        if isinstance(audit_input, list):
            return self.parse_findings(audit_input)
        raise TypeError(f"Unsupported audit input type: {type(audit_input).__name__}")


class IndependentAuditRunner:
    """SPEC.md と git diff のみを厳格に入力とし、独立したLLMセッションから構造化 AuditFinding を抽出するランナー (EEE.2)。"""

    DEFAULT_PROMPT_TEMPLATE = (
        "You are an Independent Code Auditor (Checker/Auditor).\n"
        "Review the following SPEC.md and git diff according to AGENTS.md rules.\n"
        "Your role is critical audit only: identify any bugs, specification deviations, or unmitigated risks.\n"
        "For each issue found, provide a concrete Python unittest reproduction test that genuinely reproduces the bug.\n\n"
        "--- SEVERITY DEFINITIONS ---\n"
        "- P0: データ消失、機密の漏洩、証跡（Evidence / Bundle）の偽装・改ざんの見逃し、保護ファイル防壁のバイパス。\n"
        "- P1: 安全ゲート（検証・監査・Red 判定・Checker 判定）が Fail-Open になる経路、通常利用の主経路での仕様違反・誤った結果。\n"
        "- P2: 限定的な条件・構文上のエッジケースでのみ起きる誤判定（偽陽性・偽陰性）、ヒューリスティクスの精度不足。\n"
        "- INFO: 改善提案・理論的懸念・可読性。\n"
        "判定に迷う場合は低い方を付ける（P1 か P2 か迷うなら P2）。\n\n"
        "--- SPEC.md ---\n{spec_content}\n\n"
        "--- GIT DIFF ---\n{git_diff}\n\n"
        "--- OUTPUT FORMAT ---\n"
        "Return ONLY a JSON array of findings (or markdown codeblock ```json ... ```):\n"
        "[\n"
        "  {\n"
        '    "finding_id": "<ID>",\n'
        '    "severity": "P0" | "P1" | "P2" | "INFO",\n'
        '    "spec_reference": "<SPEC ref>",\n'
        '    "location": "<path:line>",\n'
        '    "description": "<summary>",\n'
        '    "reproduction_test_code": "<executable Python unittest code>",\n'
        '    "status": "REPORTED"\n'
        "  }\n"
        "]\n"
        'If no issues are found, return an empty array `[]` or `{"findings": []}`.\n'
        "CRITICAL: Do NOT output conversational prose, explanations, markdown headings, or commentary outside the JSON array.\n"
        "Your output must be strictly the JSON array (or ```json [...] ```) so token budget is not wasted and max_tokens is never exceeded."
    )

    def __init__(
        self,
        auditor_cmd_template: str = "",
        model_name: str = "claude-opus-5.5",
        timeout_sec: int = 600,
        config: AuditConfig | None = None,
        baseline_commit: str | None = None,
        fallback_models: list[str] | None = None,
        allow_emergency_fallback: bool = False,
        maker_model: str | None = None,
    ) -> None:
        self.auditor_cmd_template = auditor_cmd_template
        self.model_name = model_name
        self.timeout_sec = timeout_sec
        self.config = config
        self.baseline_commit = baseline_commit or (config.baseline_commit if config else None)
        self.fallback_models = list(fallback_models or [])
        self.allow_emergency_fallback = allow_emergency_fallback
        self.maker_model = maker_model or (config.maker_model if config else "gemini-3.8-flash")
        self.audit_runner = AuditRunner()
        self.last_run_metadata: dict[str, Any] | None = None
        self._last_envelope_metadata: dict[str, Any] | None = None

    def build_audit_prompt(self, spec_content: str, git_diff: str) -> str:
        return (
            self.DEFAULT_PROMPT_TEMPLATE
            .replace("{spec_content}", spec_content)
            .replace("{git_diff}", git_diff)
        )

    def parse_response(self, output: str) -> list[AuditFinding]:
        import json
        import re

        clean_output = output.strip()
        found_envelope: dict[str, Any] | None = None
        collected_findings: list[dict[str, Any]] = []
        empty_block_found = False
        has_decode_error = False
        empty_envelope: dict[str, Any] | None = None

        # 1. まず output 全体が有効な JSON（Envelope や JSON 配列）であるかを直接試行
        try:
            parsed = json.loads(clean_output)
            if isinstance(parsed, list):
                findings = [item for item in parsed if isinstance(item, dict) and "finding_id" in item]
                if len(parsed) == 0:
                    empty_block_found = True
                elif len(findings) == len(parsed):
                    collected_findings.extend(findings)
                else:
                    raise ValueError(f"AUDIT_PARSE_ERROR: JSON array contains non-finding items: {parsed}")
            elif isinstance(parsed, dict):
                if parsed.get("error"):
                    raise ValueError(f"AUDIT_FAILED: Auditor response contains error: {parsed.get('error')}")
                is_prov = bool(
                    parsed.get("mode")
                    or parsed.get("execution_id")
                    or parsed.get("provider_request_id")
                    or parsed.get("provider")
                    or ("status" in parsed and "finding_id" not in parsed and "severity" not in parsed)
                    or parsed.get("stop_reason")
                )
                if is_prov:
                    status = parsed.get("status")
                    if status and status != "COMPLETED":
                        raise ValueError(f"AUDIT_{status}: Live audit envelope returned non-completed status '{status}': {parsed.get('error') or ''}")
                    found_envelope = parsed
                    if "findings" in parsed and isinstance(parsed["findings"], list):
                        findings = [item for item in parsed["findings"] if isinstance(item, dict) and "finding_id" in item]
                        if len(parsed["findings"]) == 0:
                            empty_block_found = True
                        elif len(findings) == len(parsed["findings"]):
                            collected_findings.extend(findings)
                        else:
                            raise ValueError(f"AUDIT_PARSE_ERROR: JSON findings array contains non-finding items: {parsed['findings']}")
                elif "finding_id" in parsed and "findings" not in parsed:
                    collected_findings.append(parsed)
                elif "findings" in parsed and isinstance(parsed["findings"], list):
                    status = parsed.get("status")
                    if status and status != "COMPLETED":
                        raise ValueError(f"AUDIT_{status}: Live audit envelope returned non-completed status '{status}': {parsed.get('error') or ''}")
                    found_envelope = parsed
                    findings = [item for item in parsed["findings"] if isinstance(item, dict) and "finding_id" in item]
                    if len(parsed["findings"]) == 0:
                        empty_block_found = True
                    elif len(findings) == len(parsed["findings"]):
                        collected_findings.extend(findings)
                    else:
                        raise ValueError(f"AUDIT_PARSE_ERROR: JSON findings array contains non-finding items: {parsed['findings']}")
        except json.JSONDecodeError:
            # 2. Markdown コードブロックの探索（複数ブロック走査＆全件集約）
            all_code_blocks = list(re.finditer(r"```[ \t]*(?:[a-zA-Z0-9_-]+)?[ \t]*\n?(.*?)\n?```", clean_output, re.DOTALL))
            if all_code_blocks:
                for m in all_code_blocks:
                    content = m.group(1).strip()
                    if not content.startswith(("[", "{")):
                        stripped = re.sub(r"^[a-zA-Z0-9_-]+[ \t]*\n?", "", content).strip()
                        if stripped.startswith(("[", "{")):
                            content = stripped
                    if not content.startswith(("[", "{")):
                        continue
                    try:
                        parsed = json.loads(content)
                        if isinstance(parsed, list):
                            findings = [item for item in parsed if isinstance(item, dict) and "finding_id" in item]
                            if len(parsed) == 0:
                                empty_block_found = True
                            elif len(findings) == len(parsed):
                                collected_findings.extend(findings)
                            else:
                                has_decode_error = True
                        elif isinstance(parsed, dict):
                            if parsed.get("error"):
                                raise ValueError(f"AUDIT_FAILED: Auditor response contains error: {parsed.get('error')}")
                            is_prov = bool(
                                parsed.get("mode")
                                or parsed.get("execution_id")
                                or parsed.get("provider_request_id")
                                or parsed.get("provider")
                                or ("status" in parsed and "finding_id" not in parsed and "severity" not in parsed)
                                or parsed.get("stop_reason")
                            )
                            if is_prov:
                                env_status = parsed.get("status")
                                if env_status and env_status != "COMPLETED":
                                    raise ValueError(
                                        f"AUDIT_{env_status}: Live audit envelope returned non-completed status '{env_status}': {parsed.get('error') or ''}"
                                    )
                                env_mode = parsed.get("mode")
                                env_stop = parsed.get("stop_reason")
                                if (env_mode == "LIVE" or env_stop is not None) and (not env_stop or env_stop != "end_turn"):
                                    raise ValueError(
                                        f"AUDIT_INTERRUPTED: Live audit envelope has invalid stop_reason '{env_stop}' (expected 'end_turn')"
                                    )
                                if found_envelope is None:
                                    found_envelope = parsed
                                if "findings" in parsed and isinstance(parsed["findings"], list):
                                    findings = [item for item in parsed["findings"] if isinstance(item, dict) and "finding_id" in item]
                                    if len(parsed["findings"]) == 0:
                                        empty_block_found = True
                                    elif len(findings) == len(parsed["findings"]):
                                        collected_findings.extend(findings)
                                    else:
                                        has_decode_error = True
                            elif "finding_id" in parsed and "findings" not in parsed:
                                collected_findings.append(parsed)
                            elif "findings" in parsed and isinstance(parsed["findings"], list):
                                env_status = parsed.get("status")
                                if env_status and env_status != "COMPLETED":
                                    raise ValueError(
                                        f"AUDIT_{env_status}: Live audit envelope returned non-completed status '{env_status}': {parsed.get('error') or ''}"
                                    )
                                if found_envelope is None:
                                    found_envelope = parsed
                                findings = [item for item in parsed["findings"] if isinstance(item, dict) and "finding_id" in item]
                                if len(parsed["findings"]) == 0:
                                    empty_block_found = True
                                elif len(findings) == len(parsed["findings"]):
                                    collected_findings.extend(findings)
                                else:
                                    has_decode_error = True
                            else:
                                has_decode_error = True
                    except json.JSONDecodeError:
                        has_decode_error = True

                if has_decode_error:
                    raise ValueError("Malformed JSON in audit response code block")

            # 3. コードブロック外またはコードブロックが存在しない場合の生構造探索
            text_outside = re.sub(r"```[ \t]*(?:[a-zA-Z0-9_-]+)?[ \t]*\n?(.*?)\n?```", "", clean_output, flags=re.DOTALL)
            decoder = json.JSONDecoder()

            # プロバイダエンベロープ辞書およびエラー辞書を最優先で探索（ログ行混入時でも外側封筒を厳格評価）
            for m_brace in re.finditer(r"\{", text_outside):
                s_idx = m_brace.start()
                try:
                    parsed_out, _ = decoder.raw_decode(text_outside, s_idx)
                    if isinstance(parsed_out, dict):
                        if parsed_out.get("error"):
                            raise ValueError(f"AUDIT_FAILED: Auditor response contains error: {parsed_out.get('error')}")
                        is_prov = bool(
                            parsed_out.get("mode")
                            or parsed_out.get("execution_id")
                            or parsed_out.get("provider_request_id")
                            or parsed_out.get("provider")
                            or ("status" in parsed_out and "finding_id" not in parsed_out and "severity" not in parsed_out)
                            or parsed_out.get("stop_reason")
                        )
                        if is_prov:
                            env_status = parsed_out.get("status")
                            if env_status and env_status != "COMPLETED":
                                raise ValueError(
                                    f"AUDIT_{env_status}: Live audit envelope returned non-completed status '{env_status}': {parsed_out.get('error') or ''}"
                                )
                            env_mode = parsed_out.get("mode")
                            env_stop = parsed_out.get("stop_reason")
                            if (env_mode == "LIVE" or env_stop is not None) and (not env_stop or env_stop != "end_turn"):
                                raise ValueError(
                                    f"AUDIT_INTERRUPTED: Live audit envelope has invalid stop_reason '{env_stop}' (expected 'end_turn')"
                                )
                            if found_envelope is None:
                                found_envelope = parsed_out
                            if "findings" in parsed_out and isinstance(parsed_out["findings"], list):
                                findings = [item for item in parsed_out["findings"] if isinstance(item, dict) and "finding_id" in item]
                                if len(parsed_out["findings"]) == 0:
                                    empty_block_found = True
                                elif len(findings) == len(parsed_out["findings"]):
                                    if not collected_findings:
                                        collected_findings.extend(findings)
                                else:
                                    has_decode_error = True
                except json.JSONDecodeError:
                    pass

            # 配列構造の探索
            parsed_raw_bracket = False
            for m_bracket in re.finditer(r"\[", text_outside):
                s_idx = m_bracket.start()
                try:
                    parsed_out, _ = decoder.raw_decode(text_outside, s_idx)
                    if isinstance(parsed_out, list):
                        findings = [item for item in parsed_out if isinstance(item, dict) and "finding_id" in item]
                        if len(parsed_out) == 0:
                            pass
                        elif len(findings) == len(parsed_out):
                            parsed_raw_bracket = True
                            if not collected_findings:
                                collected_findings.extend(findings)
                            break
                        else:
                            has_decode_error = True
                except json.JSONDecodeError:
                    pass

            # 非プロバイダ辞書（{"findings": [...]} または単一指摘）の探索
            for m_brace in re.finditer(r"\{", text_outside):
                s_idx = m_brace.start()
                try:
                    parsed_out, _ = decoder.raw_decode(text_outside, s_idx)
                    if isinstance(parsed_out, dict):
                        if parsed_out.get("error"):
                            raise ValueError(f"AUDIT_FAILED: Auditor response contains error: {parsed_out.get('error')}")
                        findings_fld = parsed_out.get("findings")
                        if isinstance(findings_fld, list):
                            findings = [item for item in findings_fld if isinstance(item, dict) and "finding_id" in item]
                            if len(findings_fld) == 0:
                                if found_envelope is None:
                                    found_envelope = parsed_out
                                if empty_envelope is None:
                                    empty_envelope = parsed_out
                            elif len(findings) == len(findings_fld):
                                if found_envelope is None:
                                    found_envelope = parsed_out
                                if not collected_findings:
                                    collected_findings.extend(findings)
                                break
                            else:
                                has_decode_error = True
                        elif "finding_id" in parsed_out:
                            if not collected_findings:
                                collected_findings.append(parsed_out)
                            break
                except json.JSONDecodeError:
                    pass

            for m in re.finditer(r"\[\s*\{", text_outside):
                try:
                    p_candidate, _ = decoder.raw_decode(text_outside, m.start())
                    if not isinstance(p_candidate, list):
                        has_decode_error = True
                except json.JSONDecodeError:
                    has_decode_error = True

            for m in re.finditer(r"\{\s*[\"'](?:finding_id|findings)[\"']", text_outside):
                try:
                    p_candidate, _ = decoder.raw_decode(text_outside, m.start())
                    if not isinstance(p_candidate, dict):
                        has_decode_error = True
                except json.JSONDecodeError:
                    has_decode_error = True

            if not parsed_raw_bracket and re.search(
                r"(?:\[\s*\{|\{\s*[\"'](?:finding_id|findings)[\"'])", text_outside
            ):
                has_decode_error = True

            if has_decode_error:
                raise ValueError("Malformed JSON in audit response")

            if not collected_findings and not empty_block_found and found_envelope is None and empty_envelope is not None:
                found_envelope = empty_envelope

            if not collected_findings and not empty_block_found and found_envelope is None and re.fullmatch(r"(?:```(?:json)?\s*)?\[\s*\](?:\s*```)?", clean_output):
                empty_block_found = True

            if not collected_findings and not empty_block_found and found_envelope is None:
                raise ValueError(f"Could not parse valid JSON from audit response: {output[:200]}")

        # Envelope メタデータ検証と保持（指摘配列とは独立して常に検証）
        if found_envelope is not None:
            exec_id = found_envelope.get("execution_id") or found_envelope.get("id")
            prov_req_id = found_envelope.get("provider_request_id") or found_envelope.get("id")
            raw_mode = found_envelope.get("mode")
            status = found_envelope.get("status")
            stop_reason = found_envelope.get("stop_reason")
            has_prov_metadata = bool(raw_mode or exec_id or prov_req_id or found_envelope.get("provider") or status or stop_reason)

            # AUD-002 (Sol Phase 62 / Cycle 11 & 12): Envelope status validation (Fail-Closed: reject non-COMPLETED status)
            if status and status != "COMPLETED":
                raise ValueError(
                    f"AUDIT_{status}: Live audit envelope returned non-completed status '{status}': {found_envelope.get('error') or ''}"
                )

            if has_prov_metadata:
                mode = raw_mode or ("MOCK" if (exec_id and str(exec_id).startswith("mock_")) else "LIVE")

                # AUD-001 / AUD-002 / VVV.1 / Sol Phase 61 P1: mode: LIVE 時の stop_reason 厳格検証（Fail-Closed allow-list）
                if (mode == "LIVE" or stop_reason is not None) and (not stop_reason or stop_reason != "end_turn"):
                    raise ValueError(
                        f"AUDIT_INTERRUPTED: Live audit envelope has invalid stop_reason '{stop_reason}' (expected 'end_turn')"
                    )

                self._last_envelope_metadata = {
                    "mode": mode,
                    "provider": found_envelope.get("provider"),
                    "execution_id": exec_id,
                    "provider_request_id": prov_req_id,
                    "model": found_envelope.get("model"),
                    "resolved_model": found_envelope.get("resolved_model") or found_envelope.get("model"),
                    "status": status,
                    "stop_reason": stop_reason,
                    "usage": found_envelope.get("usage"),
                }
            else:
                self._last_envelope_metadata = None
        else:
            self._last_envelope_metadata = None

        if collected_findings:
            data = collected_findings
        elif found_envelope is not None and "findings" in found_envelope and isinstance(found_envelope["findings"], list):
            data = found_envelope["findings"]
        elif empty_block_found:
            data = []
        else:
            raise ValueError(f"Could not parse valid JSON findings from audit response: {output[:200]}")

        if not data:
            return []

        return self.audit_runner.parse_findings(data)

    def audit(self, spec_content: str, git_diff: str) -> list[AuditFinding]:
        import hashlib
        import os
        import subprocess
        import tempfile

        prompt = self.build_audit_prompt(spec_content, git_diff)
        raw_prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        if not self.auditor_cmd_template:
            raise RuntimeError(
                "No auditor_cmd_template configured for IndependentAuditRunner (Fail-Closed)"
            )

        if self.config:
            self.config.validate()
            if self.config.auditor_model != self.model_name:
                raise ValueError(
                    f"INDEPENDENT_AUDIT_VIOLATION: Runner model '{self.model_name}' "
                    f"does not match config.auditor_model '{self.config.auditor_model}'"
                )

        maker_m = self.config.maker_model if self.config else self.maker_model
        candidate_models = [self.model_name] + [m for m in self.fallback_models if m != self.model_name]

        # SSS.3: モデル独立性を満たす候補のみを選別（同系統モデルの混入・バイパスを排除）
        independent_candidates: list[str] = []
        for cand in candidate_models:
            ok, _ = verify_model_independence(
                maker_model=maker_m,
                auditor_model=cand,
                strict=True,
            )
            if ok:
                independent_candidates.append(cand)

        if not independent_candidates:
            raise RuntimeError(
                f"NO_INDEPENDENT_AUDITOR_AVAILABLE: None of candidate models {candidate_models} "
                f"satisfy model independence from maker_model '{maker_m}'"
            )

        last_error: Exception | None = None

        for candidate in independent_candidates:
            is_fallback = (candidate != self.model_name)

            temp_prompt_file: str | None = None
            try:
                cmd_str = self.auditor_cmd_template.replace("{model}", candidate)
                if "{prompt_file}" in cmd_str:
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as tfile:
                        tfile.write(prompt)
                        temp_prompt_file = tfile.name
                    cmd_str = cmd_str.replace("{prompt_file}", temp_prompt_file)

                proc = subprocess.run(
                    ["bash", "-c", cmd_str],
                    input=prompt,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self.timeout_sec,
                )
                if proc.returncode != 0:
                    err_msg = f"Auditor '{candidate}' failed with exit {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
                    last_error = RuntimeError(err_msg)
                    continue

                output = proc.stdout
                raw_response_hash = hashlib.sha256(output.encode("utf-8")).hexdigest()
                findings = self.parse_response(output)
                for finding in findings:
                    finding.auditor_model = candidate
                    if is_fallback:
                        finding.fallback_occurred = True
                        finding.fallback_from = self.model_name
                    finding.raw_prompt_hash = raw_prompt_hash
                    finding.raw_response_hash = raw_response_hash
                    if self.baseline_commit and not finding.baseline_commit:
                        finding.baseline_commit = self.baseline_commit

                # TTT.1: プロバイダ名および実行IDの導出・記録
                envelope_meta = getattr(self, "_last_envelope_metadata", None) or {}
                provider = envelope_meta.get("provider")
                if not provider:
                    cand_lower = candidate.lower()
                    if "claude" in cand_lower or "anthropic" in cand_lower:
                        provider = "anthropic"
                    elif "gpt" in cand_lower or "o1" in cand_lower or "o3" in cand_lower or "openai" in cand_lower:
                        provider = "openai"
                    elif "gemini" in cand_lower or "google" in cand_lower:
                        provider = "google"
                    else:
                        provider = "independent"

                execution_id = envelope_meta.get("execution_id") or f"exec-{raw_response_hash[:16]}"
                provider_req_id = envelope_meta.get("provider_request_id") or execution_id
                mode = envelope_meta.get("mode") or ("MOCK" if str(execution_id).startswith("mock_") else "LIVE")

                self.last_run_metadata = {
                    "mode": mode,
                    "provider": provider,
                    "provider_request_id": provider_req_id,
                    "auditor_model": candidate,
                    "resolved_model": envelope_meta.get("resolved_model") or candidate,
                    "execution_id": execution_id,
                    "status": envelope_meta.get("status") or "COMPLETED",
                    "stop_reason": envelope_meta.get("stop_reason"),
                    "fallback_occurred": is_fallback,
                    "fallback_from": self.model_name if is_fallback else None,
                    "maker_model": maker_m,
                    "independence_verified": True,
                    "raw_prompt_hash": raw_prompt_hash,
                    "raw_response_hash": raw_response_hash,
                    "findings_count": len(findings),
                    "usage": envelope_meta.get("usage"),
                }
                return findings
            except (subprocess.SubprocessError, OSError, ValueError, RuntimeError) as e:
                last_error = e
                continue
            finally:
                if temp_prompt_file and os.path.exists(temp_prompt_file):
                    try:
                        os.unlink(temp_prompt_file)
                    except OSError:
                        pass

        # すべての独立候補モデルがエラーとなった場合、NO_INDEPENDENT_AUDITOR_AVAILABLE として送出 (SSS.3)
        raise RuntimeError(
            f"NO_INDEPENDENT_AUDITOR_AVAILABLE: All candidate auditor models failed or were rejected by independence policy. Last error: {last_error}"
        )

    def audit_repository(
        self,
        repo_dir: Path,
        target_commit: str = "HEAD",
        baseline_commit: str | None = None,
        spec_path: Path | str | None = None,
        head_commit: str | None = None,
    ) -> list[AuditFinding]:
        """指定されたベースラインコミットからの diff およびコミット拘束された SPEC を抽出し、独立外部監査を実行する (RRR.1)。"""
        import hashlib
        import subprocess

        # 古い位置引数呼び出し（第2引数に spec_path が渡された場合）との後方互換対応
        if isinstance(target_commit, Path) or (isinstance(target_commit, str) and target_commit.endswith(".md")):
            spec_path = target_commit
            target_commit = head_commit or "HEAD"

        target_baseline = baseline_commit or self.baseline_commit
        if not target_baseline:
            raise ValueError("baseline_commit must be specified for repository audit")

        target = head_commit or target_commit
        # ターゲットコミットSHAおよびTree SHAの解決
        rev_res = subprocess.run(
            ["git", "rev-parse", target],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        target_sha = rev_res.stdout.strip()

        tree_res = subprocess.run(
            ["git", "rev-parse", f"{target_sha}^{{tree}}"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        target_tree_sha = tree_res.stdout.strip()

        # baseline から target までの git diff を厳格に抽出
        diff_res = subprocess.run(
            ["git", "diff", target_baseline, target_sha],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        git_diff = diff_res.stdout

        # SPEC.md の内容を target_commit から厳密に抽出 (SSS.2 / Sol氏 Phase 58 P1指摘)
        # 作業ツリーへのフォールバックを完全撤廃し、コミット内に存在しない場合は Fail-Closed で即座に拒絶
        spec_git_path = "SPEC.md"
        if spec_path is not None:
            p = Path(spec_path)
            if p.is_absolute():
                try:
                    spec_git_path = str(p.relative_to(repo_dir))
                except ValueError:
                    spec_git_path = p.name
            else:
                spec_git_path = str(p)

        show_spec = subprocess.run(
            ["git", "show", f"{target_sha}:{spec_git_path}"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if show_spec.returncode != 0 or not show_spec.stdout:
            raise FileNotFoundError(
                f"SPEC_NOT_FOUND_IN_COMMIT: {spec_git_path} not found in target commit {target_sha}"
            )
        spec_content = show_spec.stdout

        spec_sha256 = hashlib.sha256(spec_content.encode("utf-8")).hexdigest()
        diff_sha256 = hashlib.sha256(git_diff.encode("utf-8")).hexdigest()

        findings = self.audit(spec_content=spec_content, git_diff=git_diff)
        for finding in findings:
            finding.baseline_commit = target_baseline
            finding.target_commit = target_sha
            finding.target_tree_sha = target_tree_sha
            finding.spec_sha256 = spec_sha256
            finding.diff_sha256 = diff_sha256

        if self.last_run_metadata:
            self.last_run_metadata.update({
                "baseline_commit": target_baseline,
                "target_commit": target_sha,
                "target_tree_sha": target_tree_sha,
                "spec_sha256": spec_sha256,
                "diff_sha256": diff_sha256,
            })
        return findings

    def screen_and_dispatch_findings(
        self,
        findings: list[AuditFinding],
        dispatcher: Any,
        plan_path: Path | None = None,
    ) -> dict[str, Any]:
        """外部監査役から投入された指摘リストを多層防壁で選別し、真正な指摘のみを PLAN.md に登録する (QQQ.2 / WWW.2)。"""
        # SPEC.md WWW.2: cycle_id 省略時は先頭 finding の target_commit、それもなければ HEAD SHA を使う
        dispatched = dispatcher.process_findings(findings, plan_path=plan_path, cycle_id=None)

        rejected: list[tuple[AuditFinding, str]] = []
        backlogged: list[AuditFinding] = []
        escalated: list[AuditFinding] = []

        for finding in findings:
            if finding.status == "BACKLOGGED":
                backlogged.append(finding)
            elif finding.status == "ESCALATED":
                escalated.append(finding)
            elif finding.status != "DISPATCHED":
                reason = finding.red_verification_reason or finding.status or "UNKNOWN_REJECTION"
                rejected.append((finding, reason))

        return {
            "dispatched": dispatched,
            "rejected": rejected,
            "backlogged": backlogged,
            "escalated": escalated,
            "total_processed": len(findings),
            "genuine_count": len(dispatched),
            "rejected_count": len(rejected),
        }


class AuditResolutionManager:
    """AUDタスク解決時のBefore Red / After Green検証、Evidence拘束、および免疫テスト自動昇格を管理する (EEE.3)。"""

    def __init__(self, repo_dir: Path | None = None) -> None:
        from auto_developer.audit_store import AuditStore

        self.repo_dir = repo_dir or Path.cwd()
        self.audit_store = AuditStore(repo_dir=self.repo_dir)

    def _get_head_commit_sha(self) -> str:
        """Git リポジトリの最新 HEAD コミット SHA を取得する。"""
        import subprocess

        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        return "unknown"

    def extract_finding_id(self, task_id: str) -> str | None:
        if task_id.startswith("AUD-"):
            return task_id[4:]
        return None

    def resolve_task(self, task_id: str) -> dict[str, Any]:
        """AUDタスクのBefore Red / After Greenを実コミット上で検証し、免疫テストとして自動昇格配置する (HHH.1)。"""
        import hashlib
        import re
        import subprocess
        import time

        from auto_developer.verifier import RedTestVerifier

        finding_id = self.extract_finding_id(task_id)
        if not finding_id:
            return {"success": False, "reason": f"Task ID '{task_id}' is not an AUD task"}

        finding = self.audit_store.get_finding(finding_id)
        if not finding:
            return {"success": False, "reason": f"Finding '{finding_id}' not found in audit store"}

        # 0. Git リポジトリおよび作業ツリーのクリーン性検証 (HHH.1)
        is_git_repo = (self.repo_dir / ".git").is_dir()
        if not is_git_repo:
            return {
                "success": False,
                "reason": "UNCOMMITTED_AFTER_STATE: Repository is not a git repository",
                "error": "UNCOMMITTED_AFTER_STATE",
            }

        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if status_proc.returncode != 0:
            return {
                "success": False,
                "reason": "UNCOMMITTED_AFTER_STATE: Failed to inspect git status",
                "error": "UNCOMMITTED_AFTER_STATE",
            }

        uncommitted_files: list[str] = []
        for line in status_proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) >= 2:
                file_path = parts[1].strip()
                if " -> " in file_path:
                    file_path = file_path.split(" -> ")[1].strip()
                if not file_path.startswith(".dev/") and file_path != ".dev":
                    uncommitted_files.append(file_path)

        if uncommitted_files:
            return {
                "success": False,
                "reason": f"UNCOMMITTED_AFTER_STATE: Working tree contains uncommitted changes: {uncommitted_files}",
                "error": "UNCOMMITTED_AFTER_STATE",
            }

        # 1. Before Commit の実在性検証 (HHH.1)
        before_commit = (finding.before_commit_sha or "").strip()
        if not before_commit or before_commit == "unknown":
            return {
                "success": False,
                "reason": f"INVALID_BEFORE_COMMIT: finding.before_commit_sha is invalid or missing ('{before_commit}')",
                "error": "INVALID_BEFORE_COMMIT",
            }

        cat_proc = subprocess.run(
            ["git", "cat-file", "-e", f"{before_commit}^{{commit}}"],
            cwd=self.repo_dir,
            capture_output=True,
            check=False,
        )
        if cat_proc.returncode != 0:
            return {
                "success": False,
                "reason": f"INVALID_BEFORE_COMMIT: before_commit_sha '{before_commit}' is not a valid commit in git",
                "error": "INVALID_BEFORE_COMMIT",
            }

        # 2. After Commit の前進および子孫関係の検証 (HHH.1)
        after_commit = self._get_head_commit_sha()
        if not after_commit or after_commit == "unknown":
            return {
                "success": False,
                "reason": "INVALID_AFTER_COMMIT: Failed to obtain valid HEAD commit",
                "error": "INVALID_AFTER_COMMIT",
            }

        if after_commit == before_commit:
            return {
                "success": False,
                "reason": f"NO_COMMIT_ADVANCE: after_commit '{after_commit}' is identical to before_commit",
                "error": "NO_COMMIT_ADVANCE",
            }

        ancestor_proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", before_commit, after_commit],
            cwd=self.repo_dir,
            capture_output=True,
            check=False,
        )
        if ancestor_proc.returncode != 0:
            return {
                "success": False,
                "reason": f"NO_COMMIT_ADVANCE: after_commit '{after_commit}' is not a descendant of before_commit '{before_commit}'",
                "error": "NO_COMMIT_ADVANCE",
            }

        # 3. Before Red 真正性の検証 (FFF.2)
        if finding.status not in ("VERIFIED_RED", "DISPATCHED"):
            return {
                "success": False,
                "reason": (
                    f"UNVERIFIED_BEFORE_RED: Finding '{finding_id}' has status '{finding.status}', "
                    "expected 'VERIFIED_RED' or 'DISPATCHED'"
                ),
                "error": "UNVERIFIED_BEFORE_RED",
            }

        if finding.red_verification_reason != "GENUINE_RED":
            return {
                "success": False,
                "reason": (
                    f"UNVERIFIED_BEFORE_RED: Finding '{finding_id}' lacks verified genuine red proof "
                    f"(red_verification_reason='{finding.red_verification_reason}')"
                ),
                "error": "UNVERIFIED_BEFORE_RED",
            }

        repro_code = self.audit_store.get_repro_test(finding_id)
        if not repro_code:
            repro_code = finding.reproduction_test_code
        if not repro_code:
            return {
                "success": False,
                "reason": f"No reproduction test code found for finding '{finding_id}'",
            }

        # 再現テストコードの改ざん検知
        if finding.code_hash:
            actual_repro_hash = hashlib.sha256(repro_code.encode("utf-8")).hexdigest()
            if actual_repro_hash != finding.code_hash:
                return {
                    "success": False,
                    "reason": (
                        f"TAMPERED_REPRO_TEST: reproduction test code hash mismatch "
                        f"(expected={finding.code_hash[:8]}, actual={actual_repro_hash[:8]})"
                    ),
                    "error": "TAMPERED_REPRO_TEST",
                }

        # 4. 修正前コミット (Before Red) および修正後コミット (After Green) の隔離環境での再実証 (HHH.1)
        verifier = RedTestVerifier(repo_dir=self.repo_dir)

        before_verification = verifier.verify_candidate_test(repro_code, commit=before_commit)
        if not (before_verification.is_reproduced and before_verification.reason == "GENUINE_RED"):
            return {
                "success": False,
                "reason": (
                    f"BEFORE_RED_NOT_REPRODUCED: test did not genuinely reproduce red on before_commit '{before_commit[:8]}' "
                    f"(reason={before_verification.reason}, exit={before_verification.exit_code}):\n{before_verification.output}"
                ),
                "error": "BEFORE_RED_NOT_REPRODUCED",
            }

        after_verification = verifier.verify_candidate_test(repro_code, commit=after_commit)
        if not (after_verification.reason == "ALREADY_GREEN" and after_verification.exit_code == 0):
            return {
                "success": False,
                "reason": (
                    f"AFTER_GREEN_FAILED: test still failed or produced error on after_commit '{after_commit[:8]}' "
                    f"(reason={after_verification.reason}, exit={after_verification.exit_code}):\n{after_verification.output}"
                ),
                "error": "AFTER_GREEN_FAILED",
            }

        # 5. 免疫テストとしての昇格・配置 (非破壊的確定 FFF.1)
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", finding_id)
        immunity_rel_path = f"tests/immunity/test_immunity_{safe_name}.py"
        target_path = self.repo_dir / immunity_rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        is_already_promoted = False
        if target_path.exists():
            existing_content = target_path.read_text(encoding="utf-8")
            existing_hash = hashlib.sha256(existing_content.encode("utf-8")).hexdigest()
            candidate_hash = hashlib.sha256(repro_code.encode("utf-8")).hexdigest()
            if existing_hash != candidate_hash:
                return {
                    "success": False,
                    "reason": (
                        f"IMMUNITY_COLLISION: target immunity test '{immunity_rel_path}' already exists "
                        f"with different content (hash={existing_hash[:8]} vs candidate={candidate_hash[:8]}). "
                        "Refusing to overwrite existing immunity test."
                    ),
                    "error": "IMMUNITY_COLLISION",
                }
            is_already_promoted = True

        if not is_already_promoted:
            target_path.write_text(repro_code, encoding="utf-8")

        # 6. 免疫テスト安全性の機械的検証 (AST 検査 & 不干渉)
        from auto_developer.guard import GitGuard, validate_test_ast

        if not validate_test_ast(target_path):
            if not is_already_promoted:
                target_path.unlink(missing_ok=True)
            return {
                "success": False,
                "reason": "PROMOTION_FAILED: Promoted test failed AST safety validation",
            }

        guard = GitGuard(repo_dir=self.repo_dir)
        if not guard.verify_mutual_non_interference(immunity_rel_path):
            if not is_already_promoted:
                target_path.unlink(missing_ok=True)
            return {
                "success": False,
                "reason": "PROMOTION_FAILED: Promoted test interfered with existing test suite",
            }

        proof = {
            "finding_id": finding_id,
            "before_commit": before_commit,
            "after_commit": after_commit,
            "repro_test_sha256": finding.code_hash or hashlib.sha256(repro_code.encode("utf-8")).hexdigest(),
            "before_status": "VERIFIED_RED",
            "after_status": "GREEN",
            "before_result": {
                "reason": before_verification.reason,
                "exit_code": before_verification.exit_code,
                "tests_run": before_verification.tests_run,
                "failures": before_verification.failures,
                "errors": before_verification.errors,
                "output_hash": hashlib.sha256(before_verification.output.encode("utf-8")).hexdigest(),
            },
            "after_result": {
                "reason": after_verification.reason,
                "exit_code": after_verification.exit_code,
                "tests_run": after_verification.tests_run,
                "failures": after_verification.failures,
                "errors": after_verification.errors,
                "output_hash": hashlib.sha256(after_verification.output.encode("utf-8")).hexdigest(),
            },
            "before_red_reason": before_verification.reason,
            "after_green_reason": after_verification.reason,
            "immunity_test_path": immunity_rel_path,
            "resolved_at": time.time(),
        }

        # 7. LESSONS.md への自動教訓記録 (EEE.4 / FFF.3: Fail-Closed)
        lesson_recorded = self.record_lesson(finding, proof)
        if not lesson_recorded:
            finding.status = "LEARNING_RECORD_FAILED"
            self.audit_store.save_finding(finding)
            return {
                "success": False,
                "reason": "LEARNING_RECORD_FAILED: Failed to record lesson in LESSONS.md",
                "error": "LEARNING_RECORD_FAILED",
                "proof": proof,
            }

        finding.status = "RESOLVED"
        self.audit_store.save_finding(finding)

        return {
            "success": True,
            "proof": proof,
            "finding": finding.to_dict(),
            "immunity_test_path": immunity_rel_path,
        }

    def record_lesson(
        self,
        finding: AuditFinding,
        proof: dict[str, Any],
        lessons_path: Path | None = None,
    ) -> bool:
        """解決されたAUD指摘から LESSONS.md に構造化された教訓を自動追記する (EEE.4)。"""
        from datetime import datetime

        target_path = lessons_path or (self.repo_dir / "LESSONS.md")
        if not target_path.exists():
            return False

        content = target_path.read_text(encoding="utf-8")
        token = f"[{finding.finding_id}]"
        if token in content:
            return True  # 既に記録済み (二重追記防止)

        today_str = datetime.now().strftime("%Y-%m-%d")
        before_commit = proof.get("before_commit", "unknown")
        after_commit = proof.get("after_commit", "unknown")
        test_sha = proof.get("repro_test_sha256", "unknown")
        new_lesson = (
            f"### [{today_str}] [{finding.finding_id}] {finding.description}\n"
            f"- Context: {finding.location} (Severity: {finding.severity}, Spec: {finding.spec_reference})\n"
            f"- Trap: {finding.description}\n"
            f"- Rule: Ensure compliance with {finding.spec_reference} to prevent regression of {finding.finding_id}.\n"
            f"- Rung: 検査 & 作り（自動昇格免疫テスト）\n"
            f"- Immunity Test: {proof.get('immunity_test_path', 'unknown')}\n"
            f"- Proof: Verified Before Red ({proof.get('before_status', 'VERIFIED_RED')}) on commit {before_commit} "
            f"and After Green ({proof.get('after_status', 'GREEN')}) on commit {after_commit} "
            f"[test_sha256: {test_sha}]\n\n"
        )

        header = "## 恒久教訓インデックス\n"
        if header in content:
            parts = content.split(header, 1)
            updated_content = parts[0] + header + "\n" + new_lesson + parts[1].lstrip("\n")
        else:
            updated_content = content.rstrip() + "\n\n" + header + "\n" + new_lesson

        target_path.write_text(updated_content, encoding="utf-8")
        return True


@dataclass
class LessonEntry:
    title: str
    date: str
    context: str = ""
    trap: str = ""
    rule: str = ""
    rung: str = ""
    immunity_test: str = ""
    proof: str = ""
    raw_markdown: str = ""


class LessonInjector:
    """LESSONS.md から関連する教訓を検索し、Makerプロンプト/コンテキストへ注入する (FFF.4 / Level 7 Rule 10)。"""

    def __init__(
        self,
        repo_dir: Path | str | None = None,
        lessons_path: Path | str | None = None,
    ) -> None:
        self.repo_dir = Path(repo_dir) if repo_dir else Path.cwd()
        self.lessons_path = Path(lessons_path) if lessons_path else self.repo_dir / "LESSONS.md"

    def parse_lessons(self) -> list[LessonEntry]:
        """LESSONS.md の『## 恒久教訓インデックス』配下の教訓エントリを構造化パースする。"""
        import re

        if not self.lessons_path.exists():
            return []

        content = self.lessons_path.read_text(encoding="utf-8")
        header = "## 恒久教訓インデックス"
        if header in content:
            _, body = content.split(header, 1)
        else:
            body = content

        entries: list[LessonEntry] = []
        raw_sections = body.split("\n### ")
        for sec in raw_sections:
            sec = sec.strip()
            if not sec or sec.startswith("#"):
                continue

            lines = sec.splitlines()
            title_line = lines[0].strip()
            date_match = re.match(r"^\[(.*?)\]\s*(.*)$", title_line)
            date_str = date_match.group(1) if date_match else ""
            title_str = title_line

            context = ""
            trap = ""
            rule = ""
            rung = ""
            immunity_test = ""
            proof = ""

            for line in lines[1:]:
                line_s = line.strip()
                if line_s.startswith("- Context:"):
                    context = line_s[len("- Context:") :].strip()
                elif line_s.startswith("- Trap:"):
                    trap = line_s[len("- Trap:") :].strip()
                elif line_s.startswith("- Rule:"):
                    rule = line_s[len("- Rule:") :].strip()
                elif line_s.startswith("- Rung:"):
                    rung = line_s[len("- Rung:") :].strip()
                elif line_s.startswith("- Immunity Test:"):
                    immunity_test = line_s[len("- Immunity Test:") :].strip()
                elif line_s.startswith("- Proof:"):
                    proof = line_s[len("- Proof:") :].strip()

            entries.append(
                LessonEntry(
                    title=title_str,
                    date=date_str,
                    context=context,
                    trap=trap,
                    rule=rule,
                    rung=rung,
                    immunity_test=immunity_test,
                    proof=proof,
                    raw_markdown=f"### {sec}",
                )
            )

        return entries

    def find_relevant_lessons(self, query: str, max_results: int = 5) -> list[LessonEntry]:
        """クエリ（タスク名、ファイルパス、説明文等）から関連教訓をスコアリング検索する。"""
        import re

        entries = self.parse_lessons()
        if not entries:
            return []

        if not query or not query.strip():
            return entries[:max_results]

        tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9_\-\.]+", query) if len(t) >= 2]
        if not tokens:
            return entries[:max_results]

        scored: list[tuple[int, LessonEntry]] = []
        for entry in entries:
            score = 0
            haystack_title = entry.title.lower()
            haystack_ctx = entry.context.lower()
            haystack_trap = entry.trap.lower()
            haystack_rule = entry.rule.lower()

            for tok in tokens:
                if tok in haystack_title:
                    score += 10
                if tok in haystack_ctx:
                    score += 6
                if tok in haystack_trap:
                    score += 4
                if tok in haystack_rule:
                    score += 4

            if score > 0:
                scored.append((score, entry))

        if not scored:
            return entries[:max_results]

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:max_results]]

    def format_injection(self, entries: list[LessonEntry]) -> str:
        """Maker プロンプトやコンテキストに注入するための Markdown ブロックを生成する。"""
        if not entries:
            return ""
        lines = ["## 関連する教訓・Traps (from LESSONS.md / Rule 10)"]
        for entry in entries:
            lines.append(f"### {entry.title}")
            if entry.trap:
                lines.append(f"- Trap: {entry.trap}")
            if entry.rule:
                lines.append(f"- Rule: {entry.rule}")
            if entry.immunity_test:
                lines.append(f"- Immunity Test: {entry.immunity_test}")
            lines.append("")
        return "\n".join(lines).strip()

    def get_lessons_for_task(
        self,
        task_title: str,
        task_description: str = "",
        max_results: int = 5,
    ) -> str:
        """タスクのタイトルと説明から関連する教訓を検索し、注入用テキストを返す (QQQ.3 / FFF.4)。"""
        query = f"{task_title} {task_description}".strip()
        entries = self.find_relevant_lessons(query=query, max_results=max_results)
        return self.format_injection(entries)
