from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter
from typing import Iterator


@dataclass
class InMemoryMetrics:
    counters: Counter = field(default_factory=Counter)

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] += value


metrics = InMemoryMetrics()


class Timer:
    def __init__(self, metric_name: str):
        self.metric_name = metric_name
        self.start = 0.0

    def __enter__(self) -> "Timer":
        self.start = perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        duration_ms = int((perf_counter() - self.start) * 1000)
        metrics.increment(f"{self.metric_name}:{duration_ms}")
