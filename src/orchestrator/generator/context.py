"""Shared generation state: config, seeded RNG streams, ID counters, tables and the audit log."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from orchestrator import ids
from orchestrator.config import GeneratorConfig
from orchestrator.schema import LOAD_ORDER

Row = dict[str, Any]


class AuditLog:
    """Append-only event log. IDs are assigned once, in chronological order, at finalize()."""

    def __init__(self) -> None:
        self._events: list[Row] = []

    def add(
        self,
        ts: datetime,
        entity_type: str,
        entity_id: str,
        event_type: str,
        actor_role: str,
        actor_id: str | None = None,
        from_state: str | None = None,
        to_state: str | None = None,
        related_case_id: str | None = None,
        reason: str | None = None,
    ) -> str:
        """Record an event; returns a temporary reference that finalize() maps to the real EVT id."""
        ref = f"_tmp:{len(self._events)}"
        self._events.append(
            {
                "_ref": ref,
                "event_timestamp": ts,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "event_type": event_type,
                "actor_id": actor_id,
                "actor_role": actor_role,
                "from_state": from_state,
                "to_state": to_state,
                "related_case_id": related_case_id,
                "reason": reason,
            }
        )
        return ref

    def events(self) -> list[Row]:
        return self._events

    def remove(self, predicate) -> int:
        before = len(self._events)
        self._events = [e for e in self._events if not predicate(e)]
        return before - len(self._events)

    def finalize(self) -> tuple[list[Row], dict[str, str]]:
        """Sort chronologically (stable on insertion order) and assign EVT ids."""
        ordered = sorted(enumerate(self._events), key=lambda p: (p[1]["event_timestamp"], p[0]))
        rows, ref_map = [], {}
        for n, (_, ev) in enumerate(ordered, start=1):
            eid = ids.event_id(n)
            ref_map[ev["_ref"]] = eid
            row = {"event_id": eid, **{k: v for k, v in ev.items() if k != "_ref"}}
            rows.append(row)
        return rows, ref_map


@dataclass
class GenContext:
    cfg: GeneratorConfig
    tables: dict[str, list[Row]] = field(default_factory=lambda: {name: [] for name in LOAD_ORDER})
    audit: AuditLog = field(default_factory=AuditLog)
    counters: dict[str, ids.Counter] = field(default_factory=dict)
    # Generator-internal state (never exported, never used as a label).
    plan: dict[str, Any] = field(default_factory=dict)
    # Labels for injected anomalies. Exported separately; never written into input tables.
    ground_truth: list[Row] = field(default_factory=list)

    def rng(self, stage: str) -> random.Random:
        """Independent, reproducible RNG per stage: changing one stage never shifts another's values."""
        return random.Random(f"{self.cfg.seed}:{stage}")

    def counter(self, name: str) -> ids.Counter:
        return self.counters.setdefault(name, ids.Counter())

    @property
    def as_of(self) -> date:
        return self.cfg.as_of_date


# ---------------------------------------------------------------------------------- helpers
def rand_date(rng: random.Random, lo: date, hi: date) -> date:
    if hi < lo:
        raise ValueError(f"empty date range {lo}..{hi}")
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def next_monday(d: date) -> date:
    return d + timedelta(days=(7 - d.weekday()) % 7)


def at_time(rng: random.Random, d: date, start_hour: int = 8, end_hour: int = 18) -> datetime:
    """A business-hours timestamp on day d."""
    return datetime.combine(d, time(rng.randint(start_hour, end_hour - 1), rng.randint(0, 59)))


def weighted(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def allocate(total: int, weights: dict[str, float]) -> dict[str, int]:
    """Split `total` into integer counts proportional to weights (largest-remainder; deterministic)."""
    wsum = sum(weights.values())
    raw = {k: total * w / wsum for k, w in weights.items()}
    out = {k: int(v) for k, v in raw.items()}
    remainder = total - sum(out.values())
    for k in sorted(raw, key=lambda k: (-(raw[k] - out[k]), k))[:remainder]:
        out[k] += 1
    return out
