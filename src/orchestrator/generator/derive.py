"""Final stage - derived values, computed only from source records (never generated independently).

Runs after anomaly injection so every status/total reflects the data exactly as published.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from orchestrator.generator.context import GenContext

ASSET_STATE_BY_EVENT = {"ASSET_PURCHASED": "IN_STOCK", "ASSET_ASSIGNED": "ASSIGNED", "ASSET_RETURNED": "IN_STOCK",
                        "ASSET_LOST": "LOST", "ASSET_RETIRED": "RETIRED"}


def derive_all(ctx: GenContext) -> None:
    as_of, t = ctx.as_of, ctx.tables

    onb_by_emp = {c["employee_id"]: c for c in t["onboarding_case"]}
    off_by_emp = {c["employee_id"]: c for c in t["offboarding_case"]}
    hist = defaultdict(list)
    for h in t["employee_role_history"]:
        hist[h["employee_id"]].append(h)

    # ---- onboarding case: planned start from START_DATE_CHANGED events
    latest_start: dict[str, tuple] = {}
    for e in t["audit_event"]:
        if e["event_type"] == "START_DATE_CHANGED":
            prev = latest_start.get(e["entity_id"])
            if prev is None or e["event_timestamp"] >= prev[0]:
                latest_start[e["entity_id"]] = (e["event_timestamp"], date.fromisoformat(e["to_state"]))
    for c in t["onboarding_case"]:
        c["planned_start_date"] = latest_start.get(c["onboarding_case_id"], (None, c["original_start_date"]))[1]
        c["start_delay_days"] = (c["planned_start_date"] - c["original_start_date"]).days

    # ---- employee
    for e in t["employee"]:
        eid = e["employee_id"]
        rows = sorted(hist.get(eid, []), key=lambda h: h["effective_from"])
        if rows:
            cur = rows[-1]
            e["role_id"], e["department_id"], e["manager_id"] = cur["role_id"], cur["department_id"], cur["manager_id"]
        onb, off = onb_by_emp.get(eid), off_by_emp.get(eid)
        e["start_date"] = (onb["actual_start_date"] or onb["planned_start_date"]) if onb else rows[0]["effective_from"]
        e["termination_date"] = off["last_working_date"] if off and off["last_working_date"] < as_of else None
        if onb and onb["cancelled_date"]:
            e["employment_status"] = "WITHDRAWN"
        elif e["start_date"] > as_of:
            e["employment_status"] = "PRE_BOARDING"
        elif e["termination_date"]:
            e["employment_status"] = "TERMINATED"
        else:
            e["employment_status"] = "ACTIVE"

    for m in t["manager"]:
        m["manager_status"] = "ACTIVE" if m["effective_to"] is None or m["effective_to"] >= as_of else "INACTIVE"

    # ---- tasks
    task_by_id = {x["task_id"]: x for x in t["task"]}
    prereqs = defaultdict(list)
    for d in t["task_dependency"]:
        prereqs[d["task_id"]].append(d["depends_on_task_id"])
    cancelled_cases = {c["onboarding_case_id"] for c in t["onboarding_case"] if c["cancelled_date"]}
    for x in t["task"]:
        case_cancelled = x["onboarding_case_id"] in cancelled_cases
        if x["completed_date"]:
            x["task_status"] = "COMPLETED"
        elif case_cancelled:
            x["task_status"] = "CANCELLED"
        elif x["started_date"]:
            x["task_status"] = "IN_PROGRESS"
        elif all(task_by_id[p]["completed_date"] for p in prereqs[x["task_id"]]):
            x["task_status"] = "READY"
        else:
            x["task_status"] = "BLOCKED"
        if x["completed_date"]:
            x["is_overdue"] = x["completed_date"] > x["due_date"]
        else:
            x["is_overdue"] = (not case_cancelled) and as_of > x["due_date"]

    tasks_by_case = defaultdict(list)
    for x in t["task"]:
        tasks_by_case[x["onboarding_case_id"] or x["offboarding_case_id"]].append(x)

    def case_rollup(c: dict, case_id: str) -> None:
        tasks = tasks_by_case[case_id]
        done = [x["completed_date"] for x in tasks if x["completed_date"]]
        c["total_tasks"] = len(tasks)
        c["completed_tasks"] = len(done)
        if c.get("cancelled_date"):
            c["case_status"], c["case_completed_date"] = "CANCELLED", None
        elif tasks and len(done) == len(tasks):
            c["case_status"], c["case_completed_date"] = "COMPLETED", max(done)
        else:
            c["case_status"] = "IN_PROGRESS" if any(x["started_date"] for x in tasks) else "OPEN"
            c["case_completed_date"] = None

    for c in t["onboarding_case"]:
        case_rollup(c, c["onboarding_case_id"])
        c["completion_pct"] = round(100.0 * c["completed_tasks"] / c["total_tasks"], 2) if c["total_tasks"] else 0.0

    # ---- grants
    for g in t["access_grant"]:
        if g["revoked_date"]:
            g["grant_status"] = "REVOKED"
        elif g["provisioned_date"]:
            g["grant_status"] = "ACTIVE"
        elif g["approval_status"] == "REJECTED":   # a rejection is final, even if the offer is later withdrawn
            g["grant_status"] = "REJECTED"
        elif g["onboarding_case_id"] in cancelled_cases:
            g["grant_status"] = "CANCELLED"
        elif g["approval_status"] == "APPROVED":
            g["grant_status"] = "APPROVED"
        else:
            g["grant_status"] = "REQUESTED"

    # ---- assets (status from the asset's own lifecycle events) and assignments
    asset_state: dict[str, tuple] = {}
    for e in t["audit_event"]:
        if e["entity_type"] == "asset" and e["event_type"] in ASSET_STATE_BY_EVENT and e["event_timestamp"].date() <= as_of:
            prev = asset_state.get(e["entity_id"])
            if prev is None or e["event_timestamp"] >= prev[0]:
                asset_state[e["entity_id"]] = (e["event_timestamp"], ASSET_STATE_BY_EVENT[e["event_type"]])
    for a in t["asset"]:
        a["asset_status"] = asset_state[a["asset_id"]][1]
    for s in t["asset_assignment"]:
        if s["lost_reported_date"]:
            s["assignment_status"] = "LOST"
        elif s["returned_date"]:
            s["assignment_status"] = "RETURNED"
        elif s["acknowledged_date"]:
            s["assignment_status"] = "ACKNOWLEDGED"
        elif s["delivered_date"]:
            s["assignment_status"] = "DELIVERED"
        else:
            s["assignment_status"] = "ASSIGNED"

    # ---- offboarding case: revocation SLA and reconciliation counts
    grants_by_emp, asg_by_emp = defaultdict(list), defaultdict(list)
    for g in t["access_grant"]:
        grants_by_emp[g["employee_id"]].append(g)
    for s in t["asset_assignment"]:
        asg_by_emp[s["employee_id"]].append(s)
    for c in t["offboarding_case"]:
        notice, emp = c["notice_date"], c["employee_id"]
        c["is_urgent"] = c["exit_type"] == "URGENT_TERMINATION"
        c["revocation_due_date"] = notice if c["is_urgent"] else c["last_working_date"] + timedelta(days=1)
        in_scope = [g for g in grants_by_emp[emp]
                    if g["provisioned_date"] and (g["revoked_date"] is None or g["revoked_date"] >= notice)]
        c["grants_to_revoke"] = len(in_scope)
        c["grants_revoked"] = sum(1 for g in in_scope if g["revoked_date"])
        held = [s for s in asg_by_emp[emp] if s["assigned_date"] <= notice
                and (s["returned_date"] is None or s["returned_date"] >= notice)]
        c["assets_to_recover"] = len(held)
        c["assets_recovered"] = sum(1 for s in held if s["returned_date"])
        case_rollup(c, c["offboarding_case_id"])
