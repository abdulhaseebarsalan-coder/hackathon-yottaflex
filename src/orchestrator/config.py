"""Load generator settings and resolve a named scale (starter / full) into one flat config."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS = PROJECT_ROOT / "config" / "settings.json"


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int
    scale: str
    as_of_date: date
    case_id_year: int
    employees: int
    assets: int
    anomalies_per_type: int
    workforce: dict[str, Any]
    scenarios: dict[str, Any]
    sla: dict[str, Any]

    def window(self, key: str) -> tuple[date, date]:
        lo, hi = self.workforce[key]
        return date.fromisoformat(lo), date.fromisoformat(hi)


def load_config(
    scale: str = "starter",
    seed: int | None = None,
    settings_path: Path = DEFAULT_SETTINGS,
    employees: int | None = None,
) -> GeneratorConfig:
    """Build a config. `seed` and `employees` override the file (assets/anomalies scale with employees)."""
    raw = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    if scale not in raw["scales"]:
        raise ValueError(f"Unknown scale '{scale}'. Choose from: {sorted(raw['scales'])}")
    sc = raw["scales"][scale]

    n_emp = employees or sc["employees"]
    ratio = n_emp / sc["employees"]
    return GeneratorConfig(
        seed=seed if seed is not None else raw["seed"],
        scale=scale,
        as_of_date=date.fromisoformat(raw["as_of_date"]),
        case_id_year=raw["case_id_year"],
        employees=n_emp,
        assets=round(sc["assets"] * ratio),
        anomalies_per_type=max(1, round(sc["anomalies_per_type"] * ratio)),
        workforce=raw["workforce"],
        scenarios=raw["scenarios"],
        sla=raw["sla"],
    )
