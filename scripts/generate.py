"""Generate the synthetic onboarding/offboarding dataset and its evaluation report.

Usage:
    python scripts/generate.py --scale starter
    python scripts/generate.py --scale full
    python scripts/generate.py --scale full --no-anomalies --out output/full_clean   # clean baseline
    python scripts/generate.py --scale full --seed 20261022 --out output/full

Writes <out>/csv/*.csv (input tables), <out>/ground_truth/anomalies.{csv,json} (labels, kept separate)
and <out>/reports/evaluation_report.{md,json} (evaluation computed from the written files).
Exit code 0 = evaluation verdict PASS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.config import load_config  # noqa: E402
from orchestrator.export.csv_writer import write_csv  # noqa: E402
from orchestrator.export.ground_truth_writer import write_ground_truth  # noqa: E402
from orchestrator.generator.pipeline import generate  # noqa: E402
from orchestrator.metrics.evaluation import evaluate_and_write, summary_lines  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", default="starter", choices=["starter", "full"])
    ap.add_argument("--seed", type=int, default=None, help="override the fixed seed in config/settings.json")
    ap.add_argument("--employees", type=int, default=None, help="override employee count (other volumes scale)")
    ap.add_argument("--out", type=Path, default=None, help="output directory (default: output/<scale>)")
    ap.add_argument("--no-anomalies", action="store_true", help="write the clean baseline (no injected anomalies)")
    args = ap.parse_args()

    cfg = load_config(args.scale, seed=args.seed, employees=args.employees)
    out = args.out or ROOT / "output" / args.scale
    inject = not args.no_anomalies
    print(f"Generating scale={cfg.scale} seed={cfg.seed} employees={cfg.employees} as_of={cfg.as_of_date} "
          f"anomalies={'on' if inject else 'off'}")

    ctx = generate(cfg, inject=inject)
    write_csv(ctx.tables, out / "csv")
    gt_dir = out / "ground_truth"
    if inject:
        write_ground_truth(ctx.ground_truth, gt_dir, {"seed": cfg.seed, "scale": cfg.scale,
                                                      "as_of_date": cfg.as_of_date.isoformat(),
                                                      **ctx.plan["anomaly_summary"]})
    else:  # a clean dataset must not carry labels from an earlier run
        for f in ("anomalies.csv", "anomalies.json"):
            (gt_dir / f).unlink(missing_ok=True)

    print("\nRow counts:")
    for name, rows in ctx.tables.items():
        print(f"  {name:<24}{len(rows):>8}")

    # Evaluate what was actually written to disk (CSV + ground truth) against the clean baseline.
    report = evaluate_and_write(out, cfg)
    s = report["validation"]["published_dataset"]["structural"]
    print(f"\nStructural checks: {s['checks_passed']}/{s['checks_run']} passed")
    print("\n".join(summary_lines(report, out)))
    print(f"\nWrote {out / 'csv'}" + (f", {gt_dir}" if inject else "") + f" and {out / 'reports'}")
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
