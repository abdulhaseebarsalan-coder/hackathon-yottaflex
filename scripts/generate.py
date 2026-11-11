"""Generate the synthetic onboarding/offboarding dataset, its exports, evaluation report and manifest.

Usage:
    python scripts/generate.py --scale starter
    python scripts/generate.py --scale full
    python scripts/generate.py --scale full --no-anomalies --out output/full_clean   # clean baseline
    python scripts/generate.py --scale full --seed 20261022 --out output/full

Writes into <out>/:
    csv/<table>.csv                input tables          json/<table>.json      same data as JSON
    sql/seed.sql                   MySQL INSERTs         ground_truth/          anomaly labels (separate)
    reports/evaluation_report.*    evaluation            manifest.json          SHA-256 of every file
Exit code 0 = evaluation verdict PASS.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.config import load_config  # noqa: E402
from orchestrator.metrics.evaluation import summary_lines  # noqa: E402
from orchestrator.publish import publish  # noqa: E402


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

    result = publish(cfg, out, inject=inject)
    report, manifest = result["report"], result["manifest"]

    print("\nRow counts:")
    for name, n in manifest["row_counts"].items():
        print(f"  {name:<24}{n:>8}")
    s = report["validation"]["published_dataset"]["structural"]
    print(f"\nStructural checks: {s['checks_passed']}/{s['checks_run']} passed")
    print("\n".join(summary_lines(report, out)))
    print(f"\nWrote {len(manifest['files'])} files + manifest.json to {out}")
    return 0 if report["verdict"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
