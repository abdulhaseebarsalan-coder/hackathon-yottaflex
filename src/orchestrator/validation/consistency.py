"""Cross-record consistency checks required by the brief (Step 8) beyond the schema-level checks:
date ordering, arithmetic reconciliation, lifecycle transitions and duplicate checks.

Each check returns violations {check, entity_type, entity_id, detail}. On the clean baseline every
check must return nothing; on the injected dataset every violation must be *explained* by a
ground-truth record (classify()). An unexplained violation is a generator defect.
"""

from __future__ import annotations

import copy
from collections import Counter, defaultdict
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

from orchestrator.generator.derive import derive_all
from orchestrator.schema import TABLES

Row = dict[str, Any]
RECOVERY_SLA_DAYS = 10


def _v(check: str, entity_type: str, entity_id: str, detail: str) -> Row:
    return {"check": check, "entity_type": entity_type, "entity_id": entity_id, "detail": detail}


# ------------------------------------------------------------------ date ordering
def _order(out, check, etype, eid, pairs):
    """pairs: (label_a, a, label_b, b) meaning a <= b when both are present."""
    for la, a, lb, b in pairs:
        if a is not None and b is not None and a > b:
            out.append(_v(check, etype, eid, f"{la} {a} > {lb} {b}"))


def check_date_ordering(t: dict[str, list[Row]]) -> list[Row]:
    out: list[Row] = []
    for e in t["employee"]:
        _order(out, "employee_dates", "employee", e["employee_id"],
               [("hire_date", e["hire_date"], "start_date", e["start_date"]),
                ("start_date", e["start_date"], "contract_end_date", e["contract_end_date"])])
    for h in t["employee_role_history"]:
        _order(out, "role_history_dates", "employee_role_history", h["role_history_id"],
               [("effective_from", h["effective_from"], "effective_to", h["effective_to"])])
    for c in t["onboarding_case"]:
        _order(out, "onboarding_dates", "onboarding_case", c["onboarding_case_id"],
               [("case_created_date", c["case_created_date"], "original_start_date", c["original_start_date"]),
                ("cancelled_date", c["cancelled_date"], "original_start_date", c["original_start_date"])])
    for c in t["offboarding_case"]:
        _order(out, "offboarding_dates", "offboarding_case", c["offboarding_case_id"],
               [("notice_date", c["notice_date"], "last_working_date", c["last_working_date"])])
    for x in t["task"]:
        _order(out, "task_dates", "task", x["task_id"],
               [("created_date", x["created_date"], "due_date", x["due_date"]),
                ("created_date", x["created_date"], "started_date", x["started_date"]),
                ("started_date", x["started_date"], "completed_date", x["completed_date"])])
        if x["completed_date"] and not x["started_date"]:
            out.append(_v("task_dates", "task", x["task_id"], "completed without started_date"))
    for g in t["access_grant"]:
        _order(out, "grant_dates", "access_grant", g["grant_id"],
               [("requested_date", g["requested_date"], "approval_date", g["approval_date"]),
                ("approval_date", g["approval_date"], "provisioned_date", g["provisioned_date"]),
                ("requested_date", g["requested_date"], "provisioned_date", g["provisioned_date"]),
                ("provisioned_date", g["provisioned_date"], "revoked_date", g["revoked_date"])])
    purchase = {a["asset_id"]: a["purchase_date"] for a in t["asset"]}
    for s in t["asset_assignment"]:
        _order(out, "assignment_dates", "asset_assignment", s["assignment_id"],
               [("purchase_date", purchase[s["asset_id"]], "assigned_date", s["assigned_date"]),
                ("assigned_date", s["assigned_date"], "delivered_date", s["delivered_date"]),
                ("delivered_date", s["delivered_date"], "acknowledged_date", s["acknowledged_date"]),
                ("assigned_date", s["assigned_date"], "returned_date", s["returned_date"])])
    tasks = {x["task_id"]: x for x in t["task"]}
    for d in t["task_dependency"]:
        pre, dep = tasks[d["depends_on_task_id"]], tasks[d["task_id"]]
        if dep["started_date"] and (pre["completed_date"] is None or pre["completed_date"] > dep["started_date"]):
            out.append(_v("dependency_order", "task_dependency", d["dependency_id"],
                          f"{dep['task_id']} started {dep['started_date']} before prerequisite "
                          f"{pre['task_id']} completed ({pre['completed_date']})"))
    return out


