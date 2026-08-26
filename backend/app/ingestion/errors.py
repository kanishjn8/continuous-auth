"""Explicit errors at the ingestion boundary."""


class IngestionError(RuntimeError):
    """Base ingestion failure."""


class IngestionConfigError(IngestionError):
    """Configuration is missing or invalid."""


class IngestionLifecycleError(IngestionError):
    """An authenticated session lifecycle operation is invalid."""
