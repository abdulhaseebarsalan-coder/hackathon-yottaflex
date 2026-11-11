"""Stage 2b - onboarding and offboarding cases with their tasks, dependencies and audit trail.

Onboarding (one case per NEW_HIRE, ONB ids in offer-accepted order):
  * Tasks are instantiated from templates; conditional tasks:
      background check    - not for agency contractors (assumption: vetted by the agency)
      privileged training - only if a PRIVILEGED entitlement was requested
      app access          - only if a non-SSO application entitlement was requested
      workspace & badge   - not for REMOTE workers
      equipment ship/ack  - only if the person is issued equipment
  * Delayed start  -> START_DATE_CHANGED event; tasks re-anchor on the new start date.
  * Offer withdrawn -> case CANCELLED; open tasks CANCELLED; nothing after the withdrawal happens.
  * Approved exception: early access before start with an ACCESS_OVERRIDE_APPROVED event per grant.
Offboarding (one case per exit, OFF ids in notice-date order):
  * Urgent termination: confirm + revoke on the notice date (same-day SLA).
  * Standard exits: revoke from the last working date; SLA = last working date + 1 day.
  * A small share of standard exits revoke late (SLA breach, resolved before the snapshot).

Audit timing: task events carry the scheduler's timestamps; case-level events are placed so the
trail reads causally (case created 07:59 before any task starts at >= 08:00; case completed one
minute after its last task; withdrawal / termination at end of day).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from orchestrator import ids
from orchestrator.generator.context import GenContext
from orchestrator.generator.tasks import (
    OFFBOARDING_TEMPLATES, ONBOARDING_TEMPLATES, schedule_case,
)

ACTOR_ROLE_BY_TEAM = {"HR": "HR_PARTNER", "IT": "IT_OPS_PARTNER", "FACILITIES": "IT_OPS_PARTNER",
                      "MANAGER": "MANAGER", "EMPLOYEE": "EMPLOYEE"}
LATE_REVOCATION_RATE = 0.06
PRIVILEGED_APP_ACCESS_DUE_DAYS = 9   # training is due start+7, provisioning follows within 2 days
ONE_MIN = timedelta(minutes=1)


def ts(rng, d: date, hour: int) -> datetime:
    """Timestamp within a fixed hour band so same-day events keep their business order."""
    return datetime(d.year, d.month, d.day, hour, rng.randint(0, 59))


def at(d: date, hour: int, minute: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute)


def build_cases(ctx: GenContext) -> None:
    _build_onboarding(ctx)
    _build_offboarding(ctx)


def _emit_task_events(ctx, sched, case_id, cancelled_on=None) -> None:
    for t in sched.tasks:
        code, actor_role, actor = t["task_code"], ACTOR_ROLE_BY_TEAM[t["owner_team"]], t["assignee_employee_id"]
        if t["started_date"]:
            ctx.audit.add(sched.started_ts[code], "task", t["task_id"], "TASK_STARTED", actor_role, actor_id=actor,
                          from_state="READY", to_state="IN_PROGRESS", related_case_id=case_id)
        if t["completed_date"]:
            ctx.audit.add(sched.completed_ts[code], "task", t["task_id"], "TASK_COMPLETED", actor_role, actor_id=actor,
                          from_state="IN_PROGRESS", to_state="COMPLETED", related_case_id=case_id)
        elif cancelled_on:
            ctx.audit.add(at(cancelled_on, 23, 40), "task", t["task_id"], "TASK_CANCELLED", "HR_PARTNER",
                          from_state="IN_PROGRESS" if t["started_date"] else None, to_state="CANCELLED",
                          related_case_id=case_id, reason="Offer withdrawn")


def _case_completion(sched) -> datetime | None:
    """Timestamp of the last task completion, if every task on the case is complete."""
    if sched.tasks and all(t["completed_date"] for t in sched.tasks):
        return max(sched.completed_ts.values())
    return None


# ====================================================================================
def _build_onboarding(ctx: GenContext) -> None:
    cfg, rng, as_of = ctx.cfg, ctx.rng("onboarding"), ctx.as_of
    sc = cfg.scenarios
    partners = ctx.plan["partners"]
    sso = ctx.plan["ar_by_key"]["SSO"]
    mgr_emp = {m["manager_id"]: m["employee_id"] for m in ctx.tables["manager"]}
    counter, dep_counter = ctx.counter("onboarding_case"), ctx.counter("dependency")
    ctx.plan["onb"] = {}

    for emp_id, p in ctx.plan["people"].items():
        if p["cohort"] != "NEW_HIRE":
            continue
        assert p["hire_date"].year == cfg.case_id_year
        case_id = ids.onboarding_case_id(cfg.case_id_year, counter.next())
        hr, it, wp = rng.choice(partners["HRBP"]), rng.choice(partners["ITOPS"]), rng.choice(partners["WORKPLACE"])
        mgr = p["manager_id"]
        start, withdrawn = p["start_date"], p["withdrawn_on"]
        early_roll, ack_roll, early_days = rng.random(), rng.random(), rng.randint(2, 5)
        early_access = not withdrawn and early_roll < sc["approved_early_access_override_rate"]
        never_ack = ack_roll < sc["never_acknowledged_rate"]
        privileged = emp_id in ctx.plan["privileged_at_hire"]

        include = {"ONB_CONTRACT", "ONB_HRIS_RECORD", "ONB_ACCESS_APPROVAL", "ONB_IDENTITY_SSO"}
        if any(ar != sso for ar, _ in ctx.plan["access_at_hire"][emp_id]):
            include.add("ONB_APP_ACCESS")
        if p["employment_type"] != "CONTRACTOR":
            include.add("ONB_BACKGROUND_CHECK")
        if privileged:
            include.add("ONB_PRIVILEGED_TRAINING")
        if p["work_location"] != "REMOTE":
            include.add("ONB_WORKSPACE_BADGE")
        if ctx.plan["equipment"][emp_id]:
            include |= {"ONB_EQUIPMENT_SHIP", "ONB_EQUIPMENT_ACK"}
        templates = [t for t in ONBOARDING_TEMPLATES if t.code in include]
        if privileged:  # provisioning waits for training, so its SLA accounts for the training window
            templates = [replace(t, due=("start", PRIVILEGED_APP_ACCESS_DUE_DAYS)) if t.code == "ONB_APP_ACCESS" else t
                         for t in templates]
        if early_access:  # approved exception: identity + app access may go live a few days before start
            templates = [replace(t, not_before=("start", -early_days)) if t.code in ("ONB_IDENTITY_SSO", "ONB_APP_ACCESS")
                         else t for t in templates]

        sched = schedule_case(
            rng, case_id, "onboarding_case_id", templates,
            {"created": p["hire_date"], "start": start}, as_of,
            {"HR": hr, "IT": it, "FACILITIES": wp, "MANAGER": mgr_emp[mgr], "EMPLOYEE": emp_id},
            dep_counter, cancelled_on=withdrawn,
            never_complete=frozenset({"ONB_EQUIPMENT_ACK"}) if never_ack else frozenset(),
        )
        ctx.tables["task"].extend(sched.tasks)
        ctx.tables["task_dependency"].extend(sched.dependencies)
        actual_start = start if (not withdrawn and start <= as_of) else None
        ctx.tables["onboarding_case"].append({
            "onboarding_case_id": case_id, "employee_id": emp_id, "manager_id": mgr,
            "hr_partner_id": hr, "it_partner_id": it, "case_created_date": p["hire_date"],
            "original_start_date": p["original_start"], "planned_start_date": None,
            "actual_start_date": actual_start, "cancelled_date": withdrawn,
            "start_delay_days": None, "total_tasks": None, "completed_tasks": None, "completion_pct": None,
            "case_status": None, "case_completed_date": None,
        })
        ctx.plan["onb"][emp_id] = {"case_id": case_id, "sched": sched, "manager_id": mgr, "hr": hr, "it": it,
                                   "early_access": early_access}

        # ---- audit trail
        ctx.audit.add(at(p["hire_date"], 7, 59), "onboarding_case", case_id, "CASE_CREATED", "HR_PARTNER",
                      actor_id=hr, to_state="OPEN", related_case_id=case_id, reason="Offer accepted")
        if p["delay_days"]:
            ctx.audit.add(ts(rng, p["delay_decided_on"], 12), "onboarding_case", case_id, "START_DATE_CHANGED",
                          "HR_PARTNER", actor_id=hr, from_state=p["original_start"].isoformat(),
                          to_state=start.isoformat(), related_case_id=case_id,
                          reason=rng.choice(["Candidate notice period extended", "Background check pending",
                                             "Relocation / visa timing", "Hiring manager availability"]))
        if actual_start:
            ctx.audit.add(at(actual_start, 7, 58), "employee", emp_id, "EMPLOYEE_STARTED", "SYSTEM",
                          from_state="PRE_BOARDING", to_state="ACTIVE", related_case_id=case_id)
        _emit_task_events(ctx, sched, case_id, cancelled_on=withdrawn)
        if withdrawn:
            ctx.audit.add(at(withdrawn, 23, 30), "employee", emp_id, "OFFER_WITHDRAWN", "EMPLOYEE", actor_id=emp_id,
                          from_state="PRE_BOARDING", to_state="WITHDRAWN", related_case_id=case_id,
                          reason="Candidate declined before start")
            ctx.audit.add(at(withdrawn, 23, 45), "onboarding_case", case_id, "CASE_CANCELLED", "HR_PARTNER",
                          actor_id=hr, to_state="CANCELLED", related_case_id=case_id, reason="Offer withdrawn")
        done = _case_completion(sched)
        if done and not withdrawn:
            ctx.audit.add(done + ONE_MIN, "onboarding_case", case_id, "CASE_COMPLETED", "SYSTEM",
                          from_state="IN_PROGRESS", to_state="COMPLETED", related_case_id=case_id)


# ====================================================================================
def manager_at(ctx: GenContext, emp_id: str, on: date) -> str | None:
    rows = [h for h in ctx.tables["employee_role_history"] if h["employee_id"] == emp_id and h["effective_from"] <= on]
    return max(rows, key=lambda h: h["effective_from"])["manager_id"] if rows else None


def _build_offboarding(ctx: GenContext) -> None:
    cfg, rng, as_of = ctx.cfg, ctx.rng("offboarding"), ctx.as_of
    partners = ctx.plan["partners"]
    mgr_emp = {m["manager_id"]: m["employee_id"] for m in ctx.tables["manager"]}
    counter, dep_counter = ctx.counter("offboarding_case"), ctx.counter("dependency")
    ctx.plan["off"] = {}

    for emp_id, ex in sorted(ctx.plan["exits"].items(), key=lambda kv: (kv[1]["notice_date"], kv[0])):
        p = ctx.plan["people"][emp_id]
        notice, lwd, etype = ex["notice_date"], ex["last_working_date"], ex["exit_type"]
        assert notice.year == cfg.case_id_year
        case_id = ids.offboarding_case_id(cfg.case_id_year, counter.next())
        hr, it, wp = rng.choice(partners["HRBP"]), rng.choice(partners["ITOPS"]), rng.choice(partners["WORKPLACE"])
        mgr = manager_at(ctx, emp_id, notice)
        urgent = etype == "URGENT_TERMINATION"
        revocation_due = notice if urgent else lwd + timedelta(days=1)
        late_roll = rng.random()

        include = {"OFF_CONFIRM_EXIT", "OFF_REVOKE_ACCESS", "OFF_ACCESS_RECONCILIATION"}
        if ctx.plan["equipment"][emp_id]:
            include.add("OFF_RECOVER_EQUIPMENT")
        if p["work_location"] != "REMOTE":
            include.add("OFF_DEACTIVATE_BADGE")
        if p["employment_type"] == "FULL_TIME":
            include.add("OFF_FINAL_PAY_EXIT_INTERVIEW")
        templates = [t for t in OFFBOARDING_TEMPLATES if t.code in include]
        # Exit confirmation never lands after the last working date.
        overrides = {"OFF_CONFIRM_EXIT": (0, max(0, min(2, (lwd - notice).days)))}
        if urgent:
            templates = [replace(t, not_before=("notice", 0)) if t.code == "OFF_REVOKE_ACCESS" else t for t in templates]
            overrides["OFF_REVOKE_ACCESS"] = (0, 0)
        elif late_roll < LATE_REVOCATION_RATE and lwd + timedelta(days=5) <= as_of:
            overrides["OFF_REVOKE_ACCESS"] = (2, 4)  # SLA breach, resolved before the snapshot

        sched = schedule_case(
            rng, case_id, "offboarding_case_id", templates,
            {"created": notice, "notice": notice, "lwd": lwd, "revocation_due": revocation_due}, as_of,
            {"HR": hr, "IT": it, "FACILITIES": wp, "MANAGER": mgr_emp[mgr], "EMPLOYEE": emp_id},
            dep_counter, duration_override=overrides,
        )
        ctx.tables["task"].extend(sched.tasks)
        ctx.tables["task_dependency"].extend(sched.dependencies)
        ctx.tables["offboarding_case"].append({
            "offboarding_case_id": case_id, "employee_id": emp_id, "initiated_by_manager_id": mgr,
            "hr_partner_id": hr, "it_partner_id": it, "exit_type": etype, "is_urgent": None,
            "notice_date": notice, "last_working_date": lwd, "revocation_due_date": None,
            "grants_to_revoke": None, "grants_revoked": None, "assets_to_recover": None, "assets_recovered": None,
            "case_status": None, "case_completed_date": None,
        })
        ctx.plan["off"][emp_id] = {"case_id": case_id, "sched": sched, "exit_type": etype}

        ctx.audit.add(at(notice, 7, 59), "offboarding_case", case_id, "CASE_CREATED", "MANAGER",
                      actor_id=mgr_emp[mgr], to_state="OPEN", related_case_id=case_id,
                      reason=f"Exit notified: {etype}")
        _emit_task_events(ctx, sched, case_id)
        if lwd < as_of:
            ctx.audit.add(at(lwd, 23, 55), "employee", emp_id, "EMPLOYEE_TERMINATED", "HR_PARTNER", actor_id=hr,
                          from_state="ACTIVE", to_state="TERMINATED", related_case_id=case_id, reason=etype)
        done = _case_completion(sched)
        if done:
            ctx.audit.add(done + ONE_MIN, "offboarding_case", case_id, "CASE_COMPLETED", "SYSTEM",
                          from_state="IN_PROGRESS", to_state="COMPLETED", related_case_id=case_id)
