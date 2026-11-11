"""SHA-256 checksum manifest for a published dataset folder, and its verification.

output/<scale>/manifest.json lists every published file (relative POSIX path, bytes, SHA-256, rows
for tabular files) plus the generator command, seed, code version, source provenance and the
evaluation verdict. It contains no timestamps or machine-specific paths, so two runs with the same
seed and code produce a byte-identical manifest. The manifest does not list itself.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from orchestrator import __version__
from orchestrator.config import GeneratorConfig

MANIFEST_NAME = "manifest.json"
PUBLISHED_DIRS = ("csv", "json", "sql", "ground_truth", "reports")

SOURCES = [{
    "name": "orchestrator synthetic generator",
    "type": "synthetic",
    "external_data": False,
    "description": "All records are generated from hand-authored reference data in src/orchestrator/reference.py "
                   "(generic names, roles, systems and asset models combined at random). Emails use the reserved "
                   "domain example.com (RFC 2606). No real person, organisation or confidential data is used.",
    "license": "Generated data may be used and redistributed freely together with this repository.",
    "fields_discarded": [],
    "transformations": "none (no external source)",
}]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rows(path: Path) -> int | None:
    if path.suffix == ".csv":
        with path.open("rb") as fh:
            return max(0, sum(1 for _ in fh) - 1)
    if path.suffix == ".json" and path.parent.name == "json":
        with path.open(encoding="utf-8") as fh:
            return json.loads(fh.readline().split(', "rows": [')[0] + "}")["row_count"]
    return None


def published_files(out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    files = [p for d in PUBLISHED_DIRS if (out_dir / d).is_dir() for p in (out_dir / d).rglob("*") if p.is_file()]
    return sorted(files, key=lambda p: p.relative_to(out_dir).as_posix())


def build_manifest(out_dir: Path, cfg: GeneratorConfig, inject: bool, verdict: dict[str, Any],
                   row_counts: dict[str, int]) -> dict[str, Any]:
    out_dir = Path(out_dir)
    files = []
    for p in published_files(out_dir):
        entry = {"path": p.relative_to(out_dir).as_posix(), "bytes": p.stat().st_size, "sha256": sha256_file(p)}
        rows = _rows(p)
        if rows is not None:
            entry["rows"] = rows
        files.append(entry)
    cmd = f"python scripts/generate.py --scale {cfg.scale} --seed {cfg.seed}"
    if cfg.employees != {"starter": 100, "full": 1000}.get(cfg.scale):
        cmd += f" --employees {cfg.employees}"
    if not inject:
        cmd += " --no-anomalies"
    return {
        "manifest_version": 1,
        "dataset": {"scale": cfg.scale, "seed": cfg.seed, "as_of_date": cfg.as_of_date.isoformat(),
                    "employees": cfg.employees, "anomalies_injected": inject},
        "generator": {"command": cmd, "code_version": __version__, "python_requires": ">=3.10",
                      "dependencies": "standard library only"},
        "sources": SOURCES,
        "row_counts": row_counts,
        "evaluation_verdict": verdict,
        "files": files,
    }


def write_manifest(manifest: dict[str, Any], out_dir: Path) -> Path:
    path = Path(out_dir) / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return path


def verify_manifest(out_dir: Path) -> dict[str, Any]:
    """Re-hash every published file and compare with manifest.json (missing / changed / unlisted files)."""
    out_dir = Path(out_dir)
    manifest = json.loads((out_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    listed = {f["path"]: f for f in manifest["files"]}
    present = {p.relative_to(out_dir).as_posix(): p for p in published_files(out_dir)}
    missing = sorted(set(listed) - set(present))
    unlisted = sorted(set(present) - set(listed))
    changed = sorted(path for path in set(listed) & set(present)
                     if sha256_file(present[path]) != listed[path]["sha256"])
    return {"files_listed": len(listed), "files_verified": len(listed) - len(missing) - len(changed),
            "missing": missing, "changed": changed, "unlisted": unlisted,
            "ok": not (missing or changed or unlisted)}
