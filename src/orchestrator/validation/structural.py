"""Schema-driven structural checks: PK uniqueness, required fields, types, enums, ID patterns,
unique fields / combinations and foreign-key integrity.

Every check is generated from orchestrator.schema, so adding a field to the schema automatically
adds it to validation. A clean dataset must pass all of these, including the injected anomalies
(anomalies break business rules, never structural integrity).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from orchestrator.ids import COMPILED
from orchestrator.schema import TABLES, foreign_keys

Row = dict[str, Any]

_TYPE_CHECK = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "decimal": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "date": lambda v: isinstance(v, date) and not isinstance(v, datetime),
    "datetime": lambda v: isinstance(v, datetime),
}


@dataclass
class CheckResult:
    check: str
    table: str
    column: str
    violations: int
    examples: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.violations == 0


def _result(check: str, table: str, column: str, bad: list[str]) -> CheckResult:
    return CheckResult(check, table, column, len(bad), bad[:5])


def run_structural_checks(tables: dict[str, list[Row]]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for t in TABLES:
        rows = tables.get(t.name, [])
        pk_counts = Counter(r.get(t.pk) for r in rows)
        results.append(_result("pk_unique", t.name, t.pk, [str(k) for k, c in pk_counts.items() if c > 1 or k is None]))

        for f in t.fields:
            vals = [(r.get(t.pk), r.get(f.name)) for r in rows]
            if f.required:
                results.append(_result("required", t.name, f.name, [str(pk) for pk, v in vals if v is None]))
            results.append(_result("type", t.name, f.name,
                                   [f"{pk}={v!r}" for pk, v in vals if v is not None and not _TYPE_CHECK[f.type](v)]))
            if f.enum:
                results.append(_result("enum", t.name, f.name,
                                       [f"{pk}={v}" for pk, v in vals if v is not None and v not in f.enum]))
            if f.pattern:
                rx = COMPILED[f.pattern]
                results.append(_result("id_pattern", t.name, f.name,
                                       [f"{v}" for _, v in vals if v is not None and not rx.match(str(v))]))
            if f.unique and f.name != t.pk:
                c = Counter(v for _, v in vals if v is not None)
                results.append(_result("unique", t.name, f.name, [str(k) for k, n in c.items() if n > 1]))

        for combo in t.unique_together:
            c = Counter(tuple(r.get(col) for col in combo) for r in rows)
            results.append(_result("unique_together", t.name, "+".join(combo),
                                   [str(k) for k, n in c.items() if n > 1]))

    pk_sets = {t.name: {r[t.pk] for r in tables.get(t.name, [])} for t in TABLES}
    for child, col, parent, _pcol in foreign_keys():
        parent_keys = pk_sets[parent]
        bad = [f"{r.get(col)}" for r in tables.get(child, []) if r.get(col) is not None and r.get(col) not in parent_keys]
        results.append(_result("fk_integrity", child, f"{col} -> {parent}", bad))
    return results


def summarize(results: list[CheckResult]) -> dict[str, Any]:
    failed = [r for r in results if not r.passed]
    return {
        "checks_run": len(results),
        "checks_passed": len(results) - len(failed),
        "checks_failed": len(failed),
        "failures": [
            {"check": r.check, "table": r.table, "column": r.column, "violations": r.violations, "examples": r.examples}
            for r in failed
        ],
    }
