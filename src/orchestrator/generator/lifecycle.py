"""Stage 2a - lifecycle planning (before any transaction rows are written).

Decides, per employee: exit (type, notice date, last working date), role change, which
entitlements are requested at hire and which equipment kit is issued. Later stages turn these
decisions into cases, tasks, grants, assignments and audit events.

Rules:
  * Workflow actors (managers, HR/IT/Facilities partners, sysadmins) neither exit nor change role,
    so every case keeps valid owners for its whole life.
  * Contractors / interns exit with CONTRACT_END on contract_end_date (if notified by as_of).
  * Other exits: VOLUNTARY (14-30 days notice), INVOLUNTARY (0-7 days), URGENT_TERMINATION
    (notice date == last working date, same-day revocation SLA).
  * An exit only exists in the snapshot if its notice date is on/before as_of. If the last working
    date is after as_of the employee is still ACTIVE (serving notice) and keeps their access.
  * Role changes happen >= 60 days after start and >= 14 days before any exit notice.
  * Contractors/interns never receive entitlements that are not contractor_allowed.
"""

from __future__ import annotations

from datetime import date, timedelta

from orchestrator import ids
from orchestrator.generator.context import GenContext, at_time, rand_date, weighted

WORKFLOW_ACTOR_KINDS = {"CEO", "HEAD", "LEAD", "HRBP", "ITOPS", "SYSADMIN", "WORKPLACE"}
WINDOW_START = date(2026, 1, 1)


def plan_lifecycle(ctx: GenContext) -> None:
    _plan_exits(ctx)
    _plan_role_changes(ctx)
    _plan_access_requests(ctx)
    _plan_equipment(ctx)


# ---------------------------------------------------------------------------------- exits
def _plan_exits(ctx: GenContext) -> None:
    rng, sc, as_of = ctx.rng("exits"), ctx.cfg.scenarios, ctx.as_of
    exits: dict[str, dict] = {}
    for emp_id, p in ctx.plan["people"].items():
        if p["kind"] in WORKFLOW_ACTOR_KINDS or p["withdrawn_on"] or p["start_date"] > as_of:
            continue
        start = p["start_date"]
        roll = rng.random()  # always drawn: keeps the stream stable
        if p["employment_type"] in ("CONTRACTOR", "INTERN") and p["contract_end_date"] <= as_of + timedelta(days=30):
            lwd = p["contract_end_date"]
            notice = lwd - timedelta(days=rng.randint(*sc["notice_days"]))
            notice = max(notice, start + timedelta(days=1), WINDOW_START)
            if notice <= as_of and lwd >= start + timedelta(days=30) and notice <= lwd:
                exits[emp_id] = {"exit_type": "CONTRACT_END", "notice_date": notice, "last_working_date": lwd}
            continue
        if roll >= sc["exit_rate"]:
            continue
        lo = max(start + timedelta(days=45), date(2026, 2, 1))
        hi = as_of + timedelta(days=30)
        if p["contract_end_date"]:
            hi = min(hi, p["contract_end_date"] - timedelta(days=1))
        if lo > hi:
            continue
        lwd = rand_date(rng, lo, hi)
        etype = weighted(rng, sc["exit_type_weights"])
        if etype == "VOLUNTARY":
            notice = lwd - timedelta(days=rng.randint(*sc["notice_days"]))
        elif etype == "INVOLUNTARY":
            notice = lwd - timedelta(days=rng.randint(0, 7))
        else:
            notice = lwd
        notice = max(notice, start + timedelta(days=1), WINDOW_START)
        if notice <= as_of and notice <= lwd:
            exits[emp_id] = {"exit_type": etype, "notice_date": notice, "last_working_date": lwd}
    ctx.plan["exits"] = exits


