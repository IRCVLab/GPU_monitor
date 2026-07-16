"""Assess whether collected GPU rows represent the full expected inventory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class GpuInventoryHealth:
    visible_count: int
    expected_count: int
    pci_count: int | None
    missing_indices: list[int]
    state: str

    def to_dict(self) -> dict:
        return {
            "visible_count": self.visible_count,
            "expected_count": self.expected_count,
            "pci_count": self.pci_count,
            "missing_indices": self.missing_indices,
            "state": self.state,
        }


def _normalize_indices(indices: Iterable[int]) -> set[int]:
    return {int(index) for index in indices}


def assess_gpu_inventory(
    *,
    visible_indices: Iterable[int],
    pci_count: int | None,
    historical_indices: Iterable[int] = (),
    mismatch_count: int = 1,
) -> GpuInventoryHealth:
    """Build a GPU inventory health snapshot without inventing missing indices.

    Historical and currently visible indices are known identities. A PCI count can
    raise the expected count, but it cannot identify which index is missing.
    """
    visible = _normalize_indices(visible_indices)
    historical = _normalize_indices(historical_indices)
    known_expected = historical | visible
    normalized_pci_count = pci_count if pci_count is not None and pci_count >= 0 else None
    expected_count = max(
        len(known_expected),
        normalized_pci_count if normalized_pci_count is not None else 0,
    )
    visible_count = len(visible)
    missing_indices = sorted(index for index in known_expected if index not in visible)

    if visible_count >= expected_count and not missing_indices:
        state = "healthy"
    elif mismatch_count >= 2:
        state = "missing"
    else:
        state = "suspect"

    return GpuInventoryHealth(
        visible_count=visible_count,
        expected_count=expected_count,
        pci_count=normalized_pci_count,
        missing_indices=missing_indices,
        state=state,
    )


class GpuInventoryTracker:
    """Stateful two-sample debounce for partial GPU visibility."""

    def __init__(self, historical_indices: Iterable[int] = ()) -> None:
        self._expected_indices = _normalize_indices(historical_indices)
        self._mismatch_count = 0

    @property
    def expected_indices(self) -> set[int]:
        return set(self._expected_indices)

    def add_historical_indices(self, indices: Iterable[int]) -> None:
        self._expected_indices.update(_normalize_indices(indices))

    def assess(self, *, visible_indices: Iterable[int], pci_count: int | None) -> GpuInventoryHealth:
        visible = _normalize_indices(visible_indices)
        candidate_expected = self._expected_indices | visible
        normalized_pci_count = pci_count if pci_count is not None and pci_count >= 0 else None
        expected_count = max(
            len(candidate_expected),
            normalized_pci_count if normalized_pci_count is not None else 0,
        )
        missing_known = any(index not in visible for index in candidate_expected)
        mismatch = len(visible) < expected_count or missing_known

        if mismatch:
            self._mismatch_count += 1
        else:
            self._mismatch_count = 0
            self._expected_indices = candidate_expected

        health = assess_gpu_inventory(
            visible_indices=visible,
            pci_count=normalized_pci_count,
            historical_indices=candidate_expected,
            mismatch_count=self._mismatch_count,
        )

        if health.state == "healthy":
            self._expected_indices = candidate_expected
        else:
            # Learn only the GPUs we can actually see; do not infer missing
            # identities from the PCI count.
            self._expected_indices.update(visible)

        return health
