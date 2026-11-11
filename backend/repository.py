"""Queries behind the API. Every user-supplied value is passed as a query parameter (no SQL injection)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.db import ROOT, Database, Row
from orchestrator.db.mysql_loader import parse_named_queries

TABLES = ["department", "role", "access_role", "role_access_template", "employee", "manager",
          "employee_role_history", "onboarding_case", "offboarding_case", "task", "task_dependency",
          "access_grant", "asset", "asset_assignment", "audit_event"]
CHECKS_SQL = ROOT / "sql" / "queries" / "anomaly_checks.sql"


# ------------------------------------------------------------------ health
def health(db: Database) -> dict[str, Any]:
    row = db.fetch("health", "SELECT VERSION() AS version, DATABASE() AS db, "
                             "(SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()) AS tables")[0]
    return {"status": "ok", "database": row["db"], "mysql_version": row["version"], "tables": int(row["tables"])}


# ------------------------------------------------------------------ summary
SUMMARY_SQL = "\nUNION ALL\n".join(
    [f"SELECT 'row_count' AS grp, '{t}' AS k, COUNT(*) AS n FROM `{t}`" for t in TABLES] + [
        "SELECT 'employees_by_status', employment_status, COUNT(*) FROM employee GROUP BY employment_status",
        "SELECT 'employees_by_type', employment_type, COUNT(*) FROM employee GROUP BY employment_type",
        "SELECT 'onboarding_by_status', case_status, COUNT(*) FROM onboarding_case GROUP BY case_status",
        "SELECT 'offboarding_by_exit_type', exit_type, COUNT(*) FROM offboarding_case GROUP BY exit_type",
        "SELECT 'grants_by_status', grant_status, COUNT(*) FROM access_grant GROUP BY grant_status",
        "SELECT 'assets_by_status', asset_status, COUNT(*) FROM asset GROUP BY asset_status",
        "SELECT 'tasks_by_status', task_status, COUNT(*) FROM task GROUP BY task_status",
        "SELECT 'active_by_department', d.department_name, COUNT(*) FROM employee e "
        "JOIN department d ON d.department_id = e.department_id WHERE e.employment_status = 'ACTIVE' "
        "GROUP BY d.department_name",
        "SELECT 'kpi', 'tasks_completed', COUNT(*) FROM task WHERE completed_date IS NOT NULL",
        "SELECT 'kpi', 'tasks_completed_on_time', COUNT(*) FROM task WHERE completed_date IS NOT NULL "
        "AND completed_date <= due_date",
        "SELECT 'kpi', 'open_overdue_tasks', COUNT(*) FROM task WHERE is_overdue = 1 AND completed_date IS NULL",
        "SELECT 'kpi', 'delayed_starts', COUNT(*) FROM onboarding_case WHERE start_delay_days > 0",
        "SELECT 'kpi', 'approved_overrides', COUNT(*) FROM access_grant WHERE override_event_id IS NOT NULL",
        "SELECT 'kpi', 'urgent_terminations', COUNT(*) FROM offboarding_case WHERE is_urgent = 1",
    ])


def summary(db: Database) -> dict[str, Any]:
    out: dict[str, dict[str, int]] = defaultdict(dict)
    for r in db.fetch("summary", SUMMARY_SQL):
        out[r["grp"]][r["k"]] = int(r["n"])
    k = out.get("kpi", {})
    done = k.get("tasks_completed", 0)
    result = {grp: dict(sorted(v.items(), key=lambda kv: (-kv[1], kv[0])) if grp != "row_count" else v.items())
              for grp, v in out.items() if grp != "kpi"}
    result["kpis"] = {**k, "task_on_time_rate": round(k.get("tasks_completed_on_time", 0) / done, 4) if done else None}
    return result


# ------------------------------------------------------------------ anomalies
def _dataset_meta(dataset_dir: Path) -> tuple[str, list[dict]]:
    manifest = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))
    gt_path = dataset_dir / "ground_truth" / "anomalies.json"
    records = json.loads(gt_path.read_text(encoding="utf-8"))["anomalies"] if gt_path.exists() else []
    return manifest["dataset"]["as_of_date"], records


def anomalies(db: Database, dataset_dir: Path, issue_type: str | None = None, severity: str | None = None) -> dict[str, Any]:
    """Run the five SQL rule checks live in MySQL and join the findings with the ground-truth labels."""
    as_of, ground_truth = _dataset_meta(dataset_dir)
    checks = parse_named_queries(CHECKS_SQL.read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for name, sql in checks.items():
        rows = db.fetch(f"check:{name}", sql.rstrip().rstrip(";"), session_vars={"as_of": as_of})
        found[name] = {str(next(iter(r.values()))) for r in rows}
    labelled = {(g["issue_type"], g["detection_key"]) for g in ground_truth}
    items = [{
        "anomaly_id": g["anomaly_id"], "issue_type": g["issue_type"], "sub_type": g["sub_type"],
        "severity": g["severity"], "rule_id": g["rule_id"], "entity_type": g["entity_type"],
        "detection_key": g["detection_key"], "employee_id": g["employee_id"], "case_id": g["case_id"],
        "expected_decision": g["expected_decision"], "description": g["description"],
        "confirmed_in_db": g["detection_key"] in found.get(g["issue_type"], set()),
    } for g in ground_truth]
    unexpected = sorted({(it, key) for it, keys in found.items() for key in keys} - labelled)
    summary_rows = [{"issue_type": it, "found_in_db": len(found.get(it, set())),
                     "ground_truth": sum(1 for g in ground_truth if g["issue_type"] == it)} for it in checks]
    for s in summary_rows:
        s["match"] = s["found_in_db"] == s["ground_truth"]
    if issue_type:
        items = [i for i in items if i["issue_type"] == issue_type]
    if severity:
        items = [i for i in items if i["severity"] == severity]
    return {"as_of_date": as_of, "summary": summary_rows, "total": len(items), "items": items,
            "unexpected_findings": [{"issue_type": it, "detection_key": k} for it, k in unexpected]}


# ------------------------------------------------------------------ employees
EMPLOYEE_FILTER = """
FROM employee e
JOIN department d ON d.department_id = e.department_id
JOIN role r ON r.role_id = e.role_id
WHERE (%(q)s IS NULL OR e.employee_id LIKE %(like)s OR e.first_name LIKE %(like)s OR e.last_name LIKE %(like)s
       OR e.work_email LIKE %(like)s OR CONCAT(e.first_name, ' ', e.last_name) LIKE %(like)s)
  AND (%(status)s IS NULL OR e.employment_status = %(status)s)
