"""CSV export: one file per table, columns in schema order, stable formatting (LF line endings)."""

from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Any

from orchestrator.schema import TABLES


def format_value(value: Any, ftype: str) -> str:
    if value is None:
        return ""
    if ftype == "bool":
        return "true" if value else "false"
    if ftype == "decimal":
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def write_csv(tables: dict[str, list[dict]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for t in TABLES:
        path = out_dir / f"{t.name}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(t.columns)
            for row in tables.get(t.name, []):
                w.writerow([format_value(row.get(f.name), f.type) for f in t.fields])
        written.append(path)
    return written
