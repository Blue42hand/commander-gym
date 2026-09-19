"""Public Commander Gym research/evaluation interfaces."""

from .argentum import (
    ArgentumCanonicalDecision,
    ArgentumCanonicalError,
    StaleArgentumDecisionError,
)
from .argentum_client import (
    ArgentumClientConfigurationError,
    ArgentumClientError,
    ArgentumConnectionError,
    ArgentumDeliveryUnknownError,
    ArgentumGymClient,
    ArgentumRemoteError,
)
from .benchmark_runner import (
    BENCHMARK_REPORT_VERSION,
    BenchmarkPilot,
    CallablePilot,
    PilotDecision,
    first_legal_pilot,
    preferred_or_first_pilot,
    run_benchmark,
)
from .records import ActionRecord, DecisionRecord, PilotProvenance, RecordValidationError

__all__ = [
    "ActionRecord",
    "ArgentumCanonicalDecision",
    "ArgentumCanonicalError",
    "ArgentumClientConfigurationError",
    "ArgentumClientError",
    "ArgentumConnectionError",
    "ArgentumDeliveryUnknownError",
    "ArgentumGymClient",
    "ArgentumRemoteError",
    "BENCHMARK_REPORT_VERSION",
    "BenchmarkPilot",
    "CallablePilot",
    "DecisionRecord",
    "PilotDecision",
    "PilotProvenance",
    "RecordValidationError",
    "StaleArgentumDecisionError",
    "first_legal_pilot",
    "preferred_or_first_pilot",
    "run_benchmark",
]
