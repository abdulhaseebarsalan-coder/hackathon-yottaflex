"""Generate the synthetic onboarding/offboarding dataset.

Usage:
    python scripts/generate.py --scale starter
    python scripts/generate.py --scale full
    python scripts/generate.py --scale full --no-anomalies --out output/full_clean   # clean baseline
    python scripts/generate.py --scale full --seed 20261022 --out output/full

Writes <out>/csv/*.csv (input tables) and <out>/ground_truth/anomalies.{csv,json} (labels, kept separate).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.config import load_config  # noqa: E402
from orchestrator.export.csv_writer import write_csv  # noqa: E402
from orchestrator.export.ground_truth_writer import write_ground_truth  # noqa: E402
from orchestrator.generator.pipeline import generate  # noqa: E402
from orchestrator.validation.rules import detect_all, evaluate  # noqa: E402
from orchestrator.validation.structural import run_structural_checks, summarize  # noqa: E402


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
    else:
        for f in ("anomalies.csv", "anomalies.json"):
            (gt_dir / f).unlink(missing_ok=True)

    print("\nRow counts:")
    for name, rows in ctx.tables.items():
        print(f"  {name:<24}{len(rows):>8}")

    summary = summarize(run_structural_checks(ctx.tables))
    print(f"\nStructural checks: {summary['checks_passed']}/{summary['checks_run']} passed")
    for f in summary["failures"]:
        print(f"  FAIL {f['check']} {f['table']}.{f['column']}: {f['violations']} e.g. {f['examples']}")

    findings = detect_all(ctx.tables, cfg.as_of_date)
    print(f"\nInjected anomalies: {len(ctx.ground_truth)} {dict(Counter(r['issue_type'] for r in ctx.ground_truth))}")
    if inject and ctx.plan["anomaly_summary"]["shortfall"]:
        print(f"  shortfall vs requested {ctx.plan['anomaly_summary']['requested_per_type']}/type: "
              f"{ctx.plan['anomaly_summary']['shortfall']} (not enough eligible records; none fabricated)")
    print(f"Rule detector findings: {len(findings)}")
    report = evaluate(findings, ctx.ground_truth)
    for it, r in report.items():
        print(f"  {it:<36} expected={r['expected']:>3} detected={r['detected']:>3} "
              f"precision={r['precision']:.2f} recall={r['recall']:.2f}")
    print(f"\nWrote {out / 'csv'}" + (f" and {gt_dir}" if inject else ""))
    perfect = all(r["precision"] == 1.0 and r["recall"] == 1.0 for r in report.values())
    return 0 if not summary["failures"] and perfect else 1


if __name__ == "__main__":
    raise SystemExit(main())
