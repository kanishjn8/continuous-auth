"""C1 framing, ordering, session, and segment ingestion (T-007)."""

from .config import IngestionSettings, load_ingestion_settings
from .errors import IngestionConfigError, IngestionLifecycleError
from .pipeline import IngestionPipeline
from .types import (
    AttributedEvent,
    AuthenticatedEntry,
    AvailabilityEvent,
    IngestionBatch,
    IngestionCounters,
    LifecycleEvent,
)

__all__ = [
    "AttributedEvent",
    "AuthenticatedEntry",
    "AvailabilityEvent",
    "IngestionBatch",
    "IngestionConfigError",
    "IngestionCounters",
    "IngestionLifecycleError",
    "IngestionPipeline",
    "IngestionSettings",
    "LifecycleEvent",
    "load_ingestion_settings",
]
