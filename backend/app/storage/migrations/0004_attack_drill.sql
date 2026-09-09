-- Declared live attacker-drill sessions (ADR-014, PLAN.md Section 13.3).
--
-- Session-grained, because a drill IS a session: one person sits down,
-- operates the machine, and leaves. Recording the fact here rather than on
-- feature_windows keeps the protocol-generated FeatureWindow, the freeze
-- digest, and every existing table untouched, and gives every corpus loader
-- exactly one place to filter.
--
-- Windows belonging to a row in this table are still scored, risk-assessed,
-- escalated, enforced, alerted, audited, and streamed -- that is the entire
-- point of a drill. What they may never do is become training data. See
-- backend/app/storage/drill.py for the single centralized exclusion predicate
-- and every site that applies it.
CREATE TABLE drill_sessions (
    session_id      TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
    drill_label     TEXT NOT NULL CHECK(length(drill_label) > 0),
    declared_at_utc TEXT NOT NULL,
    schema_version  TEXT NOT NULL
);
