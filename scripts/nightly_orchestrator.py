#!/usr/bin/env python3
"""Antigravity Autonomous Pipeline Orchestrator (CLI Wrapper for Backward Compatibility)."""

import sys
from pathlib import Path

# Ensure src/ is on sys.path so auto_developer can be imported directly
src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer import (
    CommandRunnerAdapter,
    DeterministicVerifier,
    EvidenceStore,
    ExecutionResult,
    GitGuard,
    JobState,
    MockRunnerAdapter,
    MorningReporter,
    NightlyOrchestrator,
    RunnerAdapter,
    Task,
    TaskDispatcher,
)
from auto_developer.cli import main

__all__ = [
    "CommandRunnerAdapter",
    "DeterministicVerifier",
    "EvidenceStore",
    "ExecutionResult",
    "GitGuard",
    "JobState",
    "MockRunnerAdapter",
    "MorningReporter",
    "NightlyOrchestrator",
    "RunnerAdapter",
    "Task",
    "TaskDispatcher",
    "main",
]

if __name__ == "__main__":
    sys.exit(main())
