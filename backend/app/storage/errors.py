"""Safe, explicit failures for the local persistence boundary."""

from __future__ import annotations


class StorageError(RuntimeError):
    """Base error that callers must turn into a loud availability signal."""


class StorageConfigError(StorageError):
    """The storage configuration is missing, corrupt, or unsafe."""


class StorageMigrationError(StorageError):
    """A forward migration could not be applied or was modified in place."""


class StorageIntegrityError(StorageError):
    """SQLite or the audit hash chain failed an integrity check."""


class StoragePermissionError(StorageError):
    """The local storage location could not be access-restricted."""


class StorageUnavailableError(StorageError):
    """A write failed; enforcement must remain fail-open while this is raised."""
