"""Stage 3 - labelled anomaly injection (runs on the finished, validated baseline).

Five edge cases required by the brief are injected deliberately, a configured number of each
(`anomalies_per_type`). Every injection:
  * is selected with its own seeded RNG stream (deterministic, independent of other stages);
  * touches the minimum set of source rows, and every row it touches is listed in the ground truth
    (no silent corruption of unrelated rows);
  * never re-numbers existing IDs: new rows/events get the next free ID, a removed event leaves a gap;
  * keeps structural integrity (PK/FK/enums) - anomalies break business rules, not the schema;
  * never records a fact after the as-of snapshot.
Derived values are recomputed afterwards, so reconciliation counts reflect the anomalies honestly.

Issue types, rules and expected decisions
-----------------------------------------
ACCESS_BEFORE_START_OR_APPROVAL  RULE-ACCESS-01  grant provisioned before the employee's start date
                                                 (without an approved override) or before approval
DUPLICATE_ASSET_ASSIGNMENT       RULE-ASSET-01   one asset held by two people at the same time
TERMINATED_EMPLOYEE_RETAINS_GRANT RULE-ACCESS-02 terminated employee still holds an active grant
                                                 after the revocation SLA
TASK_DEPENDENCY_CYCLE            RULE-TASK-01    a case's task dependency graph contains a cycle
RECOVERED_ASSET_REMAINS_ASSIGNED RULE-ASSET-02   asset recovered into stock but its assignment is
                                                 still open
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta

from orchestrator import ids
from orchestrator.generator.context import GenContext, rand_date

ISSUE_TYPES = {
    "ACCESS_BEFORE_START_OR_APPROVAL": ("RULE-ACCESS-01", "HIGH"),
    "DUPLICATE_ASSET_ASSIGNMENT": ("RULE-ASSET-01", "MEDIUM"),
    "TERMINATED_EMPLOYEE_RETAINS_GRANT": ("RULE-ACCESS-02", "CRITICAL"),
    "TASK_DEPENDENCY_CYCLE": ("RULE-TASK-01", "MEDIUM"),
    "RECOVERED_ASSET_REMAINS_ASSIGNED": ("RULE-ASSET-02", "LOW"),
}


class _Injector:
    def __init__(self, ctx: GenContext) -> None:
        self.ctx, self.t, self.as_of = ctx, ctx.tables, ctx.as_of
        self.rng = ctx.rng("anomalies")
        self.n = ctx.cfg.anomalies_per_type
        self.used_employees: set[str] = set()
        self.used_assets: set[str] = set()
        self.used_cases: set[str] = set()
        self.records: list[dict] = []
        self.emp = {e["employee_id"]: e for e in self.t["employee"]}
        self.events_by_entity: dict[tuple, list[dict]] = defaultdict(list)
        for e in self.t["audit_event"]:
            self.events_by_entity[(e["entity_type"], e["entity_id"])].append(e)
        self._next = {
            "event": max(int(e["event_id"][4:]) for e in self.t["audit_event"]) + 1,
            "assignment": max(int(s["assignment_id"][4:]) for s in self.t["asset_assignment"]) + 1,
            "dependency": max(int(d["dependency_id"][5:]) for d in self.t["task_dependency"]) + 1,
        }

    # ---------------------------------------------------------------- helpers
    def next_id(self, kind: str) -> str:
        n = self._next[kind]
        self._next[kind] += 1
        return {"event": ids.event_id, "assignment": ids.assignment_id, "dependency": ids.dependency_id}[kind](n)

    def add_event(self, **fields) -> str:
        eid = self.next_id("event")
        row = {"event_id": eid, "from_state": None, "to_state": None, "related_case_id": None, "reason": None,
               "actor_id": None, **fields}
        self.t["audit_event"].append(row)
        return eid

    def record(self, issue_type: str, sub_type: str, entity_type: str, entity_id: str, detection_key: str,
               employee_id: str | None, case_id: str | None, related: list[str], changes: list[str],
               decision: str, description: str) -> None:
        rule_id, severity = ISSUE_TYPES[issue_type]
        self.records.append({
            "issue_type": issue_type, "sub_type": sub_type, "severity": severity, "rule_id": rule_id,
            "entity_type": entity_type, "entity_id": entity_id, "detection_key": detection_key,
            "employee_id": employee_id, "case_id": case_id, "related_ids": related, "field_changes": changes,
            "expected_decision": decision, "description": description,
        })

    def shuffled(self, rows: list[dict], key: str) -> list[dict]:
        rows = sorted(rows, key=lambda r: r[key])
        self.rng.shuffle(rows)
        return rows

    # ---------------------------------------------------------------- 1. access before start / approval
    def access_before_start_or_approval(self) -> None:
        cands = [g for g in self.t["access_grant"]
                 if g["request_reason"] == "NEW_HIRE" and g["grant_status"] == "ACTIVE" and not g["override_event_id"]
                 and self.emp[g["employee_id"]]["employment_status"] == "ACTIVE"]
        done = 0
        for g in self.shuffled(cands, "grant_id"):
            if done == self.n:
                break
            emp_id = g["employee_id"]
            if emp_id in self.used_employees:
                continue
            start = self.emp[emp_id]["start_date"]
            sub = "BEFORE_START" if done % 2 == 0 else "BEFORE_APPROVAL"
            if sub == "BEFORE_START":
                lo, hi = max(g["approval_date"], start - timedelta(days=10)), start - timedelta(days=1)
            else:
                lo, hi = g["requested_date"], g["approval_date"] - timedelta(days=1)
            if lo > hi:
                continue
            old, new = g["provisioned_date"], rand_date(self.rng, lo, hi)
            g["provisioned_date"] = new
            ev = next(e for e in self.events_by_entity[("access_grant", g["grant_id"])] if e["event_type"] == "GRANT_PROVISIONED")
            ev["event_timestamp"] = datetime(new.year, new.month, new.day, 14, self.rng.randint(0, 59))
            self.used_employees.add(emp_id)
            decision = ("SUSPEND_ACCESS_UNTIL_START_AND_RAISE_EXCEPTION" if sub == "BEFORE_START"
                        else "REVOKE_ACCESS_PENDING_APPROVAL_AND_INVESTIGATE")
            self.record("ACCESS_BEFORE_START_OR_APPROVAL", sub, "access_grant", g["grant_id"], g["grant_id"],
                        emp_id, g["onboarding_case_id"], [ev["event_id"]],
                        [f"access_grant.provisioned_date: {old} -> {new}",
                         f"audit_event[{ev['event_id']}].event_timestamp moved to {new}"],
                        decision,
                        f"Provisioned {new} but employee start is {start} and approval is {g['approval_date']}; "
                        f"no ACCESS_OVERRIDE_APPROVED event exists.")
            done += 1

    # ---------------------------------------------------------------- 2. duplicate asset assignment
    def duplicate_asset_assignment(self) -> None:
        holders = defaultdict(list)
        for s in self.t["asset_assignment"]:
            holders[s["asset_id"]].append(s)
        open_rows = [s for s in self.t["asset_assignment"]
                     if not s["returned_date"] and not s["lost_reported_date"] and s["delivered_date"]
                     and self.emp[s["employee_id"]]["employment_status"] == "ACTIVE" and len(holders[s["asset_id"]]) == 1]
        receivers = sorted(e["employee_id"] for e in self.t["employee"]
                           if e["employment_status"] == "ACTIVE" and e["start_date"] <= self.as_of - timedelta(days=30))
        itops = self.ctx.plan["partners"]["ITOPS"]
        done = 0
        for s in self.shuffled(open_rows, "assignment_id"):
            if done == self.n:
                break
            if s["asset_id"] in self.used_assets or s["employee_id"] in self.used_employees:
                continue
            pool = [r for r in receivers if r not in self.used_employees and r != s["employee_id"]]
            other = self.rng.choice(pool)
            lo = max(s["delivered_date"] + timedelta(days=1), self.emp[other]["start_date"], self.as_of - timedelta(days=120))
            hi = self.as_of - timedelta(days=2)
            if lo > hi:
                continue
            when = rand_date(self.rng, lo, hi)
            asg = self.next_id("assignment")
            it = self.rng.choice(itops)
            self.t["asset_assignment"].append({
                "assignment_id": asg, "asset_id": s["asset_id"], "employee_id": other, "onboarding_case_id": None,
                "offboarding_case_id": None, "assigned_date": when, "delivered_date": when + timedelta(days=1),
                "acknowledged_date": None, "returned_date": None, "lost_reported_date": None, "assignment_status": None,
            })
            e1 = self.add_event(event_timestamp=datetime(when.year, when.month, when.day, 10, self.rng.randint(0, 59)),
                                entity_type="asset", entity_id=s["asset_id"], event_type="ASSET_ASSIGNED",
                                actor_id=it, actor_role="IT_OPS_PARTNER", from_state="IN_STOCK", to_state="ASSIGNED",
                                reason=f"Issued to {other} ({asg})")
            d = when + timedelta(days=1)
            e2 = self.add_event(event_timestamp=datetime(d.year, d.month, d.day, 13, self.rng.randint(0, 59)),
                                entity_type="asset_assignment", entity_id=asg, event_type="ASSET_DELIVERED",
                                actor_id=it, actor_role="IT_OPS_PARTNER", from_state="ASSIGNED", to_state="DELIVERED")
            self.used_assets.add(s["asset_id"])
            self.used_employees |= {s["employee_id"], other}
            self.record("DUPLICATE_ASSET_ASSIGNMENT", "OVERLAPPING_OPEN_ASSIGNMENTS", "asset", s["asset_id"],
                        s["asset_id"], other, None, [s["assignment_id"], asg, s["employee_id"], e1, e2],
                        [f"asset_assignment {asg} added for {other} while {s['assignment_id']} ({s['employee_id']}) is open",
                         f"audit_event {e1}, {e2} added"],
                        "RECALL_ASSET_FROM_SECOND_HOLDER_AND_CLOSE_DUPLICATE_ASSIGNMENT",
                        f"{s['asset_id']} issued to {other} on {when} while still held by {s['employee_id']} "
                        f"since {s['assigned_date']}.")
            done += 1

    # ---------------------------------------------------------------- 3. terminated employee retains grant
    def terminated_employee_retains_grant(self) -> None:
        grants_by_case = defaultdict(list)
        for g in self.t["access_grant"]:
            if g["offboarding_case_id"]:
                grants_by_case[g["offboarding_case_id"]].append(g)
        cases = [c for c in self.t["offboarding_case"]
                 if self.emp[c["employee_id"]]["employment_status"] == "TERMINATED"
                 and self.as_of > c["revocation_due_date"] and grants_by_case[c["offboarding_case_id"]]]
        done = 0
        for c in self.shuffled(cases, "offboarding_case_id"):
            if done == self.n:
                break
            if c["employee_id"] in self.used_employees or c["offboarding_case_id"] in self.used_cases:
                continue
            g = self.rng.choice(sorted(grants_by_case[c["offboarding_case_id"]], key=lambda x: x["grant_id"]))
            ev = next(e for e in self.events_by_entity[("access_grant", g["grant_id"])] if e["event_type"] == "GRANT_REVOKED")
            self.t["audit_event"].remove(ev)
            old = (g["revoked_date"], g["revocation_reason"])
            g["revoked_date"] = g["revocation_reason"] = g["offboarding_case_id"] = None
            self.used_employees.add(c["employee_id"])
            self.used_cases.add(c["offboarding_case_id"])
            self.record("TERMINATED_EMPLOYEE_RETAINS_GRANT", c["exit_type"], "access_grant", g["grant_id"],
                        g["grant_id"], c["employee_id"], c["offboarding_case_id"],
                        [c["offboarding_case_id"], ev["event_id"]],
                        [f"access_grant.revoked_date: {old[0]} -> null", f"access_grant.revocation_reason: {old[1]} -> null",
                         f"access_grant.offboarding_case_id: {c['offboarding_case_id']} -> null",
                         f"audit_event {ev['event_id']} (GRANT_REVOKED) removed"],
                        "REVOKE_ACCESS_IMMEDIATELY_AND_REVIEW_ACCESS_LOGS",
                        f"Employee terminated (last working date {c['last_working_date']}, revocation due "
                        f"{c['revocation_due_date']}) but {g['access_role_id']} is still active.")
            done += 1

    # ---------------------------------------------------------------- 4. task dependency cycle
    def task_dependency_cycle(self) -> None:
        deps_by_case = defaultdict(list)
        task_case = {x["task_id"]: x["onboarding_case_id"] for x in self.t["task"] if x["onboarding_case_id"]}
        for d in self.t["task_dependency"]:
            if d["task_id"] in task_case:
                deps_by_case[task_case[d["task_id"]]].append(d)
        cases = [c for c in self.t["onboarding_case"] if c["case_status"] != "CANCELLED" and len(deps_by_case[c["onboarding_case_id"]]) >= 4]
        done = 0
        for c in self.shuffled(cases, "onboarding_case_id"):
            if done == self.n:
                break
            cid = c["onboarding_case_id"]
            if cid in self.used_cases or c["employee_id"] in self.used_employees:
                continue
            dependents = defaultdict(set)              # prerequisite -> tasks that (transitively) need it
            for d in deps_by_case[cid]:
                dependents[d["depends_on_task_id"]].add(d["task_id"])
            def downstream(task: str) -> set[str]:
                out, stack = set(), [task]
                while stack:
                    for nxt in dependents[stack.pop()]:
                        if nxt not in out:
                            out.add(nxt)
                            stack.append(nxt)
                return out
            prereq = self.rng.choice(sorted(dependents))
            later = self.rng.choice(sorted(downstream(prereq)))
            dep_id = self.next_id("dependency")
            self.t["task_dependency"].append({"dependency_id": dep_id, "task_id": prereq, "depends_on_task_id": later,
                                              "dependency_type": "FINISH_TO_START"})
            cycle = sorted({prereq, later} | (downstream(prereq) & self._upstream(deps_by_case[cid], later)))
            self.used_cases.add(cid)
            self.used_employees.add(c["employee_id"])
            self.record("TASK_DEPENDENCY_CYCLE", "BACK_EDGE", "task_dependency", dep_id, cid, c["employee_id"], cid,
                        cycle,
                        [f"task_dependency {dep_id} added: {prereq} depends on {later} ({later} already depends on {prereq})"],
                        f"REMOVE_INVALID_DEPENDENCY_{dep_id}",
                        f"Dependency {dep_id} makes {prereq} wait for its own downstream task {later}; "
                        f"the case task graph is no longer acyclic.")
            done += 1

    @staticmethod
    def _upstream(deps: list[dict], task: str) -> set[str]:
        prereqs = defaultdict(set)
        for d in deps:
            prereqs[d["task_id"]].add(d["depends_on_task_id"])
        out, stack = {task}, [task]
        while stack:
            for p in prereqs[stack.pop()]:
                if p not in out:
                    out.add(p)
                    stack.append(p)
        return out

    # ---------------------------------------------------------------- 5. recovered asset remains assigned
    def recovered_asset_remains_assigned(self) -> None:
        latest = {}
        for s in self.t["asset_assignment"]:
            if s["asset_id"] not in latest or s["assigned_date"] > latest[s["asset_id"]]["assigned_date"]:
                latest[s["asset_id"]] = s
        status = {a["asset_id"]: a["asset_status"] for a in self.t["asset"]}
        # Eligible: the asset's most recent assignment was closed by an exit recovery and the asset has
        # not been re-issued since (still IN_STOCK, or RETIRED after return). Isolation is by asset: the same
        # leaver may also carry a TERMINATED_EMPLOYEE_RETAINS_GRANT anomaly on a different record.
        cands = [s for s in latest.values()
                 if s["returned_date"] and s["offboarding_case_id"] and status[s["asset_id"]] in ("IN_STOCK", "RETIRED")]
        done = 0
        for s in self.shuffled(cands, "assignment_id"):
            if done == self.n:
                break
            if s["asset_id"] in self.used_assets:
                continue
            ret = next(e for e in self.events_by_entity[("asset", s["asset_id"])]
                       if e["event_type"] == "ASSET_RETURNED" and re.search(rf"\b{s['assignment_id']}\b", e["reason"] or ""))
            old = s["returned_date"]
            s["returned_date"] = None
            self.used_assets.add(s["asset_id"])
            self.used_employees.add(s["employee_id"])
            self.record("RECOVERED_ASSET_REMAINS_ASSIGNED", "ASSIGNMENT_NOT_CLOSED", "asset_assignment",
                        s["assignment_id"], s["assignment_id"], s["employee_id"], s["offboarding_case_id"],
                        [s["asset_id"], s["offboarding_case_id"], ret["event_id"]],
                        [f"asset_assignment.returned_date: {old} -> null (ASSET_RETURNED {ret['event_id']} kept)"],
                        "CLOSE_ASSIGNMENT_USING_RECOVERY_EVENT_DATE",
                        f"{s['asset_id']} was recovered on {old} ({ret['event_id']}) and is in stock, but assignment "
                        f"{s['assignment_id']} to {s['employee_id']} is still open.")
            done += 1


def inject_anomalies(ctx: GenContext) -> list[dict]:
    """Inject the five labelled edge cases; returns ground-truth records (also stored on ctx)."""
    inj = _Injector(ctx)
    inj.access_before_start_or_approval()
    inj.duplicate_asset_assignment()
    inj.terminated_employee_retains_grant()
    inj.task_dependency_cycle()
    inj.recovered_asset_remains_assigned()
    for i, r in enumerate(inj.records, start=1):
        r["anomaly_id"] = ids.anomaly_id(i)
    ctx.tables["audit_event"].sort(key=lambda e: e["event_id"])
    ctx.ground_truth = inj.records
    injected = {it: sum(1 for r in inj.records if r["issue_type"] == it) for it in ISSUE_TYPES}
    ctx.plan["anomaly_summary"] = {
        "requested_per_type": inj.n,
        "injected_per_type": injected,
        "shortfall": {it: inj.n - k for it, k in injected.items() if k < inj.n},
        "shortfall_reason": "fewer eligible baseline records than requested at this scale; "
                            "no record is fabricated to fill the quota",
    }
    return inj.records
