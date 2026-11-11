"""Ground-truth export: kept in its own folder, never merged into the input tables."""

from __future__ import annotations

import csv
import json
from pathlib import Path

COLUMNS = ["anomaly_id", "issue_type", "sub_type", "severity", "rule_id", "entity_type", "entity_id",
           "detection_key", "employee_id", "case_id", "related_ids", "field_changes", "expected_decision",
           "description"]


def write_ground_truth(records: list[dict], out_dir: Path, meta: dict) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path, json_path = out_dir / "anomalies.csv", out_dir / "anomalies.json"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(COLUMNS)
        for r in records:
            w.writerow([";".join(r[c]) if isinstance(r[c], list) else ("" if r[c] is None else r[c]) for c in COLUMNS])
    payload = {**meta, "anomaly_count": len(records), "anomalies": [{c: r[c] for c in COLUMNS} for r in records]}
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return [csv_path, json_path]
