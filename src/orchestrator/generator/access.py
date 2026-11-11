"""Stage 2c - access grants (request -> approval -> provisioning -> revocation).

Rules (all dates come from the owning case's tasks, so grants and tasks always agree):
  * Every role template includes Corporate SSO & Email (mandatory) plus role/department apps.
  * New hire: requested by the case HR partner when the HRIS record is complete; approved/rejected
    by the hiring manager when ONB_ACCESS_APPROVAL completes; SSO provisioned by ONB_IDENTITY_SSO,
    applications by ONB_APP_ACCESS. Both tasks are gated on approval AND the start date
    -> "provision access only after approvals".
  * Segregation of duties: the requester (HR partner / system feed) is never the approver (manager).
  * MANAGER_AND_SYSTEM_OWNER entitlements carry a second approval by a system administrator.
  * Only optional entitlements can be rejected.
  * Approved exception: early access (before start) linked to an ACCESS_OVERRIDE_APPROVED event.
  * Offer withdrawn: open requests are CANCELLED (never provisioned).
  * Role change: entitlements not in the new role template are revoked (ROLE_CHANGE); new ones are
    requested by HR, approved by the new manager and provisioned.
  * Exit: every active grant is revoked when OFF_REVOKE_ACCESS completes (TERMINATION or
    CONTRACT_END). If that has not happened by as_of the employee is still serving notice /
    inside the revocation SLA and the grant legitimately stays ACTIVE.
  * GRANT ids are numbered per employee in request order: GRANT-<employee-id>-NNN.

Audit timing: grant events triggered by a task are stamped relative to that task's timestamps
(request 1 min after the HRIS task completes, approval 1-2 min after the approval task completes,
provisioning / revocation 1 min before the provisioning / revocation task closes).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from orchestrator import ids
from orchestrator.generator.cases import ONE_MIN, ts
from orchestrator.generator.context import GenContext
from orchestrator.generator.lifecycle import requested_access


class _GrantWriter:
    def __init__(self, ctx: GenContext, rng) -> None:
        self.ctx, self.rng = ctx, rng
        self.approver_type = {a["access_role_id"]: a["approver_type"] for a in ctx.tables["access_role"]}
        self.mgr_emp = {m["manager_id"]: m["employee_id"] for m in ctx.tables["manager"]}
        self.sysadmins = ctx.plan["partners"]["SYSADMIN"]
        it_dept = ctx.plan["dept_by_name"]["IT & Security"]
        self.it_head = next(m["employee_id"] for m in ctx.tables["manager"]
                            if m["department_id"] == it_dept and m["manager_level"] == "DIRECTOR")

    def system_owner(self, grantee: str) -> str:
        """A system administrator other than the grantee (IT Director if no other admin exists)."""
        others = [s for s in self.sysadmins if s != grantee]
        return self.rng.choice(others) if others else self.it_head

    def add(self, emp_id: str, ar: str, reason: str, case_id: str | None,
            requested: date, requested_at: datetime, requester: str | None,
            approval_status: str, approver: str | None, approval_date: date | None, approved_at: datetime | None,
            provisioned: date | None, provisioned_at: datetime | None, it_actor: str | None,
            override_ref: str | None = None) -> dict:
        ctx, rng, audit = self.ctx, self.rng, self.ctx.audit
        gid = ids.grant_id(emp_id, ctx.counter("grant").next(emp_id))
        g = {
            "grant_id": gid, "employee_id": emp_id, "access_role_id": ar, "onboarding_case_id": case_id,
            "offboarding_case_id": None, "request_reason": reason, "requested_date": requested,
            "approval_status": approval_status, "approved_by_manager_id": approver if approval_date else None,
            "approval_date": approval_date, "provisioned_date": provisioned, "override_event_id": override_ref,
            "revoked_date": None, "revocation_reason": None, "grant_status": None,
        }
        ctx.tables["access_grant"].append(g)
        audit.add(requested_at, "access_grant", gid, "GRANT_REQUESTED", "HR_PARTNER" if requester else "SYSTEM",
                  actor_id=requester, to_state="REQUESTED", related_case_id=case_id, reason=reason)
        if approval_date:
            approver_emp = self.mgr_emp.get(approver) if approver else None
            if approval_status == "REJECTED":
                audit.add(approved_at, "access_grant", gid, "GRANT_REJECTED", "MANAGER", actor_id=approver_emp,
                          from_state="REQUESTED", to_state="REJECTED", related_case_id=case_id,
                          reason="Not required for role")
            else:
                # The CEO has no line manager: a system administrator approves (documented exception).
                audit.add(approved_at, "access_grant", gid, "GRANT_APPROVED", "MANAGER" if approver_emp else "SYSTEM_ADMIN",
                          actor_id=approver_emp or self.system_owner(emp_id), from_state="REQUESTED", to_state="APPROVED",
                          related_case_id=case_id, reason="Line manager approval")
                if self.approver_type[ar] == "MANAGER_AND_SYSTEM_OWNER":
                    audit.add(approved_at + ONE_MIN, "access_grant", gid, "GRANT_APPROVED", "SYSTEM_ADMIN",
                              actor_id=self.system_owner(emp_id), from_state="APPROVED", to_state="APPROVED",
                              related_case_id=case_id, reason="System owner approval")
        if provisioned:
            audit.add(provisioned_at, "access_grant", gid, "GRANT_PROVISIONED", "IT_OPS_PARTNER",
                      actor_id=it_actor, from_state="APPROVED", to_state="ACTIVE", related_case_id=case_id)
        return g

    def revoke(self, g: dict, when: date, at_ts: datetime, reason: str, case_id: str | None, actor: str | None) -> None:
        g["revoked_date"], g["revocation_reason"] = when, reason
        if case_id and case_id.startswith("OFF-"):
            g["offboarding_case_id"] = case_id
        self.ctx.audit.add(at_ts, "access_grant", g["grant_id"], "GRANT_REVOKED", "IT_OPS_PARTNER",
                           actor_id=actor, from_state="ACTIVE", to_state="REVOKED", related_case_id=case_id,
                           reason=reason)


def build_access(ctx: GenContext) -> None:
    rng, sc = ctx.rng("access"), ctx.cfg.scenarios
    w = _GrantWriter(ctx, rng)
    sso = ctx.plan["ar_by_key"]["SSO"]
    hire_mgr = {h["employee_id"]: h["manager_id"] for h in ctx.tables["employee_role_history"] if h["change_reason"] == "HIRE"}
    itops = ctx.plan["partners"]["ITOPS"]

    for emp_id, p in ctx.plan["people"].items():
        grants: list[dict] = []
        requests = ctx.plan["access_at_hire"][emp_id]

        # ---- existing staff: baseline access granted at their (pre-2026) start via the HRIS feed
        if p["cohort"] == "EXISTING_STAFF":
            s = p["start_date"]
            for ar, _mandatory in requests:
                req_d, appr_d = s - timedelta(days=3), s - timedelta(days=2)
                grants.append(w.add(emp_id, ar, "EXISTING_BASELINE", None, req_d, ts(rng, req_d, 9), None,
                                    "APPROVED", hire_mgr[emp_id], appr_d, ts(rng, appr_d, 11),
                                    s, ts(rng, s, 14), rng.choice(itops)))

        # ---- new hires: driven by onboarding tasks
        else:
            onb = ctx.plan["onb"][emp_id]
            sched, case_id = onb["sched"], onb["case_id"]
            requested = sched.done("ONB_HRIS_RECORD")
            approval = sched.done("ONB_ACCESS_APPROVAL")
            for ar, mandatory in requests:
                reject_roll = rng.random()
                if requested is None:
                    continue
                rejected = (not mandatory) and reject_roll < sc["grant_rejection_rate"]
                status = "PENDING" if approval is None else ("REJECTED" if rejected else "APPROVED")
                task = "ONB_IDENTITY_SSO" if ar == sso else "ONB_APP_ACCESS"
                prov = sched.done(task) if status == "APPROVED" else None
                prov_at = sched.completed_ts[task] - ONE_MIN if prov else None
                override_ref = None
                if prov and prov < p["start_date"]:
                    assert onb["early_access"], "only approved exceptions provision before start"
                    override_ref = ctx.audit.add(
                        sched.started_ts[task] + ONE_MIN, "access_grant", "__pending__", "ACCESS_OVERRIDE_APPROVED",
                        "SYSTEM_ADMIN", actor_id=rng.choice(ctx.plan["partners"]["SYSADMIN"]), related_case_id=case_id,
                        reason="Early access approved for mandatory pre-start training")
                g = w.add(emp_id, ar, "NEW_HIRE", case_id,
                          requested, sched.completed_ts["ONB_HRIS_RECORD"] + ONE_MIN, onb["hr"],
                          status, onb["manager_id"], approval,
                          sched.completed_ts["ONB_ACCESS_APPROVAL"] + ONE_MIN if approval else None,
                          prov, prov_at, onb["it"], override_ref=override_ref)
                if override_ref:
                    next(e for e in ctx.audit.events() if e["_ref"] == override_ref)["entity_id"] = g["grant_id"]
                if p["withdrawn_on"] and status in ("PENDING", "APPROVED"):  # REJECTED is already final
                    ctx.audit.add(datetime(*p["withdrawn_on"].timetuple()[:3], 23, 50), "access_grant", g["grant_id"],
                                  "GRANT_CANCELLED", "SYSTEM",
                                  from_state="APPROVED" if status == "APPROVED" else "REQUESTED",
                                  to_state="CANCELLED", related_case_id=case_id, reason="Offer withdrawn")
                grants.append(g)

        # ---- role change: revoke what the new role does not need, request what it adds
        rc = ctx.plan["role_changes"].get(emp_id)
        if rc:
            d = rc["date"]
            active = [g for g in grants if g["provisioned_date"] and g["provisioned_date"] <= d and not g["revoked_date"]]
            new_req = requested_access(ctx, rng, rc["new_role"], p["employment_type"])
            new_ids = {ar for ar, _ in new_req}
            held = {g["access_role_id"] for g in active}
            for g in active:
                if g["access_role_id"] not in new_ids:
                    rd = d + timedelta(days=rng.randint(0, 1))
                    w.revoke(g, rd, ts(rng, rd, 16), "ROLE_CHANGE", None, rng.choice(itops))
            for ar, mandatory in new_req:
                reject_roll = rng.random()
                if ar in held:
                    continue
                appr = d + timedelta(days=rng.randint(0, 2))
                rejected = (not mandatory) and reject_roll < sc["grant_rejection_rate"]
                prov = None if rejected else appr + timedelta(days=rng.randint(0, 1))
                grants.append(w.add(emp_id, ar, "ROLE_CHANGE", None, d, ts(rng, d, 9), rc["hr_actor"],
                                    "REJECTED" if rejected else "APPROVED", rc["new_manager"], appr, ts(rng, appr, 11),
                                    prov, ts(rng, prov, 14) if prov else None, rng.choice(itops)))

        # ---- exit: revoke everything still active when OFF_REVOKE_ACCESS completes
        off = ctx.plan.get("off", {}).get(emp_id)
        if off:
            sched = off["sched"]
            revoke_on = sched.done("OFF_REVOKE_ACCESS")
            if revoke_on:
                reason = "CONTRACT_END" if off["exit_type"] == "CONTRACT_END" else "TERMINATION"
                it_actor = next(t["assignee_employee_id"] for t in sched.tasks if t["task_code"] == "OFF_REVOKE_ACCESS")
                revoke_at = sched.completed_ts["OFF_REVOKE_ACCESS"] - ONE_MIN
                for g in grants:
                    if g["provisioned_date"] and not g["revoked_date"]:
                        assert g["provisioned_date"] <= revoke_on
                        w.revoke(g, revoke_on, revoke_at, reason, off["case_id"], it_actor)
