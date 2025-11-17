from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

ResourcePayload = Dict[str, Any]


@dataclass
class CollectorResult:
    """Holds a single collector's output and optional error."""

    payload: Optional[ResourcePayload] = None
    error: Optional[str] = None


class ResourceCollector(ABC):
    """Abstract base for resource collectors (GPU/CPU/Storage...)."""

    name: str

    @abstractmethod
    def collect(self, ssh_client) -> ResourcePayload:
        """Return the resource payload."""


@dataclass
class CollectorRegistry:
    """Keeps track of resource collectors and gathers their output."""

    collectors: Dict[str, ResourceCollector] = field(default_factory=dict)

    def register(self, collector: ResourceCollector) -> None:
        if collector.name in self.collectors:
            raise ValueError(f"Collector '{collector.name}' already registered")
        self.collectors[collector.name] = collector

    def collect_all(self, ssh_client) -> Dict[str, CollectorResult]:
        results: Dict[str, CollectorResult] = {}
        for name, collector in self.collectors.items():
            try:
                payload = collector.collect(ssh_client)
                results[name] = CollectorResult(payload=payload)
            except Exception as exc:  # pragma: no cover - defensive
                results[name] = CollectorResult(error=str(exc))
        return results

