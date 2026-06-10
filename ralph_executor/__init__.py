"""Public surface of the Ralph executor package.

Re-exports the stable names that Plans 8 (sweep), 9 (safety controls),
10 (supervisor skills), 11 (ralph-doctor), and 13 (smoke test) import.
The orchestrator's "Cross-plan integration points" section is the
contract; keep these names stable.
"""

from ralph_executor.cli import main
from ralph_executor.config import ConfigError, ExecutorConfig, load_config
from ralph_executor.host_select import (
    HostSelectionError,
    prepare_host_environment,
)
from ralph_executor.iteration import (
    IterationOutcome,
    IterationResult,
    iterate_once,
    run_loop,
)
from ralph_executor.types import PBI, PBIStatus, PBIType, Severity

__all__ = [
    "ConfigError",
    "ExecutorConfig",
    "HostSelectionError",
    "IterationOutcome",
    "IterationResult",
    "PBI",
    "PBIStatus",
    "PBIType",
    "Severity",
    "iterate_once",
    "load_config",
    "main",
    "prepare_host_environment",
    "run_loop",
]
