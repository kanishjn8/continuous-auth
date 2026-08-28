CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK(state IN ('ENROLLING', 'CALIBRATING', 'ACTIVE', 'DEGRADED', 'SUSPENDED')),
    schema_version TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    entry_auth_evidence TEXT NOT NULL CHECK(entry_auth_evidence IN ('A1_LOGIN_UNLOCK', 'A2_REAUTH', 'A3_SCHEDULED_PROMPT')),
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT,
    schema_version TEXT NOT NULL,
    CHECK(ended_at_utc IS NULL OR ended_at_utc >= started_at_utc)
);

CREATE TABLE segments (
    segment_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    started_at_capture_us INTEGER NOT NULL CHECK(started_at_capture_us >= 0),
    ended_at_capture_us INTEGER CHECK(ended_at_capture_us IS NULL OR ended_at_capture_us >= started_at_capture_us),
    boundary_reason TEXT NOT NULL,
    schema_version TEXT NOT NULL
);

CREATE TABLE feature_windows (
    window_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    segment_id TEXT NOT NULL REFERENCES segments(segment_id),
    t_start_us INTEGER NOT NULL CHECK(t_start_us >= 0),
    t_end_us INTEGER NOT NULL CHECK(t_end_us >= t_start_us),
    quality_label TEXT NOT NULL CHECK(quality_label IN ('FULL', 'KBD_ONLY', 'MOUSE_ONLY', 'INSUFFICIENT_DATA')),
    key_event_count INTEGER NOT NULL CHECK(key_event_count >= 0),
    mouse_event_count INTEGER NOT NULL CHECK(mouse_event_count >= 0),
    collection_day TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK(provenance IN ('SYNTHETIC', 'PUBLIC', 'TEAM', 'PILOT')),
    keyboard_features_json TEXT CHECK(keyboard_features_json IS NULL OR json_valid(keyboard_features_json)),
    mouse_features_json TEXT CHECK(mouse_features_json IS NULL OR json_valid(mouse_features_json)),
    context_json TEXT NOT NULL CHECK(json_valid(context_json)),
    schema_version TEXT NOT NULL,
    stored_at_utc TEXT NOT NULL,
    CHECK(
        (quality_label = 'FULL' AND keyboard_features_json IS NOT NULL AND mouse_features_json IS NOT NULL) OR
        (quality_label = 'KBD_ONLY' AND keyboard_features_json IS NOT NULL AND mouse_features_json IS NULL) OR
        (quality_label = 'MOUSE_ONLY' AND keyboard_features_json IS NULL AND mouse_features_json IS NOT NULL) OR
        (quality_label = 'INSUFFICIENT_DATA' AND keyboard_features_json IS NULL AND mouse_features_json IS NULL)
    )
);

CREATE TABLE scores (
    score_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    window_id TEXT NOT NULL,
    profile_version TEXT NOT NULL,
    feature_schema_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    quality_label TEXT NOT NULL,
    score_json TEXT NOT NULL CHECK(json_valid(score_json)),
    schema_version TEXT NOT NULL,
    stored_at_utc TEXT NOT NULL,
    UNIQUE(window_id, model_version)
);

CREATE TABLE risk_events (
    decision_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    segment_id TEXT NOT NULL REFERENCES segments(segment_id),
    window_id TEXT NOT NULL,
    t_decision_us INTEGER NOT NULL CHECK(t_decision_us >= 0),
    fused_score REAL CHECK(fused_score IS NULL OR (fused_score >= 0 AND fused_score <= 1)),
    context_confidence REAL NOT NULL CHECK(context_confidence > 0 AND context_confidence <= 1),
    smoothed_score REAL CHECK(smoothed_score IS NULL OR (smoothed_score >= 0 AND smoothed_score <= 1)),
    risk_level TEXT NOT NULL CHECK(risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'UNAVAILABLE')),
    user_state TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('CONTINUE', 'SOFT_CHALLENGE', 'REAUTH', 'TERMINATE', 'NONE')),
    reason_code TEXT NOT NULL,
    threshold_config_version TEXT NOT NULL,
    config_checksum TEXT NOT NULL CHECK(length(config_checksum) = 64),
    shadow_mode INTEGER NOT NULL CHECK(shadow_mode IN (0, 1)),
    enforcement_applied INTEGER NOT NULL CHECK(enforcement_applied IN (0, 1)),
    decision_json TEXT NOT NULL CHECK(json_valid(decision_json)),
    schema_version TEXT NOT NULL,
    stored_at_utc TEXT NOT NULL
);

