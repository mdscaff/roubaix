from collections import Counter
from dataclasses import dataclass, field
from time import perf_counter


@dataclass
class InMemoryMetrics:
    counters: Counter = field(default_factory=Counter)

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] += value


metrics = InMemoryMetrics()


class Timer:
    """Records elapsed milliseconds for a block.

    Duration is exposed via ``elapsed_ms`` and accumulated into a *count* and a
    *total* under fixed metric names. It deliberately does not encode the
    duration into the metric name: ``f"{name}:{duration_ms}"`` mints a new
    counter key for every distinct millisecond value, which is unbounded
    cardinality and will exhaust any real metrics backend.
    """

    def __init__(self, metric_name: str):
        self.metric_name = metric_name
        self.start = 0.0
        self.elapsed_ms = 0

    def __enter__(self) -> "Timer":
        self.start = perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed_ms = int((perf_counter() - self.start) * 1000)
        metrics.increment(f"{self.metric_name}:count")
        metrics.increment(f"{self.metric_name}:total_ms", self.elapsed_ms)
