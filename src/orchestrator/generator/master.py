"""Stage 1 - master data.

Creates, in order:
  1. organization + role templates: department, role, access_role, role_access_template
  2. workforce: employee (existing staff + 2026 hires with start dates), manager, employee_role_history

Business rules applied here:
  * Existing staff are sized first so every department has a head, large teams have team leads,
    and HR / IT / Facilities have enough workflow partners for the 2026 hiring volume.
  * New hires start on Mondays; offer accepted 14-45 days before start.
  * Delayed starts (start pushed back) and withdrawn offers are decided here as part of
    "generate hires and start dates"; their audit events are written with the case in stage 2.
  * Contractors only take contractor-eligible roles; interns only intern roles;
    contractors and interns always have a contract_end_date.
"""

from __future__ import annotations

from datetime import date, timedelta

from orchestrator import ids, reference as ref
from orchestrator.generator.context import (
    GenContext, allocate, at_time, next_monday, rand_date, weighted,
)

MANAGER_LEVEL_BY_KIND = {"CEO": "EXECUTIVE", "HEAD": "DIRECTOR"}


def build_master(ctx: GenContext) -> None:
    _build_org(ctx)
    _build_workforce(ctx)


# ====================================================================================
# Organization & role templates
# ====================================================================================
def _build_org(ctx: GenContext) -> None:
    t = ctx.tables
    dept_by_name: dict[str, str] = {}
    for i, (name, group, cc, _w) in enumerate(ref.DEPARTMENTS, start=1):
        did = ids.department_id(i)
        dept_by_name[name] = did
        t["department"].append(
            {"department_id": did, "department_name": name, "function_group": group, "cost_center": cc}
        )

    role_meta: dict[str, dict] = {}
    n = 0
    for dept_name, roles in ref.ROLES.items():
        for title, level, kind, laptop, contractor_ok in roles:
            n += 1
            rid = ids.role_id(n)
            role_meta[rid] = {"title": title, "kind": kind, "dept_name": dept_name,
                              "department_id": dept_by_name[dept_name], "contractor_eligible": contractor_ok}
            t["role"].append({
                "role_id": rid, "role_title": title, "department_id": dept_by_name[dept_name],
                "job_level": level, "is_people_manager": kind in ref.MANAGER_KINDS,
                "laptop_class": laptop, "contractor_eligible": contractor_ok,
            })

    ar_by_key: dict[str, str] = {}
    for i, (key, (name, system, priv, approver, contractor_ok)) in enumerate(ref.ACCESS_ROLES.items(), start=1):
        arid = ids.access_role_id(i)
        ar_by_key[key] = arid
        t["access_role"].append({
            "access_role_id": arid, "access_role_name": name, "target_system": system,
            "privilege_level": priv, "approver_type": approver, "contractor_allowed": contractor_ok,
        })

    n = 0
    for rid, meta in role_meta.items():
        entries = [("SSO", True)] + ref.DEPARTMENT_ACCESS[meta["dept_name"]] + ref.ROLE_ACCESS_OVERRIDES.get(meta["title"], [])
        merged: dict[str, bool] = {}
        for key, mandatory in entries:
            merged[key] = merged.get(key, False) or mandatory
        for key, mandatory in merged.items():
            n += 1
            t["role_access_template"].append({
                "template_id": ids.template_id(n), "role_id": rid,
                "access_role_id": ar_by_key[key], "is_mandatory": mandatory,
            })

    ctx.plan.update(dept_by_name=dept_by_name, role_meta=role_meta, ar_by_key=ar_by_key)


# ====================================================================================
# Workforce
# ====================================================================================
def _roles_of(ctx: GenContext, dept_name: str, kinds: set[str], contractor_only: bool = False) -> list[str]:
    return [
        rid for rid, m in ctx.plan["role_meta"].items()
        if m["dept_name"] == dept_name and m["kind"] in kinds and (m["contractor_eligible"] or not contractor_only)
    ]


