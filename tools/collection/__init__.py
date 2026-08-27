"""Consent-aware collection health and immutable dataset-freeze tooling."""

from .config import CollectionSettings, load_collection_settings
from .eligibility import ConsentRecord, EnrollmentRecord, eligibility_reasons
from .freeze import FreezeError, build_freeze, verify_freeze
from .health import CollectionHealthReport, WindowSummary, build_health_report
from .pause import PauseControlError, WindowsPauseController

__all__ = [
    "CollectionHealthReport",
    "CollectionSettings",
    "ConsentRecord",
    "EnrollmentRecord",
    "FreezeError",
    "PauseControlError",
    "WindowSummary",
    "WindowsPauseController",
    "build_freeze",
    "build_health_report",
    "eligibility_reasons",
    "load_collection_settings",
    "verify_freeze",
]
