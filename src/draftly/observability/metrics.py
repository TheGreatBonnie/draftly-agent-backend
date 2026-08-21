"""Metrics collection (plan §9.3).

Dependency-free counters, gauges, and timing histograms with a
Prometheus-style text exposition. Process-local: adequate for
single-instance deployments and test assertions.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from typing import Any


class Metrics:
    """Thread-safe in-process metrics registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._timings: dict[str, list[float]] = defaultdict(list)

    # --------------------------------------------------------------
    # Recording
    # --------------------------------------------------------------

    def increment(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, seconds: float) -> None:
        with self._lock:
            samples = self._timings[name]
            samples.append(seconds)
            if len(samples) > 10_000:
                del samples[: len(samples) - 10_000]

    class _Timer:
        def __init__(self, registry: Metrics, name: str) -> None:
            self.registry = registry
            self.name = name
            self.start = 0.0

        def __enter__(self) -> Metrics._Timer:
            self.start = time.perf_counter()
            return self

        def __exit__(self, *exc: Any) -> None:
            self.registry.observe(self.name, time.perf_counter() - self.start)

    def timer(self, name: str) -> Metrics._Timer:
        return Metrics._Timer(self, name)

    # --------------------------------------------------------------
    # Reading / exposition
    # --------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "timings": {
                    name: self._summarize(samples)
                    for name, samples in self._timings.items()
                },
            }

    @staticmethod
    def _summarize(samples: list[float]) -> dict[str, float]:
        if not samples:
            return {"count": 0}
        ordered = sorted(samples)
        return {
            "count": len(ordered),
            "sum_ms": round(sum(ordered) * 1000, 3),
            "p50_ms": round(ordered[len(ordered) // 2] * 1000, 3),
            "p99_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))] * 1000, 3),
        }

    def render(self) -> str:
        """Prometheus text exposition."""
        lines: list[str] = []
        with self._lock:
            for name, value in sorted(self._counters.items()):
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name} {value}")
            for name, value in sorted(self._gauges.items()):
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name} {value}")
            for name, summary in sorted(self._timings.items()):
                stats = self._summarize(summary)
                lines.append(f"# TYPE {name}_milliseconds summary")
                for key, value in stats.items():
                    suffix = "" if key == "count" else f"_{key.split('_')[0]}"
                    quantile = (
                        f'{{quantile="{key.split("_")[0]}"}}'
                        if key.endswith("_ms")
                        else ""
                    )
                    display = value if not math.isnan(value) else 0.0
                    lines.append(f"{name}_milliseconds{suffix}{quantile} {display}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