# ------------------------------------------------------------------ arithmetic reconciliation
def check_reconciliation(t: dict[str, list[Row]], as_of: date) -> list[Row]:
    out: list[Row] = []
    # 1. every stored derived value equals a fresh re-derivation from the source fields
    recomputed = copy.deepcopy(t)
    derive_all(SimpleNamespace(as_of=as_of, tables=recomputed))
    for tb in TABLES:
        derived = [f.name for f in tb.fields if f.derived]
        for stored, fresh in zip(t[tb.name], recomputed[tb.name]):
            for col in derived:
                if stored[col] != fresh[col]:
                    out.append(_v("derived_value", tb.name, stored[tb.pk], f"{col} stored {stored[col]!r} != {fresh[col]!r}"))

    # 2. inventory: an asset is ASSIGNED exactly when it has an open assignment
    open_by_asset = Counter(s["asset_id"] for s in t["asset_assignment"]
                            if s["returned_date"] is None and s["lost_reported_date"] is None)
    for a in t["asset"]:
        has_open = open_by_asset[a["asset_id"]] > 0
        if has_open != (a["asset_status"] == "ASSIGNED"):
            out.append(_v("inventory_vs_assignments", "asset", a["asset_id"],
                          f"status {a['asset_status']} but {open_by_asset[a['asset_id']]} open assignment(s)"))

    # 3. exits: after the SLA every grant is revoked and every asset is recovered or reported lost
    lost = Counter()
    for s in t["asset_assignment"]:
        if s["lost_reported_date"]:
            lost[s["offboarding_case_id"]] += 1
    for c in t["offboarding_case"]:
        cid = c["offboarding_case_id"]
        if c["grants_revoked"] > c["grants_to_revoke"] or c["assets_recovered"] > c["assets_to_recover"]:
            out.append(_v("exit_counts", "offboarding_case", cid, "recovered/revoked exceeds in-scope count"))
        if as_of > c["revocation_due_date"] and c["grants_revoked"] != c["grants_to_revoke"]:
            out.append(_v("exit_revocation_complete", "offboarding_case", cid,
                          f"{c['grants_to_revoke'] - c['grants_revoked']} grant(s) unrevoked after due {c['revocation_due_date']}"))
        if as_of > c["last_working_date"] + timedelta(days=RECOVERY_SLA_DAYS) and \
                c["assets_recovered"] + lost[cid] != c["assets_to_recover"]:
            out.append(_v("exit_recovery_complete", "offboarding_case", cid,
                          f"{c['assets_to_recover'] - c['assets_recovered'] - lost[cid]} asset(s) unaccounted after recovery SLA"))
    return out


# ------------------------------------------------------------------ lifecycle transitions (audit chains)
INITIAL_STATE = {"access_grant": None, "task": "READY", "asset": None, "asset_assignment": "ASSIGNED"}
EMPLOYEE_STATUS_EVENTS = {"EMPLOYEE_HIRED", "EMPLOYEE_STARTED", "EMPLOYEE_TERMINATED", "OFFER_WITHDRAWN"}


def check_lifecycle_transitions(t: dict[str, list[Row]]) -> list[Row]:
    """Each event's from_state must equal the entity's state after its previous event (chronological)."""
    by_entity = defaultdict(list)
    for e in sorted(t["audit_event"], key=lambda e: (e["event_timestamp"], e["event_id"])):
        by_entity[(e["entity_type"], e["entity_id"])].append(e)
    out: list[Row] = []
    for (etype, eid), evs in sorted(by_entity.items()):
        if etype == "employee":
            evs, state = [e for e in evs if e["event_type"] in EMPLOYEE_STATUS_EVENTS], None
        elif etype in INITIAL_STATE:
            state = INITIAL_STATE[etype]
        else:
            continue
        for e in evs:
            if e["to_state"] is None:
                continue
            if e["from_state"] is not None and e["from_state"] != state:
                out.append(_v("lifecycle_transition", etype, eid,
                              f"{e['event_id']} {e['event_type']} from {e['from_state']} but state was {state}"))
            state = e["to_state"]
    return out


# ------------------------------------------------------------------ duplicates (business keys)
def check_duplicates(t: dict[str, list[Row]]) -> list[Row]:
    out: list[Row] = []
    active = Counter((g["employee_id"], g["access_role_id"]) for g in t["access_grant"] if g["grant_status"] == "ACTIVE")
    for (emp, ar), n in sorted(active.items()):
        if n > 1:
            out.append(_v("duplicate_active_grant", "employee", emp, f"{n} active grants for {ar}"))
    open_by_asset = Counter(s["asset_id"] for s in t["asset_assignment"]
                            if s["returned_date"] is None and s["lost_reported_date"] is None)
    for aid, n in sorted(open_by_asset.items()):
        if n > 1:
            out.append(_v("duplicate_open_assignment", "asset", aid, f"{n} open assignments"))
    for table, key in (("onboarding_case", "employee_id"), ("offboarding_case", "employee_id")):
        for k, n in Counter(r[key] for r in t[table]).items():
            if n > 1:
                out.append(_v(f"duplicate_{table}", "employee", k, f"{n} {table} rows"))
    return out


# ------------------------------------------------------------------ orchestration
def run_consistency_checks(t: dict[str, list[Row]], as_of: date) -> dict[str, list[Row]]:
    return {
        "date_ordering": check_date_ordering(t),
        "arithmetic_reconciliation": check_reconciliation(t, as_of),
        "lifecycle_transitions": check_lifecycle_transitions(t),
        "duplicates": check_duplicates(t),
    }


def ground_truth_references(ground_truth: list[Row]) -> set[str]:
    refs: set[str] = set()
    for r in ground_truth:
        refs |= {r["entity_id"], r["detection_key"], r["case_id"], r["employee_id"], *r["related_ids"]} - {None}
    return refs


def classify(violations: list[Row], ground_truth: list[Row]) -> tuple[list[Row], list[Row]]:
    """Split violations into (explained by a ground-truth record, unexplained)."""
    refs = ground_truth_references(ground_truth)
    explained = [v for v in violations if v["entity_id"] in refs]
    return explained, [v for v in violations if v["entity_id"] not in refs]
