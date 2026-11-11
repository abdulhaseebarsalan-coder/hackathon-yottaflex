"""Load a published dataset into MySQL and verify it.

Usage:
    cp .env.example .env            # then edit .env (never commit it)
    python scripts/load_mysql.py --scale starter --dry-run     # check files + settings, no connection
    python scripts/load_mysql.py --scale starter --yes         # load and verify
    python scripts/load_mysql.py --scale full --yes

WARNING: drops and recreates the 15 project tables in MYSQL_DATABASE (default hackathon_db).
Other tables in that database are not touched. System databases are refused.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.datasets import load_ground_truth  # noqa: E402
from orchestrator.db.mysql_loader import (  # noqa: E402
    choose_backend, ground_truth_counts, load_dataset, settings_from_env,
)
from orchestrator.export.manifest import verify_manifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", default="starter", choices=["starter", "full"])
    ap.add_argument("--dir", type=Path, default=None, help="dataset directory (default: output/<scale>)")
    ap.add_argument("--env-file", type=Path, default=ROOT / ".env", help="credentials file (default: .env)")
    ap.add_argument("--dry-run", action="store_true", help="verify files and settings only; do not connect")
    ap.add_argument("--yes", action="store_true", help="confirm dropping/recreating the project tables")
    args = ap.parse_args()
    out_dir = args.dir or ROOT / "output" / args.scale

    integrity = verify_manifest(out_dir)
    print(f"Manifest check: {integrity['files_verified']}/{integrity['files_listed']} files verified"
          + ("" if integrity["ok"] else f" -- missing {integrity['missing']} changed {integrity['changed']} "
                                        f"unlisted {integrity['unlisted']}"))
    if not integrity["ok"]:
        print("Refusing to load: files do not match manifest.json. Re-run scripts/generate.py.")
        return 2
    try:
        settings = settings_from_env(env_file=args.env_file)
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 2
    print(f"Target: {settings.describe()}")

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    records, _ = load_ground_truth(out_dir / "ground_truth")
    scripts = {name: (ROOT / "sql" / name).read_text(encoding="utf-8")
               for name in ("schema.sql", "deferred_constraints.sql")}
    seed = (out_dir / "sql" / "seed.sql").read_text(encoding="utf-8")
    checks = (ROOT / "sql" / "queries" / "anomaly_checks.sql").read_text(encoding="utf-8")
    if args.dry_run:
        print(f"Dry run OK: would load {sum(manifest['row_counts'].values()):,} rows into {settings.database} "
              f"and run {checks.count('-- check:')} SQL anomaly checks.")
        return 0
    if not args.yes:
        print(f"This drops and recreates the project tables in `{settings.database}`. Re-run with --yes to proceed.")
        return 2

    backend = choose_backend(settings)
    print(f"Loading with {type(backend).__name__} ...")
    result = load_dataset(backend, settings, scripts["schema.sql"], seed, scripts["deferred_constraints.sql"],
                          manifest["row_counts"], checks, manifest["dataset"]["as_of_date"],
                          ground_truth_counts(records))
    print("\nRows loaded:")
    for table, n in result["rows_loaded"].items():
        print(f"  {table:<24}{n:>8}{'' if table not in result['row_mismatches'] else '  MISMATCH'}")
    fk = result["foreign_keys"]
    print(f"\nForeign keys present: {fk['present']}/{fk['expected']}")
    print("SQL anomaly checks vs ground truth:")
    for name, c in result["anomaly_checks"].items():
        print(f"  {name:<36} found={c['found']:>3} ground_truth={c['ground_truth']:>3} "
              f"{'OK' if c['found'] == c['ground_truth'] else 'MISMATCH'}")
    print(f"\nMySQL load: {'PASS' if result['ok'] else 'FAIL'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
