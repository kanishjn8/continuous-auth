from __future__ import annotations

import os
from pathlib import Path

from tools.benchmarks import BenchmarkConfig, ProcessBenchmark, load_benchmark_config


def test_benchmark_config_loads() -> None:
    config = load_benchmark_config(Path("config/benchmark.development.yaml"))
    assert config.warmup_seconds < config.duration_seconds


def test_current_process_report_has_runtime_percentiles() -> None:
    report = ProcessBenchmark(
        os.getpid(),
        BenchmarkConfig(
            config_version="synthetic-fast-test",
            sample_interval_seconds=0.005,
            duration_seconds=0.02,
            warmup_seconds=0.005,
        ),
    ).run()
    assert report["sample_count"] >= 1
    assert set(report["cpu_percent"]) == {"mean", "p50", "p95", "p99"}
    assert set(report["memory_bytes"]) == {"mean", "p50", "p95", "p99"}
