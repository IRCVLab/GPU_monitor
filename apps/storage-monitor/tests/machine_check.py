"""Shared checks for the I19 transition-table and random-event tests."""
from __future__ import annotations

import random
from typing import Any, Callable, Iterable, Mapping, Tuple

SEQUENCES = 10_000
STEPS = 16


def assert_table_total(transitions: Mapping[Tuple[str, str], Any], states: Iterable[str], kinds: Iterable[str]) -> None:
    """Every (state, event kind) pair has exactly one entry, and nothing else does."""
    expected = {(state, kind) for state in states for kind in kinds}
    missing = sorted(expected - set(transitions))
    extra = sorted(set(transitions) - expected)
    assert not missing and not extra, f"missing pairs {missing}, unknown pairs {extra}"


def run_random_events(
    initial: Callable[[random.Random], Any],
    event: Callable[[random.Random, Any, int], Any],
    step: Callable[[Any, Any], Tuple[Any, tuple]],
    check: Callable[[Any, Any, Any, tuple], None],
    *, sequences: int = SEQUENCES, steps: int = STEPS,
) -> None:
    """Run seeded event sequences and check the "never" list after every step.

    ``check(before, event, after, effects)`` raises AssertionError on a forbidden state.
    The failing seed and step are in the message, so a failure replays exactly.
    """
    for seed in range(sequences):
        rng = random.Random(seed)
        state = initial(rng)
        for index in range(steps):
            item = event(rng, state, index)
            after, effects = step(state, item)
            try:
                check(state, item, after, effects)
            except AssertionError as exc:
                raise AssertionError(f"seed {seed} step {index} event {item!r}: {exc}") from None
            state = after
