"""JSON export: one file per table, schema column order, native JSON types where possible.

Layout (deterministic; one record per line so diffs and checksums stay stable):
    {"table": "<name>", "primary_key": "<pk>", "row_count": N, "columns": [...], "rows": [
    {...},
    {...}
    ]}
Type mapping: str -> string, int -> number, decimal -> number (2 dp), bool -> true/false,
date -> "YYYY-MM-DD", datetime -> "YYYY-MM-DD HH:MM:SS", null -> null.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from orchestrator.schema import TABLES


def json_value(value: Any, ftype: str) -> Any:
    if value is None:
        return None
    if ftype == "decimal":
        return round(float(value), 2)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


def write_json(tables: dict[str, list[dict]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for t in TABLES:
        rows = tables.get(t.name, [])
        header = json.dumps({"table": t.name, "primary_key": t.pk, "row_count": len(rows), "columns": t.columns})
        body = ",\n".join(
            json.dumps({f.name: json_value(r.get(f.name), f.type) for f in t.fields}, ensure_ascii=False) for r in rows
        )
        path = out_dir / f"{t.name}.json"
        path.write_text(header[:-1] + ', "rows": [\n' + body + ("\n" if body else "") + "]}\n",
                        encoding="utf-8", newline="\n")
        written.append(path)
    return written
