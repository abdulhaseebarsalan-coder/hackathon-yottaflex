"""Publish one dataset folder: CSV, JSON, SQL seed, ground truth, evaluation reports and checksum manifest.

Used by scripts/generate.py and scripts/reproduce.py so both produce exactly the same files.
Every output is a pure function of (code version, seed, scale, employees, inject).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from orchestrator.config import GeneratorConfig
from orchestrator.export.csv_writer import write_csv
from orchestrator.export.ground_truth_writer import write_ground_truth
from orchestrator.export.json_writer import write_json
from orchestrator.export.manifest import PUBLISHED_DIRS, build_manifest, write_manifest
from orchestrator.export.sql_writer import build_seed_sql
from orchestrator.generator.pipeline import generate
from orchestrator.metrics.evaluation import evaluate_and_write


def publish(cfg: GeneratorConfig, out: Path, inject: bool = True) -> dict[str, Any]:
    out = Path(out)
    for d in PUBLISHED_DIRS:  # start from a clean slate so no stale file can enter the manifest
        if (out / d).is_dir():
            shutil.rmtree(out / d)
    ctx = generate(cfg, inject=inject)
    write_csv(ctx.tables, out / "csv")
    write_json(ctx.tables, out / "json")
    (out / "sql").mkdir(parents=True, exist_ok=True)
    label = f"scale={cfg.scale} seed={cfg.seed} employees={cfg.employees} anomalies={'on' if inject else 'off'}"
    (out / "sql" / "seed.sql").write_text(build_seed_sql(ctx.tables, label), encoding="utf-8", newline="\n")
    if inject:
        write_ground_truth(ctx.ground_truth, out / "ground_truth",
                           {"seed": cfg.seed, "scale": cfg.scale, "as_of_date": cfg.as_of_date.isoformat(),
                            **ctx.plan["anomaly_summary"]})
    report = evaluate_and_write(out, cfg)            # computed from the files just written
    row_counts = {name: len(rows) for name, rows in ctx.tables.items()}
    manifest = build_manifest(out, cfg, inject, report["verdict"], row_counts)
    write_manifest(manifest, out)
    return {"ctx": ctx, "report": report, "manifest": manifest}
