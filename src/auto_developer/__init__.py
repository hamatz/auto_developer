"""Auto Developer: Trustworthy Autonomous Pipeline Orchestrator."""

from auto_developer.cli import main
from auto_developer.dispatcher import TaskDispatcher
from auto_developer.evidence import EvidenceStore
from auto_developer.guard import GitGuard
from auto_developer.models import ExecutionResult, JobState, Task
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.reporter import MorningReporter
from auto_developer.runner import (
    CommandRunnerAdapter,
    MockRunnerAdapter,
    RunnerAdapter,
)
from auto_developer.verifier import DeterministicVerifier

__version__ = "0.3.0"

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
