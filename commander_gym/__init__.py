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
from .deck_package import (
    DECK_PACKAGE_SCHEMA_VERSION,
    ArtifactRef,
    DeckPackage,
    DeckPackageError,
    validate_commander_package,
)
from .pilot import (
    ArtificialPlayer,
    ArgentumActionChoice,
    ArgentumDecisionChoice,
    PilotChoice,
    PilotContractError,
    choose_for_observation,
    validate_pilot_choice,
)
from .records import ActionRecord, DecisionRecord, PilotProvenance, RecordValidationError

__all__ = [
    "ActionRecord",
    "ArtificialPlayer",
    "ArgentumActionChoice",
    "ArgentumCanonicalDecision",
    "ArgentumCanonicalError",
    "ArgentumClientConfigurationError",
    "ArgentumClientError",
    "ArgentumConnectionError",
    "ArgentumDecisionChoice",
    "ArgentumDeliveryUnknownError",
    "ArgentumGymClient",
    "ArgentumRemoteError",
    "ArtifactRef",
    "BENCHMARK_REPORT_VERSION",
    "BenchmarkPilot",
    "CallablePilot",
    "DECK_PACKAGE_SCHEMA_VERSION",
    "DeckPackage",
    "DeckPackageError",
    "DecisionRecord",
    "PilotChoice",
    "PilotContractError",
    "PilotDecision",
    "PilotProvenance",
    "RecordValidationError",
    "StaleArgentumDecisionError",
    "choose_for_observation",
    "first_legal_pilot",
    "preferred_or_first_pilot",
    "run_benchmark",
    "validate_commander_package",
    "validate_pilot_choice",
]
