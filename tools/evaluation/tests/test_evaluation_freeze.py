from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.evaluation import (
    EvaluationFreezeError,
    create_evaluation_freeze,
    verify_evaluation_freeze,
)


def test_evaluation_freeze_detects_config_or_corpus_drift(tmp_path: Path) -> None:
    config_directory = tmp_path / "config"
    config_directory.mkdir()
    config = config_directory / "risk.yaml"
    config.write_text("version: synthetic-v1\nthreshold: 0.5\n", encoding="utf-8")
    dataset = tmp_path / "manifest.json"
    dataset.write_text('{"synthetic":true}\n', encoding="utf-8")
    freeze = tmp_path / "evaluation-freeze.json"
    document = create_evaluation_freeze(
        freeze,
        config_paths=[config],
        dataset_manifest=dataset,
        code_revision="synthetic-revision",
        frozen_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert len(document["freeze_checksum"]) == 64
    verify_evaluation_freeze(
        freeze,
        config_directory=config_directory,
        dataset_manifest=dataset,
        code_revision="synthetic-revision",
    )
    config.chmod(0o600)
    config.write_text("version: synthetic-v1\nthreshold: 0.6\n", encoding="utf-8")
    with pytest.raises(EvaluationFreezeError, match="configuration changed"):
        verify_evaluation_freeze(
            freeze,
            config_directory=config_directory,
            dataset_manifest=dataset,
            code_revision="synthetic-revision",
        )


def test_evaluation_freeze_is_write_once(tmp_path: Path) -> None:
    config = tmp_path / "one.yaml"
    config.write_text("value: 1\n", encoding="utf-8")
    dataset = tmp_path / "manifest.json"
    dataset.write_text("{}\n", encoding="utf-8")
    destination = tmp_path / "freeze.json"
    create_evaluation_freeze(
        destination,
        config_paths=[config],
        dataset_manifest=dataset,
        code_revision="synthetic-revision",
    )
    with pytest.raises(EvaluationFreezeError, match="already exists"):
        create_evaluation_freeze(
            destination,
            config_paths=[config],
            dataset_manifest=dataset,
            code_revision="synthetic-revision",
        )
