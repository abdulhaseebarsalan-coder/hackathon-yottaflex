"""Regenerate the schema-derived artifacts committed in the repository.

Usage:
    python scripts/export_schema.py

Writes sql/schema.sql, sql/deferred_constraints.sql, docs/data_dictionary.md and docs/er_diagram.md
from src/orchestrator/schema.py. A test fails if the committed files are out of date.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.export.docs_writer import build_data_dictionary, build_er_diagram  # noqa: E402
from orchestrator.export.sql_writer import build_deferred_sql, build_schema_sql  # noqa: E402

ARTIFACTS = {
    "sql/schema.sql": build_schema_sql,
    "sql/deferred_constraints.sql": build_deferred_sql,
    "docs/data_dictionary.md": build_data_dictionary,
    "docs/er_diagram.md": build_er_diagram,
}


def main() -> int:
    for rel, build in ARTIFACTS.items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(build(), encoding="utf-8", newline="\n")
        print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
