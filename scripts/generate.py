"""Generate the synthetic onboarding/offboarding dataset.

Usage:
    python scripts/generate.py --scale starter
    python scripts/generate.py --scale full
    python scripts/generate.py --scale full --seed 20261022 --out output/full
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.config import load_config  # noqa: E402
from orchestrator.export.csv_writer import write_csv  # noqa: E402
from orchestrator.generator.pipeline import generate  # noqa: E402
from orchestrator.validation.structural import run_structural_checks, summarize  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", default="starter", choices=["starter", "full"])
    ap.add_argument("--seed", type=int, default=None, help="override the fixed seed in config/settings.json")
    ap.add_argument("--employees", type=int, default=None, help="override employee count (other volumes scale)")
    ap.add_argument("--out", type=Path, default=None, help="output directory (default: output/<scale>)")
    args = ap.parse_args()

    cfg = load_config(args.scale, seed=args.seed, employees=args.employees)
    out = args.out or ROOT / "output" / args.scale
    print(f"Generating scale={cfg.scale} seed={cfg.seed} employees={cfg.employees} as_of={cfg.as_of_date}")

    ctx = generate(cfg)
    write_csv(ctx.tables, out / "csv")

    print("\nRow counts:")
    for name, rows in ctx.tables.items():
        print(f"  {name:<24}{len(rows):>8}")

    summary = summarize(run_structural_checks(ctx.tables))
    print(f"\nStructural checks: {summary['checks_passed']}/{summary['checks_run']} passed")
    for f in summary["failures"]:
        print(f"  FAIL {f['check']} {f['table']}.{f['column']}: {f['violations']} e.g. {f['examples']}")
    print(f"\nWrote CSV files to {out / 'csv'}")
    return 0 if not summary["failures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
