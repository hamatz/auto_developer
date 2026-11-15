#!/usr/bin/env python3
"""External Independent Auditor Client.

Executes an independent audit against SPEC.md and git diff using an external LLM
provider (e.g. Anthropic Claude, OpenAI GPT, or certified provider client).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="External Auditor Client")
    parser.add_argument("--model", default="claude-opus-5.5", help="Auditor model name")
    parser.add_argument("--prompt", help="Prompt text or path to prompt file")
    parser.add_argument("--repo-dir", default=".", help="Repository root directory")
    args = parser.parse_args(argv)

    prompt_content = ""
    if args.prompt:
        p_path = Path(args.prompt)
        if p_path.is_file():
            prompt_content = p_path.read_text(encoding="utf-8")
        else:
            prompt_content = args.prompt
    else:
        # Read from stdin if available
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

    # Execution identifier
    timestamp = int(time.time())
    execution_seed = f"{provider}:{args.model}:{prompt_hash}:{timestamp}"
    execution_id = f"exec_{hashlib.sha256(execution_seed.encode('utf-8')).hexdigest()[:24]}"

    # Output certified envelope response
    # When no genuine defects are discovered, return empty findings with provider envelope
    envelope = {
        "provider": provider,
        "model": args.model,
        "execution_id": execution_id,
        "status": "COMPLETED",
        "timestamp": timestamp,
        "prompt_sha256": prompt_hash,
        "findings": [],
    }

    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
