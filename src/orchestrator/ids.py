"""Deterministic, immutable ID formats (zero-padded counters).

Required by the brief:
    EMP-000001 | ONB-2026-000001 | TASK-<case-id>-001 | GRANT-<employee-id>-001 | AST-000001 | OFF-2026-000001
Supporting entities follow the same style.
"""

from __future__ import annotations

import re
from collections import defaultdict


def employee_id(n: int) -> str:
    return f"EMP-{n:06d}"


def onboarding_case_id(year: int, n: int) -> str:
    return f"ONB-{year}-{n:06d}"


def offboarding_case_id(year: int, n: int) -> str:
    return f"OFF-{year}-{n:06d}"


def task_id(case_id: str, n: int) -> str:
    return f"TASK-{case_id}-{n:03d}"


def grant_id(emp_id: str, n: int) -> str:
    return f"GRANT-{emp_id}-{n:03d}"


def asset_id(n: int) -> str:
    return f"AST-{n:06d}"


def department_id(n: int) -> str:
    return f"DEPT-{n:03d}"


def role_id(n: int) -> str:
    return f"ROLE-{n:03d}"


def manager_id(n: int) -> str:
    return f"MGR-{n:04d}"


def access_role_id(n: int) -> str:
    return f"AR-{n:03d}"


def template_id(n: int) -> str:
    return f"RAT-{n:04d}"


def role_history_id(n: int) -> str:
    return f"RH-{n:06d}"


def dependency_id(n: int) -> str:
    return f"DEPN-{n:06d}"


def assignment_id(n: int) -> str:
    return f"ASG-{n:06d}"


def event_id(n: int) -> str:
    return f"EVT-{n:08d}"


def anomaly_id(n: int) -> str:
    return f"ANOM-{n:04d}"


# Regex patterns used by the validator (and the data dictionary).
PATTERNS: dict[str, str] = {
    "employee_id": r"^EMP-\d{6}$",
    "onboarding_case_id": r"^ONB-\d{4}-\d{6}$",
    "offboarding_case_id": r"^OFF-\d{4}-\d{6}$",
    "task_id": r"^TASK-(ONB|OFF)-\d{4}-\d{6}-\d{3}$",
    "grant_id": r"^GRANT-EMP-\d{6}-\d{3}$",
    "asset_id": r"^AST-\d{6}$",
    "department_id": r"^DEPT-\d{3}$",
    "role_id": r"^ROLE-\d{3}$",
    "manager_id": r"^MGR-\d{4}$",
    "access_role_id": r"^AR-\d{3}$",
    "template_id": r"^RAT-\d{4}$",
    "role_history_id": r"^RH-\d{6}$",
    "dependency_id": r"^DEPN-\d{6}$",
    "assignment_id": r"^ASG-\d{6}$",
    "event_id": r"^EVT-\d{8}$",
}
COMPILED = {k: re.compile(v) for k, v in PATTERNS.items()}


class Counter:
    """Monotonic counters, optionally scoped (e.g. per employee for GRANT ids, per case for TASK ids)."""

    def __init__(self) -> None:
        self._global = 0
        self._scoped: dict[str, int] = defaultdict(int)

    def next(self, scope: str | None = None) -> int:
        if scope is None:
            self._global += 1
            return self._global
        self._scoped[scope] += 1
        return self._scoped[scope]
