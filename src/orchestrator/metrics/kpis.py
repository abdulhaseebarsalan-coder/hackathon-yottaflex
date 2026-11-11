"""Workflow KPIs (measurable outputs), computed only from the dataset's tables.

Definitions
-----------
day_one_readiness_rate   started hires whose SSO grant was active on/before their start date AND whose
                         equipment (if any was issued) was delivered on/before the start date
delayed_start_rate       non-cancelled onboarding cases with start_delay_days > 0
task_on_time_rate        completed tasks with completed_date <= due_date
revocation_sla_rate      exits past their revocation_due_date where every in-scope grant was revoked
                         on/before the due date
urgent_same_day_rate     urgent terminations whose grants were all revoked on the notice date
asset_recovery_rate      assets in scope at exit (cases past the 10-day recovery SLA) that were returned
access_reconciliation    ACTIVE grants held by an ACTIVE employee, or by a PRE_BOARDING employee through
                         an approved early-access override
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import mean, median
from typing import Any

Row = dict[str, Any]
RECOVERY_SLA_DAYS = 10


def _rate(num: int, den: int) -> dict[str, Any]:
    return {"numerator": num, "denominator": den, "rate": round(num / den, 4) if den else None}


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def compute_kpis(t: dict[str, list[Row]], as_of: date) -> dict[str, Any]:
    emp = {e["employee_id"]: e for e in t["employee"]}
    events = {e["event_id"]: e for e in t["audit_event"]}
    sso = next(a["access_role_id"] for a in t["access_role"] if a["access_role_name"] == "Corporate SSO & Email")
    tasks_by_case = defaultdict(list)
    for x in t["task"]:
        tasks_by_case[x["onboarding_case_id"] or x["offboarding_case_id"]].append(x)
    grants_by_emp = defaultdict(list)
    for g in t["access_grant"]:
        grants_by_emp[g["employee_id"]].append(g)

    # ---------------------------------------------------------------- onboarding
    onb = t["onboarding_case"]
    live = [c for c in onb if c["case_status"] != "CANCELLED"]
    delayed = [c for c in live if c["start_delay_days"] > 0]
    started = [c for c in onb if c["actual_start_date"]]
    ready = 0
    for c in started:
        s = c["actual_start_date"]
        sso_ok = any(g["access_role_id"] == sso and g["provisioned_date"] and g["provisioned_date"] <= s
                     for g in grants_by_emp[c["employee_id"]])
        ship = [x for x in tasks_by_case[c["onboarding_case_id"]] if x["task_code"] == "ONB_EQUIPMENT_SHIP"]
        equip_ok = not ship or (ship[0]["completed_date"] is not None and ship[0]["completed_date"] <= s)
        ready += sso_ok and equip_ok
    completed = [c for c in onb if c["case_status"] == "COMPLETED"]
    new_hire_prov = [(g["provisioned_date"] - g["approval_date"]).days for g in t["access_grant"]
                     if g["request_reason"] == "NEW_HIRE" and g["provisioned_date"] and g["approval_date"]]
    onboarding = {
        "cases": len(onb),
        "by_status": dict(sorted(Counter(c["case_status"] for c in onb).items())),
        "offer_withdrawal_rate": _rate(len(onb) - len(live), len(onb)),
        "delayed_start_rate": _rate(len(delayed), len(live)),
        "avg_start_delay_days_when_delayed": _avg([c["start_delay_days"] for c in delayed]),
        "avg_offer_to_start_days": _avg([(c["planned_start_date"] - c["case_created_date"]).days for c in live]),
        "day_one_readiness_rate": _rate(ready, len(started)),
        "avg_case_cycle_days": _avg([(c["case_completed_date"] - c["case_created_date"]).days for c in completed]),
        "median_days_approval_to_provisioning": median(new_hire_prov) if new_hire_prov else None,
    }

    # ---------------------------------------------------------------- tasks
    done = [x for x in t["task"] if x["completed_date"]]
    by_team = {}
    for team in sorted({x["owner_team"] for x in t["task"]}):
        team_done = [x for x in done if x["owner_team"] == team]
        by_team[team] = _rate(sum(1 for x in team_done if x["completed_date"] <= x["due_date"]), len(team_done))
    tasks = {
        "tasks": len(t["task"]),
        "by_status": dict(sorted(Counter(x["task_status"] for x in t["task"]).items())),
        "on_time_rate": _rate(sum(1 for x in done if x["completed_date"] <= x["due_date"]), len(done)),
        "on_time_rate_by_owner_team": by_team,
        "open_overdue": sum(1 for x in t["task"] if x["is_overdue"] and not x["completed_date"]),
    }

    # ---------------------------------------------------------------- access
    g_all = t["access_grant"]
    decided = [g for g in g_all if g["approval_status"] in ("APPROVED", "REJECTED")]
    overrides = [g for g in g_all if g["override_event_id"]]
    linked = sum(1 for g in overrides if events.get(g["override_event_id"], {}).get("event_type") == "ACCESS_OVERRIDE_APPROVED")
    active = [g for g in g_all if g["grant_status"] == "ACTIVE"]

    def reconciled(g: Row) -> bool:
        status = emp[g["employee_id"]]["employment_status"]
        return status == "ACTIVE" or (status == "PRE_BOARDING" and bool(g["override_event_id"]))
    access = {
        "grants": len(g_all),
        "by_status": dict(sorted(Counter(g["grant_status"] for g in g_all).items())),
        "by_request_reason": dict(sorted(Counter(g["request_reason"] for g in g_all).items())),
        "rejection_rate": _rate(sum(1 for g in decided if g["approval_status"] == "REJECTED"), len(decided)),
        "approved_overrides": len(overrides),
        "overrides_with_linked_audit_event": _rate(linked, len(overrides)),
        "active_grants_reconciled_to_active_employment": _rate(sum(1 for g in active if reconciled(g)), len(active)),
        "active_grants_not_reconciled": sum(1 for g in active if not reconciled(g)),
    }

    # ---------------------------------------------------------------- offboarding
    off = t["offboarding_case"]
    past_due = [c for c in off if as_of > c["revocation_due_date"] and c["grants_to_revoke"] > 0]
    on_time, late = 0, 0
    for c in past_due:
        gs = [g for g in grants_by_emp[c["employee_id"]] if g["provisioned_date"]
              and (g["revoked_date"] is None or g["revoked_date"] >= c["notice_date"])]
        if all(g["revoked_date"] and g["revoked_date"] <= c["revocation_due_date"] for g in gs):
            on_time += 1
        elif all(g["revoked_date"] for g in gs):
            late += 1
    urgent = [c for c in off if c["is_urgent"]]
    urgent_ok = sum(1 for c in urgent if all(g["revoked_date"] == c["notice_date"] for g in grants_by_emp[c["employee_id"]]
                                             if g["offboarding_case_id"] == c["offboarding_case_id"])
                    and c["grants_revoked"] == c["grants_to_revoke"])
    lost_by_case = Counter(s["offboarding_case_id"] for s in t["asset_assignment"] if s["lost_reported_date"])
    rec_scope = [c for c in off if as_of > c["last_working_date"] + timedelta(days=RECOVERY_SLA_DAYS)]
    offboarding = {
        "cases": len(off),
        "by_exit_type": dict(sorted(Counter(c["exit_type"] for c in off).items())),
        "by_status": dict(sorted(Counter(c["case_status"] for c in off).items())),
        "revocation_sla_rate": _rate(on_time, len(past_due)),
        "revoked_late_count": late,
        "unrevoked_after_due_count": len(past_due) - on_time - late,
        "urgent_same_day_rate": _rate(urgent_ok, len(urgent)),
        "asset_recovery_rate": _rate(sum(c["assets_recovered"] for c in rec_scope), sum(c["assets_to_recover"] for c in rec_scope)),
        "assets_reported_lost": sum(lost_by_case.values()),
    }

    # ---------------------------------------------------------------- assets
    asg = t["asset_assignment"]
    per_asset = Counter(s["asset_id"] for s in asg)
    delivered = [s for s in asg if s["delivered_date"]]
    assets = {
        "assets": len(t["asset"]),
        "by_status": dict(sorted(Counter(a["asset_status"] for a in t["asset"]).items())),
        "by_type": dict(sorted(Counter(a["asset_type"] for a in t["asset"]).items())),
        "utilization_rate": _rate(sum(1 for a in t["asset"] if a["asset_status"] == "ASSIGNED"), len(t["asset"])),
        "reissued_assets": sum(1 for n in per_asset.values() if n > 1),
        "acknowledgement_rate": _rate(sum(1 for s in delivered if s["acknowledged_date"]), len(delivered)),
        "inventory_value_usd": round(sum(a["purchase_cost"] for a in t["asset"]), 2),
    }

    audit = {
        "events": len(t["audit_event"]),
        "by_entity_type": dict(sorted(Counter(e["entity_type"] for e in t["audit_event"]).items())),
    }
    return {"onboarding": onboarding, "tasks": tasks, "access": access, "offboarding": offboarding,
            "assets": assets, "audit": audit}


def scenario_coverage(t: dict[str, list[Row]], as_of: date, ground_truth: list[Row]) -> list[dict[str, Any]]:
    """Normal / boundary / exception / failure scenarios present in the data, with counts."""
    emp = t["employee"]
    onb, off, g, asg = t["onboarding_case"], t["offboarding_case"], t["access_grant"], t["asset_assignment"]
    gt = Counter(r["issue_type"] for r in ground_truth)
    rows = [
        ("normal", "Onboarding case completed", sum(1 for c in onb if c["case_status"] == "COMPLETED")),
        ("normal", "Voluntary exit", sum(1 for c in off if c["exit_type"] == "VOLUNTARY")),
        ("normal", "Asset re-issued after recovery", sum(1 for n in Counter(s["asset_id"] for s in asg).values() if n > 1)),
        ("boundary", "Pre-boarding hire (start after snapshot)", sum(1 for e in emp if e["employment_status"] == "PRE_BOARDING")),
        ("boundary", "Employee serving notice (last day after snapshot)", sum(1 for c in off if c["last_working_date"] >= as_of)),
        ("boundary", "Delayed start", sum(1 for c in onb if c["start_delay_days"] > 0)),
        ("boundary", "Role change (promotion / transfer)", sum(1 for h in t["employee_role_history"] if h["change_reason"] != "HIRE")),
        ("boundary", "Contractor", sum(1 for e in emp if e["employment_type"] == "CONTRACTOR")),
        ("boundary", "Contract end exit", sum(1 for c in off if c["exit_type"] == "CONTRACT_END")),
        ("exception", "Approved early-access override (audited)", sum(1 for x in g if x["override_event_id"])),
        ("exception", "Access request rejected", sum(1 for x in g if x["approval_status"] == "REJECTED")),
        ("exception", "Offer withdrawn (case cancelled)", sum(1 for c in onb if c["case_status"] == "CANCELLED")),
        ("exception", "Urgent termination", sum(1 for c in off if c["is_urgent"])),
        ("exception", "Involuntary exit", sum(1 for c in off if c["exit_type"] == "INVOLUNTARY")),
        ("exception", "Asset reported lost", sum(1 for s in asg if s["lost_reported_date"])),
        ("exception", "Asset retired (end of life)", sum(1 for a in t["asset"] if a["asset_status"] == "RETIRED")),
        ("exception", "Equipment never acknowledged (open past due)",
         sum(1 for x in t["task"] if x["task_code"] == "ONB_EQUIPMENT_ACK" and x["is_overdue"] and not x["completed_date"])),
    ]
    rows += [("failure (injected)", it, n) for it, n in sorted(gt.items())]
    return [{"category": c, "scenario": s, "count": n} for c, s, n in rows]
