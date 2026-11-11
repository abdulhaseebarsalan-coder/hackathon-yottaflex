"""Load a published dataset back from disk (typed by the schema) so evaluation runs on the actual files."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from orchestrator.schema import TABLES

_PARSE = {
    "str": str,
    "int": int,
    "decimal": float,
    "bool": lambda v: {"true": True, "false": False}[v],
    "date": date.fromisoformat,
    "datetime": lambda v: datetime.strptime(v, "%Y-%m-%d %H:%M:%S"),
}


def load_tables(csv_dir: Path) -> dict[str, list[dict[str, Any]]]:
    tables = {}
    for t in TABLES:
        with (Path(csv_dir) / f"{t.name}.csv").open(encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            if header != t.columns:
                raise ValueError(f"{t.name}.csv header does not match schema")
            tables[t.name] = [{f.name: (None if v == "" else _PARSE[f.type](v)) for f, v in zip(t.fields, row)}
                              for row in reader]
    return tables


def load_ground_truth(gt_dir: Path) -> tuple[list[dict], dict]:
    """Returns (records, metadata). Missing folder -> clean dataset: ([], {})."""
    path = Path(gt_dir) / "anomalies.json"
    if not path.exists():
        return [], {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.pop("anomalies")
    return records, payload


def fingerprint(files: list[Path]) -> str:
    """Content hash of a file set (file names + bytes; independent of the folder location)."""
    h = hashlib.sha256()
    for p in sorted(files, key=lambda p: p.name):
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()
