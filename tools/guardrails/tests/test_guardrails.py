from __future__ import annotations

from tools.guardrails.check import (
    check_app_specific_model,
    check_collector_privacy,
    check_data_tracking,
    check_drill_exclusion,
    check_hardcoded_tunables,
    check_model_loader,
    check_monitoring_invariants,
    check_prohibited_dependency,
    check_schema_identifiers,
    check_shared_feature_consumer,
    check_sql_schema_identifiers,
    check_temporal_identity_features,
    check_training_gate,
    scan_repository,
)


def test_g01_rejects_content_bearing_schema_field() -> None:
    document = {"type": "object", "properties": {"typed_text": {"type": "string"}}}
    assert check_schema_identifiers("bad.schema.json", document)


def test_g01_rejects_content_bearing_sql_column() -> None:
    sql = "CREATE TABLE unsafe (id TEXT PRIMARY KEY, typed_text TEXT NOT NULL);"
    assert check_sql_schema_identifiers("0001_unsafe.sql", sql)


def test_g02_rejects_collector_content_api() -> None:
    assert check_collector_privacy("collector.cpp", "auto title = GetWindowText(handle);")


def test_g03_rejects_screen_or_accessibility_dependency() -> None:
    assert check_prohibited_dependency("pyproject.toml", 'dependencies = ["pytesseract"]')


def test_g04_rejects_absolute_temporal_identity_feature() -> None:
    assert check_temporal_identity_features("features.py", "weekday = captured_at.weekday()")


def test_g05_rejects_application_specific_model_selection() -> None:
    assert check_app_specific_model("models.py", "if app_id == 4: model = gaming_model")


def test_g06_rejects_monitoring_disable_and_zero_confidence() -> None:
    assert check_monitoring_invariants("risk.py", "monitoring_enabled = False")
    assert check_monitoring_invariants("risk.py", "confidence_floor = 0.0")


def test_g07_rejects_training_without_promotion_gate() -> None:
    assert check_training_gate("train.py", "candidate_model.fit(windows)")
    assert not check_training_gate(
        "train.py", "require_promotion_gate(candidate)\ncandidate_model.fit(windows)"
    )


def test_g08_rejects_parallel_feature_implementation() -> None:
    assert check_shared_feature_consumer("adapter.py", "def compute_mouse_features(): pass")
    assert not check_shared_feature_consumer("adapter.py", "from ml.features import extract_window")


def test_g09_rejects_numeric_tunable_outside_config() -> None:
    assert check_hardcoded_tunables("risk.py", "medium_threshold = 0.7")


def test_g10_rejects_tracked_participant_data() -> None:
    assert check_data_tracking(["data/raw/participant-1.json"])
    assert not check_data_tracking(["protocol/examples/valid/heartbeat.json"])


def test_g11_rejects_model_load_without_schema_check() -> None:
    assert check_model_loader("loader.py", "model = joblib.load(path)")
    assert not check_model_loader(
        "loader.py",
        "model = joblib.load(path)\nassert_feature_schema_compatible(model.metadata)",
    )


def test_repository_satisfies_all_guardrails() -> None:
    assert scan_repository() == []


def test_training_call_may_cite_the_enrollment_boundary() -> None:
    from tools.guardrails.check import check_training_gate

    findings = check_training_gate(
        "train.py", "require_enrollment_admission(user, windows, admission)\nmodel.fit(X)"
    )
    assert findings == []


def test_training_call_with_no_boundary_is_still_flagged() -> None:
    from tools.guardrails.check import check_training_gate

    findings = check_training_gate("train.py", "model.fit(X)")
    assert len(findings) == 1
    assert findings[0].code == "G07_PROMOTION_GATE"


def test_common_must_reference_both_boundaries() -> None:
    """Neither boundary may be deleted without the guardrail failing."""

    from tools.guardrails.check import check_admission_boundaries_present

    complete = "require_promotion_gate\nrequire_enrollment_admission"
    assert check_admission_boundaries_present("ml/training/common.py", complete) == []

    missing = "require_promotion_gate only"
    findings = check_admission_boundaries_present("ml/training/common.py", missing)
    assert len(findings) == 1
    assert findings[0].code == "G07_ADMISSION_BOUNDARY"


def test_other_files_are_not_required_to_reference_both() -> None:
    from tools.guardrails.check import check_admission_boundaries_present

    assert check_admission_boundaries_present("ml/training/other.py", "nothing") == []


def test_g12_rejects_a_corpus_loader_that_drops_the_drill_filter() -> None:
    findings = check_drill_exclusion(
        "tools/collection/repository.py",
        "SELECT window_id FROM feature_windows ORDER BY user_id",
    )
    assert [finding.rule for finding in findings] == ["G12_DRILL_EXCLUSION"]


def test_g12_accepts_a_corpus_loader_that_keeps_the_drill_filter() -> None:
    assert (
        check_drill_exclusion(
            "tools/collection/corpus.py",
            "SELECT window_id FROM feature_windows "
            "WHERE session_id NOT IN (SELECT session_id FROM drill_sessions)",
        )
        == []
    )


def test_g12_ignores_files_that_are_not_corpus_loaders() -> None:
    """Retention and provenance lookups must keep seeing drill windows."""
    assert (
        check_drill_exclusion(
            "backend/app/storage/retention.py",
            "DELETE FROM feature_windows WHERE stored_at_utc < ?",
        )
        == []
    )
