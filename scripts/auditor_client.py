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
    """Extract finding dictionaries from LLM response text."""
    # 1. Try markdown code block ```json ... ```
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1))
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

    # 2. Try raw JSON list [ ... ]
    m_raw = re.search(r"(\[\s*\{.*?\}\s*\])", text, re.DOTALL)
    if m_raw:
        try:
            parsed = json.loads(m_raw.group(1))
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

    # 3. If single JSON object or empty array
    m_empty = re.search(r"\[\s*\]", text)
    if m_empty:
        return []

    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="External Auditor Client")
    parser.add_argument("--model", default="claude-opus-5.5", help="Auditor model name")
    parser.add_argument("--prompt", help="Prompt text or path to prompt file")
    parser.add_argument("--repo-dir", default=".", help="Repository root directory")
    parser.add_argument("--api-key", help="API key for LLM provider")
    parser.add_argument("--mock", action="store_true", help="Explicit mock mode for offline test fixtures only")
    args = parser.parse_args(argv)

    repo_path = Path(args.repo_dir).resolve()
    file_env = _load_env_file(repo_path)

    prompt_content = ""
    if args.prompt:
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

    prompt_hash = hashlib.sha256(prompt_content.encode("utf-8")).hexdigest()

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
            "Respond ONLY with the JSON array enclosed in ```json ... ```."
        )

        payload = {
            "model": anthropic_model,
            "max_tokens": 4096,
            "system": system_instruction,
            "messages": [
                {"role": "user", "content": prompt_content or "Audit repository diff against SPEC.md"}
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
            with urllib.request.urlopen(req, timeout=120) as resp:
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

        provider_req_id = resp_json.get("id") or f"msg_unknown_{timestamp}"
        raw_text = ""
        content_items = resp_json.get("content", [])
        if isinstance(content_items, list):
            for block in content_items:
                if isinstance(block, dict) and block.get("type") == "text":
                    raw_text += block.get("text", "")

        findings = _extract_findings_from_text(raw_text)

        envelope = {
            "mode": "LIVE",
            "provider": "anthropic",
            "model": args.model,
            "resolved_model": resp_json.get("model", anthropic_model),
            "provider_request_id": provider_req_id,
            "execution_id": provider_req_id,
            "status": "COMPLETED",
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
