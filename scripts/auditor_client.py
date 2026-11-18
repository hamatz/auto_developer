#!/usr/bin/env python3
"""External Independent Auditor Client.

Executes an independent audit against SPEC.md and git diff using an external LLM
provider (e.g. Anthropic Claude, OpenAI GPT, or certified provider client).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _load_env_file(repo_dir: Path) -> dict[str, str]:
    """Load key-value pairs from .env file if it exists."""
    env_vars: dict[str, str] = {}
    candidate = repo_dir / ".env"
    if candidate.is_file():
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and v:
                        env_vars[k] = v
        except OSError:
            pass
    return env_vars


def _extract_findings_from_text(text: str) -> list[dict]:
    """Extract finding dictionaries from LLM response text.

    Fail-Closed (VVV.1 / Sol Phase 61 P1 / AUD-001 / AUD-002):
    If text does not contain a valid JSON array or explicit '[]', raise ValueError.
    Do NOT convert unparseable text or arbitrary prose into '[]' (which would fake zero defects).
    If multiple code blocks exist (e.g. format example followed by actual findings), prioritize non-empty findings.
    If any JSON code block fails to decode, fail closed instead of masking behind an empty example block.
    """
    clean_text = text.strip()

    # 0. Check for enclosing error/failure envelope (Fail-Closed: reject failed envelopes even if findings array is present: AUD-016)
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", clean_text):
        try:
            p_obj, _ = decoder.raw_decode(clean_text, m.start())
            if isinstance(p_obj, dict):
                if p_obj.get("error"):
                    raise ValueError(f"AUDIT_FAILED: Auditor response contains error: {p_obj.get('error')}")
                env_status = p_obj.get("status")
                if env_status and env_status != "COMPLETED" and "finding_id" not in p_obj:
                    raise ValueError(f"AUDIT_{env_status}: Live audit envelope returned non-completed status '{env_status}'")
        except json.JSONDecodeError:
            pass

    # 1. First, check if the raw text from first '[' to last ']' forms a valid JSON list of findings
    # This correctly handles cases where findings contain embedded code blocks or markdown fences in strings.
    start = clean_text.find("[")
    end = clean_text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(clean_text[start : end + 1])
            if isinstance(parsed, list):
                findings = [item for item in parsed if isinstance(item, dict) and "finding_id" in item]
                if len(parsed) == 0:
                    pass
                elif len(findings) == len(parsed):
                    return findings
                else:
                    raise ValueError(f"JSON array contains non-finding items: {parsed}")
        except json.JSONDecodeError:
            pass

    # 2. Inspect ALL markdown code blocks ```json ... ```
    all_code_blocks = list(re.finditer(r"```[ \t]*(?:[a-zA-Z0-9_-]+)?[ \t]*\n?(.*?)\n?```", clean_text, re.DOTALL))
    empty_block_found = False
    has_decode_error = False
    decode_error_msg = ""
    collected_findings: list[dict] = []
    for m in all_code_blocks:
        content = m.group(1).strip()
        # Candidate findings must begin with a JSON structure character ('[' or '{')
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
                    decode_error_msg = f"JSON array contains non-finding items: {parsed}"
            elif isinstance(parsed, dict):
                if parsed.get("error"):
                    raise ValueError(f"AUDIT_FAILED: Auditor response contains error: {parsed.get('error')}")
                elif "finding_id" in parsed and "findings" not in parsed:
                    collected_findings.append(parsed)
                elif "findings" in parsed and isinstance(parsed["findings"], list):
                    env_status = parsed.get("status")
                    if env_status and env_status != "COMPLETED":
                        raise ValueError(f"AUDIT_{env_status}: Live audit envelope returned non-completed status '{env_status}'")
                    findings = [item for item in parsed["findings"] if isinstance(item, dict) and "finding_id" in item]
                    if len(parsed["findings"]) == 0:
                        empty_block_found = True
                    elif len(findings) == len(parsed["findings"]):
                        collected_findings.extend(findings)
                    else:
                        has_decode_error = True
                        decode_error_msg = f"Findings array contains non-finding items: {parsed['findings']}"
                elif parsed.get("status") and parsed.get("status") != "COMPLETED":
                    raise ValueError(f"AUDIT_{parsed.get('status')}: Live audit envelope returned non-completed status '{parsed.get('status')}'")
                else:
                    has_decode_error = True
                    decode_error_msg = f"Unrecognised JSON object in code block: {list(parsed.keys())}"
        except json.JSONDecodeError as exc:
            has_decode_error = True
            decode_error_msg = str(exc)

    if has_decode_error:
        raise ValueError(f"Malformed JSON in audit response code block: {decode_error_msg}")

    # 3. Check for raw findings in text outside code blocks (handles example code block + raw findings outside)
    text_outside = re.sub(r"```[ \t]*(?:[a-zA-Z0-9_-]+)?[ \t]*\n?(.*?)\n?```", "", clean_text, flags=re.DOTALL)
    decoder = json.JSONDecoder()
    raw_collected_findings: list[dict] = []
    raw_has_decode_error = False
    raw_decode_error_msg = ""
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
                    raw_collected_findings.extend(findings)
                    break
                else:
                    raise ValueError(f"JSON array contains non-finding items: {parsed_out}")
        except json.JSONDecodeError:
            pass

    for m_brace in re.finditer(r"\{", text_outside):
        s_idx = m_brace.start()
        try:
            parsed_out, _ = decoder.raw_decode(text_outside, s_idx)
            if isinstance(parsed_out, dict):
                if parsed_out.get("error"):
                    raise ValueError(f"AUDIT_FAILED: Auditor response contains error: {parsed_out.get('error')}")
                env_status = parsed_out.get("status")
                if env_status and env_status != "COMPLETED":
                    raise ValueError(f"AUDIT_{env_status}: Live audit envelope returned non-completed status '{env_status}'")
                if "findings" in parsed_out and isinstance(parsed_out["findings"], list):
                    findings = [item for item in parsed_out["findings"] if isinstance(item, dict) and "finding_id" in item]
                    if len(parsed_out["findings"]) == 0:
                        pass
                    elif len(findings) == len(parsed_out["findings"]):
                        if not raw_collected_findings:
                            raw_collected_findings.extend(findings)
                        break
                    else:
                        raise ValueError(f"JSON findings array contains non-finding items: {parsed_out['findings']}")
                elif "finding_id" in parsed_out:
                    if not raw_collected_findings:
                        raw_collected_findings.append(parsed_out)
                    break
        except json.JSONDecodeError:
            pass

    for m in re.finditer(r"\[\s*\{", text_outside):
        try:
            p_candidate, _ = decoder.raw_decode(text_outside, m.start())
            if not isinstance(p_candidate, list):
                raw_has_decode_error = True
                raw_decode_error_msg = f"Candidate array did not decode to list: {p_candidate}"
        except json.JSONDecodeError as exc:
            raw_has_decode_error = True
            raw_decode_error_msg = str(exc)

    for m in re.finditer(r"\{\s*[\"'](?:finding_id|findings)[\"']", text_outside):
        try:
            p_candidate, _ = decoder.raw_decode(text_outside, m.start())
            if not isinstance(p_candidate, dict):
                raw_has_decode_error = True
                raw_decode_error_msg = f"Candidate dict did not decode to dict: {p_candidate}"
        except json.JSONDecodeError as exc:
            raw_has_decode_error = True
            raw_decode_error_msg = str(exc)

    if not parsed_raw_bracket and re.search(r"(?:\[\s*\{|\{\s*[\"'](?:finding_id|findings)[\"'])", text_outside):
        raw_has_decode_error = True
        raw_decode_error_msg = "Invalid JSON structure outside code block"

    if raw_has_decode_error:
        raise ValueError(f"Malformed JSON in audit response outside code blocks: {raw_decode_error_msg}")

    if collected_findings:
        return collected_findings

    if raw_collected_findings:
        return raw_collected_findings

    # If any block had non-empty findings, it was returned above.
    # If a block was explicitly empty [] and no block had non-empty findings:
    if empty_block_found:
        return []

    # 4. Explicit empty array: must strictly match [] alone or inside single markdown code block
    if re.fullmatch(r"(?:```(?:json)?\s*)?\[\s*\](?:\s*```)?", clean_text):
        return []

    # Fail-Closed: unparseable text must NOT be treated as zero defects
    raise ValueError(
        f"UNPARSEABLE_AUDIT_RESPONSE: No valid JSON array found in auditor response (preview: {clean_text[:120]!r})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="External Auditor Client")
    parser.add_argument("--model", default="claude-opus-5.5", help="Auditor model name")
    parser.add_argument("--prompt", help="Prompt text or path to prompt file")
    parser.add_argument("--repo-dir", default=".", help="Repository root directory")
    parser.add_argument("--api-key", help="API key for LLM provider")
    parser.add_argument("--max-tokens", type=int, default=8192, help="Max tokens for LLM completion")
    parser.add_argument("--timeout", type=int, default=300, help="Socket and HTTP timeout in seconds")
    parser.add_argument("--mock", action="store_true", help="Explicit mock mode for offline test fixtures only")
    args = parser.parse_args(argv)

    repo_path = Path(args.repo_dir).resolve()
    file_env = _load_env_file(repo_path)

    prompt_content = ""
    if args.prompt and args.prompt not in ("{prompt}", "{prompt_file}"):
        if len(args.prompt) < 1024 and "\n" not in args.prompt:
            try:
                p_path = Path(args.prompt)
                if p_path.is_file():
                    prompt_content = p_path.read_text(encoding="utf-8")
                else:
                    prompt_content = args.prompt
            except OSError:
                prompt_content = args.prompt
        else:
            prompt_content = args.prompt
    else:
        if not sys.stdin.isatty():
            prompt_content = sys.stdin.read()

    # Determine provider family
    model_lower = args.model.lower()
    if "claude" in model_lower or "anthropic" in model_lower:
        provider = "anthropic"
    elif "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower or "openai" in model_lower:
        provider = "openai"
    elif "gemini" in model_lower:
        provider = "google"
    else:
        provider = "independent"

    # AUD-001 / Fail-Closed: 空白または未置換テンプレートによる内容皆無の監査実行を遮断
    if not prompt_content or not prompt_content.strip() or prompt_content.strip() in ("{prompt}", "{prompt_file}"):
        err_envelope = {
            "mode": "MOCK" if args.mock else "LIVE",
            "provider": provider,
            "model": args.model,
            "status": "FAILED",
            "error": "EMPTY_AUDIT_PROMPT: No audit prompt provided via --prompt or stdin (Fail-Closed)",
            "findings": [],
        }
        print(json.dumps(err_envelope, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1

    prompt_hash = hashlib.sha256(prompt_content.encode("utf-8")).hexdigest()

    timestamp = int(time.time())

    # --- MOCK MODE (For unit tests only) ---
    if args.mock:
        execution_seed = f"mock:{provider}:{args.model}:{prompt_hash}:{timestamp}"
        execution_id = f"mock_{hashlib.sha256(execution_seed.encode('utf-8')).hexdigest()[:24]}"
        envelope = {
            "mode": "MOCK",
            "provider": provider,
            "model": args.model,
            "execution_id": execution_id,
            "provider_request_id": None,
            "status": "COMPLETED",
            "timestamp": timestamp,
            "prompt_sha256": prompt_hash,
            "response_sha256": hashlib.sha256(b"[]").hexdigest(),
            "findings": [],
        }
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return 0

    # --- LIVE ANTHROPIC API MODE ---
    if provider == "anthropic":
        api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY") or file_env.get("ANTHROPIC_API_KEY")
        if not api_key:
            err_envelope = {
                "mode": "LIVE",
                "provider": "anthropic",
                "model": args.model,
                "status": "FAILED",
                "error": "MISSING_API_KEY: ANTHROPIC_API_KEY is not set in environment or .env (Fail-Closed)",
                "findings": [],
            }
            print(json.dumps(err_envelope, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

        # Model mapping for Anthropic Messages API
        clean_model = args.model.replace(".", "-")
        if "opus" in model_lower:
            anthropic_model = "claude-opus-5-5" if "5" in model_lower else clean_model
        elif "sonnet" in model_lower:
            anthropic_model = "claude-sonnet-5-5" if "5" in model_lower else clean_model
        elif "haiku" in model_lower:
            anthropic_model = "claude-haiku-5-5" if "5" in model_lower else clean_model
        else:
            anthropic_model = clean_model

        system_instruction = (
            "You are an independent third-party technical auditor verifying software specifications "
            "and code diffs. Analyze the provided SPEC.md and git diff carefully. "
            "If you identify legitimate specification violations, logic defects, or edge-case omissions, "
            "report them as a JSON array of objects conforming to schema:\n"
            "[\n"
            "  {\n"
            '    "finding_id": "AUD-001",\n'
            '    "severity": "P0" | "P1" | "P2",\n'
            '    "spec_reference": "SPEC.md section",\n'
            '    "location": "file:line",\n'
            '    "description": "Clear defect description",\n'
            '    "reproduction_test_code": "Python unittest reproducing the failure",\n'
            '    "status": "REPORTED"\n'
            "  }\n"
            "]\n"
            "If the code completely and correctly satisfies SPEC.md with no defects, return an empty array `[]`.\n"
            "CRITICAL: Output ONLY the JSON array enclosed in ```json ... ``` without any additional markdown text, introductions, or explanations."
        )

        payload = {
            "model": anthropic_model,
            "max_tokens": args.max_tokens,
            "system": system_instruction,
            "messages": [
                {"role": "user", "content": prompt_content}
            ],
        }

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                "user-agent": "auto_developer/0.7.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as resp:
                body = resp.read().decode("utf-8")
                resp_json = json.loads(body)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            err_envelope = {
                "mode": "LIVE",
                "provider": "anthropic",
                "model": args.model,
                "status": "AUDIT_PENDING" if e.code in (429, 503) else "FAILED",
                "error": f"HTTP_ERROR_{e.code}: {err_body[:200]}",
                "findings": [],
            }
            print(json.dumps(err_envelope, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
            err_envelope = {
                "mode": "LIVE",
                "provider": "anthropic",
                "model": args.model,
                "status": "FAILED",
                "error": f"CONNECTION_ERROR: {e!s}",
                "findings": [],
            }
            print(json.dumps(err_envelope, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

        stop_reason = resp_json.get("stop_reason")
        if not stop_reason:
            err_envelope = {
                "mode": "LIVE",
                "provider": "anthropic",
                "model": args.model,
                "status": "AUDIT_PENDING",
                "error": "MISSING_STOP_REASON: Provider response did not include a valid stop_reason",
                "findings": [],
            }
            print(json.dumps(err_envelope, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

        # VVV.1 / Sol Phase 61 P1 / AUD-002: Fail-Closed: stop_reason must strictly be "end_turn" (allow-list)
        # Any other value (max_tokens, refusal, stop_sequence, pause_turn, etc.) is incomplete / interrupted
        if stop_reason != "end_turn":
            content_preview = ""
            for b in resp_json.get("content", []):
                if isinstance(b, dict) and b.get("type") == "text":
                    content_preview += b.get("text", "")
            err_envelope = {
                "mode": "LIVE",
                "provider": "anthropic",
                "model": args.model,
                "status": "AUDIT_PENDING",
                "error": f"AUDIT_INTERRUPTED: Audit did not complete with end_turn (stop_reason={stop_reason})",
                "stop_reason": stop_reason,
                "usage": resp_json.get("usage"),
                "content_preview": content_preview[-500:],
                "findings": [],
            }
            print(json.dumps(err_envelope, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

        provider_req_id = resp_json.get("id")
        if not provider_req_id or not str(provider_req_id).startswith("msg_"):
            err_envelope = {
                "mode": "LIVE",
                "provider": "anthropic",
                "model": args.model,
                "status": "FAILED",
                "error": f"MISSING_PROVIDER_REQUEST_ID: Anthropic response missing valid provider request id (got {provider_req_id!r})",
                "stop_reason": stop_reason,
                "findings": [],
            }
            print(json.dumps(err_envelope, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        raw_text = ""
        content_items = resp_json.get("content", [])
        if isinstance(content_items, list):
            for block in content_items:
                if isinstance(block, dict) and block.get("type") == "text":
                    raw_text += block.get("text", "")

        audit_dir = (Path(args.repo_dir) / ".dev" / "audit") if args.repo_dir else Path(".dev/audit")
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / "last_raw_response.txt").write_text(raw_text, encoding="utf-8")

        try:
            findings = _extract_findings_from_text(raw_text)
        except ValueError as e:
            err_envelope = {
                "mode": "LIVE",
                "provider": "anthropic",
                "model": args.model,
                "status": "FAILED",
                "error": str(e),
                "stop_reason": stop_reason,
                "findings": [],
            }
            print(json.dumps(err_envelope, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1

        envelope = {
            "mode": "LIVE",
            "provider": "anthropic",
            "model": args.model,
            "resolved_model": resp_json.get("model", anthropic_model),
            "provider_request_id": provider_req_id,
            "execution_id": provider_req_id,
            "status": "COMPLETED",
            "stop_reason": stop_reason,
            "timestamp": timestamp,
            "prompt_sha256": prompt_hash,
            "response_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
            "usage": resp_json.get("usage", {}),
            "findings": findings,
        }
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return 0

    # Fallback for other providers (Fail-Closed if not supported or not configured)
    err_envelope = {
        "mode": "LIVE",
        "provider": provider,
        "model": args.model,
        "status": "FAILED",
        "error": f"UNSUPPORTED_PROVIDER: Provider '{provider}' not configured for live audit (Fail-Closed)",
        "findings": [],
    }
    print(json.dumps(err_envelope, ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
