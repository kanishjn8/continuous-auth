CREATE TABLE verification_anchors (
    anchor_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    segment_id TEXT REFERENCES segments(segment_id) ON DELETE CASCADE,
    anchor_type TEXT NOT NULL CHECK(anchor_type IN ('A1_LOGIN_UNLOCK', 'A2_REAUTH', 'A3_SCHEDULED_PROMPT')),
    evidence_reference TEXT NOT NULL,
    authenticated_at_utc TEXT NOT NULL,
    audit_revision INTEGER NOT NULL DEFAULT 1 CHECK(audit_revision >= 1),
    schema_version TEXT NOT NULL,
    UNIQUE(user_id, evidence_reference)
);

CREATE TABLE model_profiles (
    profile_version TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    keyboard_artifact_version TEXT,
    mouse_artifact_version TEXT,
    aggregate_checksum TEXT NOT NULL CHECK(length(aggregate_checksum) = 64),
    validation_json TEXT NOT NULL CHECK(json_valid(validation_json)),
    status TEXT NOT NULL CHECK(status IN ('RETAINED', 'ACTIVE', 'REJECTED')),
    created_at_utc TEXT NOT NULL,
    activated_at_utc TEXT,
    retired_at_utc TEXT,
    CHECK(keyboard_artifact_version IS NOT NULL OR mouse_artifact_version IS NOT NULL)
);

CREATE TABLE update_runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    scheduled_for_utc TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    status TEXT NOT NULL CHECK(status IN ('RUNNING', 'ACTIVATED', 'REJECTED', 'FAILED')),
    report_json TEXT NOT NULL CHECK(json_valid(report_json))
);

CREATE UNIQUE INDEX idx_model_profiles_one_active
    ON model_profiles(user_id) WHERE status = 'ACTIVE';
CREATE INDEX idx_verification_anchors_segment
    ON verification_anchors(user_id, segment_id, authenticated_at_utc);
CREATE INDEX idx_model_profiles_user_created
    ON model_profiles(user_id, created_at_utc);
CREATE INDEX idx_update_runs_user_schedule
    ON update_runs(user_id, scheduled_for_utc);
