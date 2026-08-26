"""SQLite WAL connection, migration, integrity, and transaction boundaries."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from .errors import StorageIntegrityError, StorageMigrationError, StorageUnavailableError

MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")


class SQLiteDatabase:
    """Open short-lived configured connections so concurrent callers are isolated."""

    def __init__(self, path: Path, busy_timeout_ms: int) -> None:
        self.path = path
        self.busy_timeout_ms = busy_timeout_ms

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms:d}")
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except sqlite3.Error as exc:
            raise StorageUnavailableError(f"cannot open local storage: {exc}") from exc

    def initialise(self) -> None:
        """Enable WAL, apply immutable forward migrations, and verify integrity."""

        with self.connection() as connection:
            try:
                mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
                if mode.casefold() != "wal":
                    raise StorageUnavailableError("SQLite refused WAL mode")
                self._apply_migrations(connection)
                result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                if result != "ok":
                    raise StorageIntegrityError(f"SQLite integrity check failed: {result}")
            except (StorageIntegrityError, StorageMigrationError, StorageUnavailableError):
                raise
            except sqlite3.Error as exc:
                raise StorageIntegrityError(f"SQLite initialisation failed: {exc}") from exc

    def _apply_migrations(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                checksum TEXT NOT NULL CHECK(length(checksum) = 64),
                applied_at_utc TEXT NOT NULL
            )
            """
        )
        migration_paths = sorted(MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        if not migration_paths:
            raise StorageMigrationError("no storage migrations were found")
        for migration_path in migration_paths:
            version_text, _, name = migration_path.stem.partition("_")
            version = int(version_text)
            sql = migration_path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            existing = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if existing is not None:
                if existing["checksum"] != checksum:
                    raise StorageMigrationError(
                        f"applied migration {version_text} was modified; restore the original file"
                    )
                continue
            script = (
                "BEGIN IMMEDIATE;\n"
                + sql
                + "\nINSERT INTO schema_migrations(version, name, checksum, applied_at_utc) "
                + f"VALUES ({version}, '{name}', '{checksum}', "
                + "strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));\n"
                + f"PRAGMA user_version = {version};\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except sqlite3.Error as exc:
                with suppress(sqlite3.Error):
                    connection.execute("ROLLBACK")
                raise StorageMigrationError(
                    f"migration {migration_path.name} failed and was rolled back: {exc}"
                ) from exc

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run all yielded statements atomically with a reserved write lock."""

        with self.connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except Exception as exc:
                with suppress(sqlite3.Error):
                    connection.execute("ROLLBACK")
                if isinstance(exc, sqlite3.Error):
                    raise StorageUnavailableError(f"SQLite transaction failed: {exc}") from exc
                raise
