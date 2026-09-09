"""The single centralized corpus-eligibility filter for attacker drills.

ADR-014 makes the attacker-drill data flow one-way: a drill window is scored,
risk-assessed, escalated, enforced, alerted, audited, and streamed, and it may
never become training data. This module is the one definition of "may never
become training data", so that every consumer of ``feature_windows`` applies
the same rule and a new consumer has an obvious thing to import.

Every site that reads ``feature_windows`` for a *corpus* purpose applies
:data:`DRILL_EXCLUSION_SQL`:

===============================================  ================================
Site                                             Purpose
===============================================  ================================
``tools/collection/repository.py``                health, freeze, verify
``tools/collection/corpus.py``                    training / enrollment /
                                                  validation / evaluation corpus
``backend/app/runtime/orchestrator.py``           enrollment progress counts
``ml/experiments/app_usage_study.py``             dataset statistics
``tools/demo/bootstrap_first_model.py``           synthetic demo training
===============================================  ================================

Sites that deliberately do **not** filter, because excluding drill data there
would hide the drill rather than protect the corpus:

* ``backend/app/storage/service.py`` -- provenance lookups while storing a
  score. A drill window must still be scored and stored.
* ``backend/app/storage/retention.py`` -- deletion. Drill windows age out like
  any other.
* ``backend/app/api/backend.py`` -- the operator-facing profile view. It
  reports observed volume, and hiding a live drill from the operator watching
  it would be actively misleading. Note this is display only: it feeds no
  training, calibration, or state decision. The enrollment/calibration
  *progress* counts that do drive state live in the orchestrator and are
  filtered.

Guardrail G12 (``tools/guardrails/check.py``) fails the build if a corpus
loader stops filtering. The regex cannot prove the filter is correct --
``backend/tests/test_attack_drill.py`` does that -- but it catches deletion.
"""

from __future__ import annotations

import sqlite3

#: SQL predicate excluding every window that belongs to a declared drill
#: session. Written as a bare boolean so it can be appended to an existing
#: ``WHERE`` clause with ``AND`` or used as the whole clause.
DRILL_EXCLUSION_PREDICATE = "session_id NOT IN (SELECT session_id FROM drill_sessions)"

#: The same predicate as a complete ``WHERE`` clause, for queries that have no
#: other condition.
DRILL_EXCLUSION_SQL = f"WHERE {DRILL_EXCLUSION_PREDICATE}"


class DrillTableMissingError(RuntimeError):
    """Raised when a corpus read cannot prove drill sessions were excluded.

    An un-migrated database cannot answer "is this window from a drill?", and
    silently answering "no" would be exactly the failure this module exists to
    prevent. Refusing is the fail-safe behaviour: run the backend once against
    the database so migration 0004 applies.
    """


def require_drill_table(connection: sqlite3.Connection) -> None:
    """Fail loudly if ``drill_sessions`` is absent from this database."""

    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'drill_sessions'"
    ).fetchone()
    if row is None:
        raise DrillTableMissingError(
            "DRILL_TABLE_MISSING: this database predates storage migration "
            "0004_attack_drill and cannot prove that attacker-drill windows are "
            "excluded from the corpus. Open it once through StorageService so the "
            "migration applies, then retry."
        )


def drill_session_ids(connection: sqlite3.Connection) -> frozenset[str]:
    """Return every declared drill session id, for non-SQL filtering paths."""

    require_drill_table(connection)
    rows = connection.execute("SELECT session_id FROM drill_sessions").fetchall()
    return frozenset(str(row[0]) for row in rows)
