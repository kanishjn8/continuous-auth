"""SQLite/WAL persistence and append-only audit boundaries."""

from .config import StorageSettings, load_storage_settings
from .errors import (
    StorageConfigError,
    StorageError,
    StorageIntegrityError,
    StorageMigrationError,
    StoragePermissionError,
    StorageUnavailableError,
)
from .retention import RetentionResult
from .service import AvailabilityEvent, StorageService

__all__ = [
    "AvailabilityEvent",
    "RetentionResult",
    "StorageConfigError",
    "StorageError",
    "StorageIntegrityError",
    "StorageMigrationError",
    "StoragePermissionError",
    "StorageService",
    "StorageSettings",
    "StorageUnavailableError",
    "load_storage_settings",
]
