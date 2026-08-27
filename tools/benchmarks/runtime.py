"""Sample an external process and report bounded runtime-cost percentiles."""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any

import yaml


@dataclass(frozen=True)
class BenchmarkConfig:
    config_version: str
    sample_interval_seconds: float
    duration_seconds: float
    warmup_seconds: float


def load_benchmark_config(path: Path) -> BenchmarkConfig:
    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        values = document["benchmark"]
        config = BenchmarkConfig(
            config_version=document["config_version"],
            sample_interval_seconds=float(values["sample_interval_seconds"]),
            duration_seconds=float(values["duration_seconds"]),
            warmup_seconds=float(values["warmup_seconds"]),
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"benchmark config rejected: {exc}") from exc
    if (
        min(
            config.sample_interval_seconds,
            config.duration_seconds,
            config.warmup_seconds,
        )
        <= 0
    ):
        raise ValueError("benchmark durations must be positive")
    if config.warmup_seconds >= config.duration_seconds:
        raise ValueError("benchmark warmup must be shorter than total duration")
    return config


@dataclass(frozen=True)
class ProcessSample:
    elapsed_seconds: float
    cpu_percent: float
    memory_bytes: int


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile without samples")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _linux_snapshot(pid: int) -> tuple[float, int]:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    ticks = os.sysconf("SC_CLK_TCK")
    cpu_seconds = (int(stat[13]) + int(stat[14])) / ticks
    memory_bytes = int(stat[23]) * os.sysconf("SC_PAGE_SIZE")
    return cpu_seconds, memory_bytes


def _windows_snapshot(pid: int) -> tuple[float, int]:
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("page_fault_count", wintypes.DWORD),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise RuntimeError("Windows process APIs are unavailable")
    kernel32 = loader("kernel32", use_last_error=True)
    psapi = loader("psapi", use_last_error=True)
    process_query_limited_information = 0x1000
    process_vm_read = 0x0010
    handle = kernel32.OpenProcess(process_query_limited_information | process_vm_read, False, pid)
    if not handle:
        raise RuntimeError("benchmark process could not be opened")
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise RuntimeError("benchmark process CPU time is unavailable")
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise RuntimeError("benchmark process memory is unavailable")

        def filetime(value: wintypes.FILETIME) -> int:
            return (value.dwHighDateTime << 32) | value.dwLowDateTime

        cpu_seconds = (filetime(kernel) + filetime(user)) / 10_000_000
        return cpu_seconds, int(counters.working_set_size)
    finally:
        kernel32.CloseHandle(handle)


def _snapshot(pid: int) -> tuple[float, int]:
    if platform.system() == "Linux":
        return _linux_snapshot(pid)
    if platform.system() == "Windows":
        return _windows_snapshot(pid)
    if pid != os.getpid():
        raise RuntimeError("external process sampling is implemented for Windows and Linux only")
    import resource

    usage = resource.getrusage(resource.RUSAGE_SELF)
    scale = 1 if platform.system() == "Darwin" else 1024
    return time.process_time(), int(usage.ru_maxrss * scale)


class ProcessBenchmark:
    def __init__(self, pid: int, config: BenchmarkConfig) -> None:
        if pid <= 0:
            raise ValueError("pid must be positive")
        self.pid = pid
        self.config = config

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        prior_wall = started
        prior_cpu, _ = _snapshot(self.pid)
        samples: list[ProcessSample] = []
        while True:
            time.sleep(self.config.sample_interval_seconds)
            now = time.monotonic()
            cpu, memory = _snapshot(self.pid)
            elapsed = now - started
            wall_delta = now - prior_wall
            cpu_percent = max(0.0, (cpu - prior_cpu) / wall_delta * 100)
            if elapsed >= self.config.warmup_seconds:
                samples.append(ProcessSample(elapsed, cpu_percent, memory))
            prior_wall, prior_cpu = now, cpu
            if elapsed >= self.config.duration_seconds:
                break
        cpu_values = [sample.cpu_percent for sample in samples]
        memory_values = [float(sample.memory_bytes) for sample in samples]
        return {
            "report_schema": "continuous-auth-runtime-benchmark-v1",
            "config_version": self.config.config_version,
            "hardware": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "processor": platform.processor(),
                "logical_cpu_count": os.cpu_count(),
            },
            "sample_count": len(samples),
            "cpu_percent": {
                "mean": mean(cpu_values),
                "p50": _percentile(cpu_values, 0.50),
                "p95": _percentile(cpu_values, 0.95),
                "p99": _percentile(cpu_values, 0.99),
            },
            "memory_bytes": {
                "mean": mean(memory_values),
                "p50": _percentile(memory_values, 0.50),
                "p95": _percentile(memory_values, 0.95),
                "p99": _percentile(memory_values, 0.99),
            },
            "samples": [asdict(sample) for sample in samples],
        }


def write_benchmark_report(destination: Path, report: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