CREATE TABLE decisions (
    decision_id TEXT PRIMARY KEY REFERENCES risk_events(decision_id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    enforcement_applied INTEGER NOT NULL CHECK(enforcement_applied IN (0, 1)),
    outcome TEXT,
    outcome_at_utc TEXT
);

CREATE TABLE alerts (
    alert_id TEXT PRIMARY KEY,
    alert_type TEXT NOT NULL CHECK(alert_type IN ('BEHAVIORAL', 'AVAILABILITY', 'TAMPER')),
    severity TEXT NOT NULL CHECK(severity IN ('LOW', 'MEDIUM', 'HIGH')),
    code TEXT NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    acknowledged INTEGER NOT NULL CHECK(acknowledged IN (0, 1)),
    schema_version TEXT NOT NULL
);

CREATE TABLE models (
    user_id TEXT NOT NULL REFERENCES users(user_id),
    artifact_version TEXT NOT NULL,
    modality TEXT NOT NULL CHECK(modality IN ('KEYBOARD', 'MOUSE')),
    feature_schema_version TEXT NOT NULL,
    training_range_start TEXT NOT NULL,
    training_range_end TEXT NOT NULL,
    checksum TEXT NOT NULL CHECK(length(checksum) = 64),
    artifact_json TEXT NOT NULL CHECK(json_valid(artifact_json)),
    schema_version TEXT NOT NULL,
    stored_at_utc TEXT NOT NULL,
    PRIMARY KEY(user_id, artifact_version, modality)
);

CREATE TABLE update_candidates (
    candidate_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    segment_id TEXT NOT NULL REFERENCES segments(segment_id),
    disposition TEXT NOT NULL CHECK(disposition IN ('QUARANTINED', 'ELIGIBLE', 'REJECTED', 'PROMOTED', 'INVALIDATED')),
    quarantined_at_utc TEXT NOT NULL,
    audit_revision INTEGER NOT NULL CHECK(audit_revision >= 1),
    candidate_json TEXT NOT NULL CHECK(json_valid(candidate_json)),
    schema_version TEXT NOT NULL,
    stored_at_utc TEXT NOT NULL
);

CREATE TABLE app_registry (
    app_id INTEGER PRIMARY KEY CHECK(app_id >= 0),
    process_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    category TEXT NOT NULL CHECK(category IN ('PRODUCTIVITY', 'BROWSING', 'DEVELOPMENT', 'CREATIVE', 'GAMING', 'SYSTEM', 'UNKNOWN')),
    schema_version TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE system_metrics (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    metric_kind TEXT NOT NULL CHECK(metric_kind IN ('METRICS', 'HEALTH')),
    metric_json TEXT NOT NULL CHECK(json_valid(metric_json)),
    schema_version TEXT NOT NULL
);

CREATE TABLE audit_outbox (
    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    dispatched_at_utc TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0)
);

CREATE INDEX idx_sessions_user_started ON sessions(user_id, started_at_utc);
CREATE INDEX idx_segments_session_start ON segments(session_id, started_at_capture_us);
CREATE INDEX idx_feature_windows_user_day ON feature_windows(user_id, collection_day);
CREATE INDEX idx_feature_windows_stored ON feature_windows(stored_at_utc);
CREATE INDEX idx_scores_user_stored ON scores(user_id, stored_at_utc);
CREATE INDEX idx_scores_stored ON scores(stored_at_utc);
CREATE INDEX idx_risk_events_user_time ON risk_events(user_id, t_decision_us);
CREATE INDEX idx_alerts_type_time ON alerts(alert_type, occurred_at_utc);
CREATE INDEX idx_models_user_modality ON models(user_id, modality);
CREATE INDEX idx_update_candidates_disposition ON update_candidates(disposition, quarantined_at_utc);
CREATE INDEX idx_system_metrics_time ON system_metrics(observed_at_utc);
CREATE INDEX idx_audit_outbox_pending ON audit_outbox(dispatched_at_utc, outbox_id);