# ---------------------------------------------------------------------------------- role changes
def _plan_role_changes(ctx: GenContext) -> None:
    rng, as_of = ctx.rng("role_changes"), ctx.as_of
    role_meta, roles = ctx.plan["role_meta"], {r["role_id"]: r for r in ctx.tables["role"]}
    managers_by_dept: dict[str, list[str]] = {}
    for m in ctx.tables["manager"]:
        if m["manager_level"] != "EXECUTIVE":
            managers_by_dept.setdefault(m["department_id"], []).append(m["manager_id"])
    hist_by_emp = {h["employee_id"]: h for h in ctx.tables["employee_role_history"]}
    hrbps = ctx.plan["partners"]["HRBP"]
    changes: dict[str, dict] = {}

    for emp_id, p in ctx.plan["people"].items():
        roll = rng.random()
        if p["kind"] != "IC" or p["withdrawn_on"] or roll >= ctx.cfg.scenarios["role_change_rate"]:
            continue
        lo = max(p["start_date"] + timedelta(days=60), date(2026, 2, 1))
        hi = as_of - timedelta(days=14)
        if emp_id in ctx.plan["exits"]:
            hi = min(hi, ctx.plan["exits"][emp_id]["notice_date"] - timedelta(days=14))
        if lo > hi:
            continue
        when = rand_date(rng, lo, hi)
        cur = role_meta[p["role_id"]]
        contractor = p["employment_type"] == "CONTRACTOR"

        def eligible(rid: str) -> bool:
            m = role_meta[rid]
            return rid != p["role_id"] and m["kind"] == "IC" and (m["contractor_eligible"] or not contractor)

        same = [r for r in role_meta if eligible(r) and role_meta[r]["dept_name"] == cur["dept_name"]]
        higher = [r for r in same if roles[r]["job_level"] > roles[p["role_id"]]["job_level"]]
        other = [r for r in role_meta if eligible(r) and role_meta[r]["dept_name"] != cur["dept_name"]
                 and role_meta[r]["dept_name"] != "Executive Office"]
        if higher and rng.random() < 0.6:
            new_role, reason = rng.choice(higher), "PROMOTION"
        elif same and rng.random() < 0.6:
            new_role, reason = rng.choice(same), "TRANSFER"
        else:
            new_role, reason = rng.choice(other), "TRANSFER"
        new_dept = role_meta[new_role]["department_id"]
        old = hist_by_emp[emp_id]
        new_mgr = old["manager_id"] if new_dept == old["department_id"] else rng.choice(managers_by_dept[new_dept])

        old["effective_to"] = when - timedelta(days=1)
        ctx.tables["employee_role_history"].append({
            "role_history_id": ids.role_history_id(ctx.counter("role_history").next()),
            "employee_id": emp_id, "role_id": new_role, "department_id": new_dept, "manager_id": new_mgr,
            "effective_from": when, "effective_to": None, "change_reason": reason,
        })
        actor = rng.choice(hrbps)
        # Logged at the start of the business day: follow-on access requests come after it.
        ctx.audit.add(at_time(rng, when, 8, 9).replace(minute=rng.randint(0, 30)), "employee", emp_id,
                      "ROLE_CHANGED", "HR_PARTNER", actor_id=actor,
                      from_state=p["role_id"], to_state=new_role,
                      reason=f"{reason.title()}: {cur['title']} -> {role_meta[new_role]['title']}")
        changes[emp_id] = {"date": when, "old_role": p["role_id"], "new_role": new_role,
                           "new_manager": new_mgr, "reason": reason, "hr_actor": actor}
    ctx.plan["role_changes"] = changes


# ---------------------------------------------------------------------------------- access & equipment
def requested_access(ctx: GenContext, rng, role_id: str, employment_type: str) -> list[tuple[str, bool]]:
    """Template entitlements for a role: mandatory always, optional with probability; contractor filter."""
    allowed = {a["access_role_id"]: a["contractor_allowed"] for a in ctx.tables["access_role"]}
    out = []
    for t in ctx.tables["role_access_template"]:
        if t["role_id"] != role_id:
            continue
        roll = rng.random()
        if employment_type != "FULL_TIME" and not allowed[t["access_role_id"]]:
            continue
        if t["is_mandatory"] or roll < ctx.cfg.scenarios["optional_access_request_rate"]:
            out.append((t["access_role_id"], t["is_mandatory"]))
    return out


def _plan_access_requests(ctx: GenContext) -> None:
    rng = ctx.rng("access_requests")
    priv = {a["access_role_id"] for a in ctx.tables["access_role"] if a["privilege_level"] == "PRIVILEGED"}
    ctx.plan["access_at_hire"] = {}
    ctx.plan["privileged_at_hire"] = set()
    for emp_id, p in ctx.plan["people"].items():
        req = requested_access(ctx, rng, p["role_id"], p["employment_type"])
        ctx.plan["access_at_hire"][emp_id] = req
        if any(ar in priv for ar, _ in req):
            ctx.plan["privileged_at_hire"].add(emp_id)


def _plan_equipment(ctx: GenContext) -> None:
    """Equipment kit per person: list of (asset_type, laptop_class or None)."""
    rng = ctx.rng("equipment_plan")
    roles = {r["role_id"]: r for r in ctx.tables["role"]}
    sales_dept = ctx.plan["dept_by_name"]["Sales"]
    kit: dict[str, list[tuple[str, str | None]]] = {}
    for emp_id, p in ctx.plan["people"].items():
        role = roles[p["role_id"]]
        items: list[tuple[str, str | None]] = []
        byod = p["employment_type"] == "CONTRACTOR" and rng.random() < 0.5
        if role["laptop_class"] != "NONE" and not byod:
            items.append(("LAPTOP", role["laptop_class"]))
        if p["work_location"] == "REMOTE" and rng.random() < 0.6:
            items.append(("MONITOR", None))
        if role["is_people_manager"] or (role["department_id"] == sales_dept and rng.random() < 0.4):
            items.append(("PHONE", None))
        if emp_id in ctx.plan["privileged_at_hire"]:
            items.append(("SECURITY_KEY", None))
        kit[emp_id] = items
    ctx.plan["equipment"] = kit