def _build_workforce(ctx: GenContext) -> None:
    cfg, rng = ctx.cfg, ctx.rng("workforce")
    wf, sc = cfg.workforce, cfg.scenarios
    n_total = cfg.employees
    hire_weights = {name: w for name, _g, _c, w in ref.DEPARTMENTS if w > 0}

    # ---- 1. size the existing organization needed to support the hiring volume
    new_est = n_total - round(n_total * wf["existing_staff_ratio"])
    dept_new_est = allocate(new_est, hire_weights)
    required: list[tuple[str, str]] = [("Executive Office", "CEO")]
    for name, *_ in ref.DEPARTMENTS[1:]:
        required.append((name, "HEAD"))
        if _roles_of(ctx, name, {"LEAD"}):
            required += [(name, "LEAD")] * round(dept_new_est.get(name, 0) / 30)
    required += [("People (HR)", "HRBP")] * max(2, round(new_est / 125))
    required += [("IT & Security", "ITOPS")] * max(2, round(new_est / 100))
    required += [("IT & Security", "SYSADMIN")] * max(1, round(new_est / 350))
    required += [("Facilities & Workplace", "WORKPLACE")] * max(1, round(new_est / 250))

    existing_total = max(round(n_total * wf["existing_staff_ratio"]), len(required))
    extra_ics = allocate(existing_total - len(required), hire_weights)
    for name, k in extra_ics.items():
        required += [(name, "IC")] * k
    new_total = n_total - existing_total
    dept_new = allocate(new_total, hire_weights)

    people: list[dict] = []
    ex_lo, ex_hi = cfg.window("existing_hire_window")
    ex_hi = ex_hi - timedelta(days=45)

    # ---- 2. existing staff (hired before the 2026 window)
    for dept_name, kind in required:
        role = rng.choice(_roles_of(ctx, dept_name, {kind}))
        if kind == "CEO":
            hire = ex_lo
        elif kind == "HEAD":
            hire = rand_date(rng, ex_lo, ex_lo + timedelta(days=3 * 365))
        else:
            hire = rand_date(rng, ex_lo, ex_hi)
        etype = "FULL_TIME"
        if kind == "IC" and ctx.plan["role_meta"][role]["contractor_eligible"]:
            etype = weighted(rng, {"FULL_TIME": wf["employment_type_weights"]["FULL_TIME"],
                                   "CONTRACTOR": wf["employment_type_weights"]["CONTRACTOR"]})
        start = hire + timedelta(days=rng.randint(14, 35))
        people.append({
            "cohort": "EXISTING_STAFF", "kind": kind, "role_id": role, "dept_name": dept_name,
            "employment_type": etype, "hire_date": hire, "original_start": start, "start_date": start,
            "delay_days": 0, "delay_decided_on": None, "withdrawn_on": None,
            "contract_end_date": rand_date(rng, date(2026, 2, 1), date(2027, 6, 30)) if etype == "CONTRACTOR" else None,
        })

    # ---- 3. 2026 new hires: offer accepted inside the window (never after as_of), start on a Monday
    nh_lo, nh_hi = cfg.window("new_hire_offer_window")
    nh_hi = min(nh_hi, ctx.as_of)
    lead_lo, lead_hi = wf["offer_lead_days"]
    for dept_name in hire_weights:
        for _ in range(dept_new[dept_name]):
            etype = weighted(rng, wf["employment_type_weights"])
            if etype == "INTERN" and not _roles_of(ctx, dept_name, {"INTERN"}):
                etype = "FULL_TIME"
            if etype == "CONTRACTOR" and not _roles_of(ctx, dept_name, {"IC"}, contractor_only=True):
                etype = "FULL_TIME"
            if etype == "INTERN":
                role = rng.choice(_roles_of(ctx, dept_name, {"INTERN"}))
            elif etype == "CONTRACTOR":
                role = rng.choice(_roles_of(ctx, dept_name, {"IC"}, contractor_only=True))
            else:
                role = rng.choice(_roles_of(ctx, dept_name, {"IC"}))

            hire = rand_date(rng, nh_lo, nh_hi)
            original = next_monday(hire + timedelta(days=rng.randint(lead_lo, lead_hi)))
            start, delay, decided, withdrawn = original, 0, None, None
            # Withdrawals / start changes only exist if they happened on or before the as-of snapshot.
            if rng.random() < sc["offer_withdrawn_rate"]:
                when = rand_date(rng, hire + timedelta(days=1), original - timedelta(days=1))
                withdrawn = when if when <= ctx.as_of else None
            elif rng.random() < sc["delayed_start_rate"]:
                new_start = next_monday(original + timedelta(days=rng.randint(*sc["delayed_start_days"])))
                when = rand_date(rng, hire + timedelta(days=1), original - timedelta(days=1))
                if when <= ctx.as_of:
                    start, decided = new_start, when
                    delay = (start - original).days

            contract_end = None
            if etype == "CONTRACTOR":
                contract_end = start + timedelta(days=rng.randint(*wf["contract_length_days"]))
            elif etype == "INTERN":
                contract_end = start + timedelta(days=rng.randint(*wf["intern_length_days"]))
            people.append({
                "cohort": "NEW_HIRE", "kind": ctx.plan["role_meta"][role]["kind"], "role_id": role,
                "dept_name": dept_name, "employment_type": etype, "hire_date": hire,
                "original_start": original, "start_date": start, "delay_days": delay,
                "delay_decided_on": decided, "withdrawn_on": withdrawn, "contract_end_date": contract_end,
            })

    # ---- 4. deterministic IDs in hire-date order, names, locations
    people = [p for _, p in sorted(enumerate(people), key=lambda e: (e[1]["hire_date"], e[1]["start_date"], e[0]))]
    used_emails: set[str] = set()
    for i, p in enumerate(people, start=1):
        p["employee_id"] = ids.employee_id(i)
        p["first_name"], p["last_name"] = rng.choice(ref.FIRST_NAMES), rng.choice(ref.LAST_NAMES)
        base = f"{p['first_name']}.{p['last_name']}".lower()
        email, k = f"{base}@example.com", 1
        while email in used_emails:
            k += 1
            email = f"{base}{k}@example.com"
        used_emails.add(email)
        p["work_email"] = email
        if p["dept_name"] == "Facilities & Workplace":
            p["work_location"] = "ONSITE"
        else:
            p["work_location"] = weighted(rng, wf["work_location_weights"])

    # ---- 5. managers (1:1 with the employee holding a people-manager role)
    t, dept_by_name = ctx.tables, ctx.plan["dept_by_name"]
    head_of, leads_of, ceo_mgr = {}, {}, None
    for p in people:
        if p["kind"] not in ref.MANAGER_KINDS:
            continue
        mid = ids.manager_id(ctx.counter("manager").next())
        p["manager_record_id"] = mid
        level = MANAGER_LEVEL_BY_KIND.get(p["kind"]) or (
            "SENIOR_MANAGER" if next(r for r in t["role"] if r["role_id"] == p["role_id"])["job_level"] >= "L5" else "TEAM_LEAD"
        )
        t["manager"].append({
            "manager_id": mid, "employee_id": p["employee_id"], "department_id": dept_by_name[p["dept_name"]],
            "manager_level": level, "effective_from": p["start_date"], "effective_to": None,
            "manager_status": "ACTIVE",
        })
        if p["kind"] == "CEO":
            ceo_mgr = mid
        elif p["kind"] == "HEAD":
            head_of[p["dept_name"]] = mid
        else:
            leads_of.setdefault(p["dept_name"], []).append(mid)

    # ---- 6. reporting lines, employee rows, role history, audit
    for p in people:
        if p["kind"] == "CEO":
            mgr = None
        elif p["kind"] == "HEAD" or p["dept_name"] == "Executive Office":
            mgr = ceo_mgr
        elif p["kind"] == "LEAD":
            mgr = head_of[p["dept_name"]]
        else:
            leads = leads_of.get(p["dept_name"])
            mgr = rng.choice(leads) if leads else head_of[p["dept_name"]]
        p["manager_id"] = mgr

        if p["withdrawn_on"]:
            status = "WITHDRAWN"
        elif p["start_date"] > ctx.as_of:
            status = "PRE_BOARDING"
        else:
            status = "ACTIVE"
        t["employee"].append({
            "employee_id": p["employee_id"], "first_name": p["first_name"], "last_name": p["last_name"],
            "work_email": p["work_email"], "employment_type": p["employment_type"], "cohort": p["cohort"],
            "department_id": dept_by_name[p["dept_name"]], "role_id": p["role_id"], "manager_id": mgr,
            "work_location": p["work_location"], "hire_date": p["hire_date"], "start_date": p["start_date"],
            "contract_end_date": p["contract_end_date"], "termination_date": None, "employment_status": status,
        })
        if not p["withdrawn_on"]:
            t["employee_role_history"].append({
                "role_history_id": ids.role_history_id(ctx.counter("role_history").next()),
                "employee_id": p["employee_id"], "role_id": p["role_id"],
                "department_id": dept_by_name[p["dept_name"]], "manager_id": mgr,
                "effective_from": p["start_date"], "effective_to": None, "change_reason": "HIRE",
            })
        ctx.audit.add(at_time(rng, p["hire_date"], 7, 8), "employee", p["employee_id"], "EMPLOYEE_HIRED", "SYSTEM",
                      to_state="PRE_BOARDING",
                      reason="HRIS baseline record" if p["cohort"] == "EXISTING_STAFF" else "Offer accepted; HRIS record created")
        if p["cohort"] == "EXISTING_STAFF":
            ctx.audit.add(at_time(rng, p["start_date"], 7, 8), "employee", p["employee_id"], "EMPLOYEE_STARTED", "SYSTEM",
                          from_state="PRE_BOARDING", to_state="ACTIVE", reason="HRIS baseline record")

    # Generator-internal plan for later stages (not exported).
    ctx.plan["people"] = {p["employee_id"]: p for p in people}
    ctx.plan["partners"] = {
        kind: [p["employee_id"] for p in people if p["kind"] == kind]
        for kind in ("HRBP", "ITOPS", "SYSADMIN", "WORKPLACE")
    }
