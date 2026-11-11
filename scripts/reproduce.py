"""Standalone reproducibility check (brief Step 10).

Usage:
    python scripts/reproduce.py                    # starter + full, 2 fresh runs each
    python scripts/reproduce.py --scale full
    python scripts/reproduce.py --verify-only      # only re-hash output/<scale> against its manifest

For each scale:
  1. verify output/<scale> against output/<scale>/manifest.json (integrity of the published files)
  2. regenerate the dataset N times (default 2) into fresh scratch folders with the same seed
  3. compare every file's SHA-256 and every table's row count across the runs AND with the published
     manifest
Exit code 0 only if every comparison is identical. Scratch folders (output/_repro_*) are git-ignored
and removed afterwards unless --keep is given.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.config import load_config  # noqa: E402
from orchestrator.export.manifest import verify_manifest  # noqa: E402
from orchestrator.publish import publish  # noqa: E402


def file_hashes(manifest: dict) -> dict[str, str]:
    return {f["path"]: f["sha256"] for f in manifest["files"]}


def compare(a: dict, b: dict) -> list[str]:
    """Differences between two manifests (file set, hashes, row counts)."""
    ha, hb = file_hashes(a), file_hashes(b)
    diffs = [f"only in first: {p}" for p in sorted(set(ha) - set(hb))]
    diffs += [f"only in second: {p}" for p in sorted(set(hb) - set(ha))]
    diffs += [f"checksum differs: {p}" for p in sorted(set(ha) & set(hb)) if ha[p] != hb[p]]
    diffs += [f"row count differs: {t}" for t in sorted(a["row_counts"]) if a["row_counts"][t] != b["row_counts"].get(t)]
    return diffs


def check_scale(scale: str, runs: int, keep: bool, verify_only: bool) -> bool:
    import json
    published = ROOT / "output" / scale
    ok = True
    print(f"\n=== {scale} ===")
    pub_manifest = None
    if (published / "manifest.json").exists():
        v = verify_manifest(published)
        pub_manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
        print(f"published files vs manifest: {v['files_verified']}/{v['files_listed']} verified"
              + ("" if v["ok"] else f"  missing={v['missing']} changed={v['changed']} unlisted={v['unlisted']}"))
        ok &= v["ok"]
    else:
        print(f"no published manifest at {published} (run scripts/generate.py --scale {scale})")
        ok = False if verify_only else ok
    if verify_only:
        return ok

    cfg = load_config(scale)
    manifests = []
    for i in range(1, runs + 1):
        scratch = ROOT / "output" / f"_repro_{scale}_run{i}"
        if scratch.exists():
            shutil.rmtree(scratch)
        manifests.append(publish(cfg, scratch)["manifest"])
        print(f"run {i}: {len(manifests[-1]['files'])} files, {sum(manifests[-1]['row_counts'].values()):,} rows, "
              f"verdict {'PASS' if manifests[-1]['evaluation_verdict']['passed'] else 'FAIL'}")
        if not keep:
            shutil.rmtree(scratch)
    for i in range(1, runs):
        d = compare(manifests[0], manifests[i])
        print(f"run 1 vs run {i + 1}: {'identical' if not d else f'{len(d)} differences'}")
        for line in d[:10]:
            print(f"   {line}")
        ok &= not d
    if pub_manifest:
        d = compare(manifests[0], pub_manifest)
        print(f"fresh run vs published output/{scale}: {'identical' if not d else f'{len(d)} differences'}")
        for line in d[:10]:
            print(f"   {line}")
        ok &= not d
    print("row counts: " + ", ".join(f"{t}={n}" for t, n in manifests[0]["row_counts"].items()))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", default="all", choices=["starter", "full", "all"])
    ap.add_argument("--runs", type=int, default=2, help="fresh regenerations per scale (>= 2)")
    ap.add_argument("--keep", action="store_true", help="keep scratch folders output/_repro_*")
    ap.add_argument("--verify-only", action="store_true", help="only verify published files against manifests")
    args = ap.parse_args()
    scales = ["starter", "full"] if args.scale == "all" else [args.scale]
    results = {s: check_scale(s, max(2, args.runs), args.keep, args.verify_only) for s in scales}
    print("\nReproducibility: " + ", ".join(f"{s} {'PASS' if ok else 'FAIL'}" for s, ok in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
