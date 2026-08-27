"""Command-line entry point for a bounded external-process benchmark."""

from __future__ import annotations

import argparse
from pathlib import Path

from .runtime import ProcessBenchmark, load_benchmark_config, write_benchmark_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--config", type=Path, default=Path("config/benchmark.development.yaml"))
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    report = ProcessBenchmark(
        arguments.pid,
        load_benchmark_config(arguments.config),
    ).run()
    write_benchmark_report(arguments.output, report)
    print("Runtime benchmark completed.")
    return 0