"""
EMPLOYEE_LIST_SQL = ("SELECT e.employee_id, e.first_name, e.last_name, e.work_email, e.employment_type, "
                     "e.employment_status, e.work_location, e.start_date, d.department_name, r.role_title"
                     + EMPLOYEE_FILTER + "ORDER BY e.employee_id\nLIMIT %(limit)s OFFSET %(offset)s")
EMPLOYEE_COUNT_SQL = "SELECT COUNT(*) AS n" + EMPLOYEE_FILTER


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_employees(db: Database, q: str | None, status: str | None, limit: int, offset: int) -> dict[str, Any]:
    q = (q or "").strip() or None
    params = {"q": q, "like": f"%{_escape_like(q)}%" if q else None, "status": status, "limit": limit, "offset": offset}
    total = int(db.fetch("employee_count", EMPLOYEE_COUNT_SQL, params)[0]["n"])
    return {"total": total, "limit": limit, "offset": offset, "items": db.fetch("employee_list", EMPLOYEE_LIST_SQL, params)}


DETAIL_SQL = {
    "profile": """
        SELECT e.*, d.department_name, r.role_title, r.job_level,
               CONCAT(me.first_name, ' ', me.last_name) AS manager_name
        FROM employee e
        JOIN department d ON d.department_id = e.department_id
        JOIN role r ON r.role_id = e.role_id
        LEFT JOIN manager m ON m.manager_id = e.manager_id
        LEFT JOIN employee me ON me.employee_id = m.employee_id
        WHERE e.employee_id = %(id)s""",
    "role_history": """
        SELECT h.role_history_id, h.effective_from, h.effective_to, h.change_reason, r.role_title, d.department_name
        FROM employee_role_history h JOIN role r ON r.role_id = h.role_id
        JOIN department d ON d.department_id = h.department_id
        WHERE h.employee_id = %(id)s ORDER BY h.effective_from""",
    "onboarding_case": "SELECT * FROM onboarding_case WHERE employee_id = %(id)s",
    "offboarding_case": "SELECT * FROM offboarding_case WHERE employee_id = %(id)s",
    "tasks": """
        SELECT t.task_id, COALESCE(t.onboarding_case_id, t.offboarding_case_id) AS case_id, t.task_seq, t.task_code,
               t.task_name, t.owner_team, t.due_date, t.started_date, t.completed_date, t.task_status, t.is_overdue,
               (SELECT GROUP_CONCAT(d.depends_on_task_id ORDER BY d.depends_on_task_id SEPARATOR ',')
                  FROM task_dependency d WHERE d.task_id = t.task_id) AS depends_on
        FROM task t
        LEFT JOIN onboarding_case o ON o.onboarding_case_id = t.onboarding_case_id
        LEFT JOIN offboarding_case f ON f.offboarding_case_id = t.offboarding_case_id
        WHERE o.employee_id = %(id)s OR f.employee_id = %(id)s
        ORDER BY case_id, t.task_seq""",
    "access_grants": """
        SELECT g.grant_id, a.access_role_name, a.privilege_level, g.request_reason, g.requested_date,
               g.approval_status, g.approval_date, g.provisioned_date, g.override_event_id, g.revoked_date,
               g.revocation_reason, g.grant_status
        FROM access_grant g JOIN access_role a ON a.access_role_id = g.access_role_id
        WHERE g.employee_id = %(id)s ORDER BY g.grant_id""",
    "assets": """
        SELECT s.assignment_id, s.asset_id, a.asset_type, a.model, a.asset_status, s.assigned_date, s.delivered_date,
               s.acknowledged_date, s.returned_date, s.lost_reported_date, s.assignment_status
        FROM asset_assignment s JOIN asset a ON a.asset_id = s.asset_id
        WHERE s.employee_id = %(id)s ORDER BY s.assigned_date, s.assignment_id""",
    "audit_trail": """
        SELECT ev.event_id, ev.event_timestamp, ev.entity_type, ev.entity_id, ev.event_type, ev.actor_role,
               ev.actor_id, ev.from_state, ev.to_state, ev.related_case_id, ev.reason
        FROM audit_event ev
        WHERE ev.entity_id = %(id)s
           OR ev.related_case_id IN (SELECT onboarding_case_id FROM onboarding_case WHERE employee_id = %(id)s
                                     UNION SELECT offboarding_case_id FROM offboarding_case WHERE employee_id = %(id)s)
           OR ev.entity_id IN (SELECT grant_id FROM access_grant WHERE employee_id = %(id)s)
           OR ev.entity_id IN (SELECT assignment_id FROM asset_assignment WHERE employee_id = %(id)s)
        ORDER BY ev.event_timestamp, ev.event_id
        LIMIT 500""",
}


def employee_detail(db: Database, employee_id: str) -> dict[str, Any] | None:
    params = {"id": employee_id}
    profile = db.fetch("employee_profile", DETAIL_SQL["profile"], params)
    if not profile:
        return None
    out: dict[str, Any] = {"profile": profile[0]}
    for key in ("role_history", "onboarding_case", "offboarding_case", "tasks", "access_grants", "assets", "audit_trail"):
        rows: list[Row] = db.fetch(f"employee_{key}", DETAIL_SQL[key], params)
        out[key] = rows[0] if key.endswith("_case") and rows else (None if key.endswith("_case") else rows)
    return out
