"""Regression tests for the Step 3 review fixes, plus audit-trail relationship and chronology checks.

Fix  | Test(s)
-----|------------------------------------------------------------------------------
SSO  | test_sso_is_a_mandatory_grant_for_every_employee
A    | test_requester_is_never_the_approver
B    | test_asset_returns_are_never_fabricated
C    | test_grant_events_follow_their_triggering_tasks, test_asset_events_follow_their_triggering_tasks
D    | test_every_started_task_has_a_task_started_event, test_task_events_respect_dependencies
6    | test_privileged_app_access_due_date_covers_training
7    | test_asset_purchase_dates_not_before_2017
all  | test_event_dates_match_record_dates, test_entity_lifecycle_chains, test_event_ids_are_chronological
"""

from collections import defaultdict
from datetime import date, timedelta

import pytest


@pytest.fixture(scope="module")
def full(full_clean):
    """Business rules are asserted on the clean baseline; injected anomalies are covered in test_anomalies.py."""
    return full_clean


@pytest.fixture(scope="module")
def t(full):
    return full.tables


@pytest.fixture(scope="module")
def events(t):
    by_entity = defaultdict(list)
    for e in t["audit_event"]:  # audit_event is already in chronological order
        by_entity[(e["entity_type"], e["entity_id"])].append(e)
    return by_entity


@pytest.fixture(scope="module")
def task_ts(t, events):
    """(case_id, task_code) -> {"TASK_STARTED": ts, "TASK_COMPLETED": ts}"""
    out = {}
    for x in t["task"]:
        evs = {e["event_type"]: e["event_timestamp"] for e in events[("task", x["task_id"])]}
        out[(x["onboarding_case_id"] or x["offboarding_case_id"], x["task_code"])] = evs
    return out


def _sso(t):
    return next(a["access_role_id"] for a in t["access_role"] if a["access_role_name"] == "Corporate SSO & Email")


# ------------------------------------------------------------------ SSO restored
def test_sso_is_a_mandatory_grant_for_every_employee(full, t):
    sso = _sso(t)
    sso_grants = defaultdict(list)
    for g in t["access_grant"]:
        if g["access_role_id"] == sso:
            sso_grants[g["employee_id"]].append(g)
    for e in t["employee"]:
        gs = sso_grants[e["employee_id"]]
        status = e["employment_status"]
        if status == "ACTIVE" and e["start_date"] <= full.as_of - timedelta(days=2):
            assert any(g["grant_status"] == "ACTIVE" for g in gs), e["employee_id"]
        if status == "TERMINATED":
            assert gs and all(g["grant_status"] == "REVOKED" for g in gs), e["employee_id"]
        if status == "WITHDRAWN":
            assert all(g["grant_status"] in ("CANCELLED", "REJECTED") for g in gs)


# ------------------------------------------------------------------ Fix A
def test_requester_is_never_the_approver(t, events):
    checked = 0
    for g in t["access_grant"]:
        evs = events[("access_grant", g["grant_id"])]
        req = next(e for e in evs if e["event_type"] == "GRANT_REQUESTED")
        assert req["actor_role"] in ("HR_PARTNER", "SYSTEM")
        for e in evs:
            if e["event_type"] in ("GRANT_APPROVED", "GRANT_REJECTED"):
                assert e["actor_id"] != req["actor_id"] or req["actor_id"] is None, g["grant_id"]
                assert e["actor_id"] != g["employee_id"], "nobody approves their own access"
                checked += 1
    assert checked > 2000


# ------------------------------------------------------------------ Fix B
def test_asset_returns_are_never_fabricated(full, t, events):
    plan = full.plan["people"]
    for s in t["asset_assignment"]:
        withdrawn = plan[s["employee_id"]]["withdrawn_on"]
        if s["returned_date"]:
            assert s["returned_date"] <= full.as_of
            asset_returns = [e for e in events[("asset", s["asset_id"])]
                             if e["event_type"] == "ASSET_RETURNED" and s["assignment_id"] in (e["reason"] or "")]
            assert len(asset_returns) == 1 and asset_returns[0]["event_timestamp"].date() == s["returned_date"]
            if withdrawn:
                assert 2 <= (s["returned_date"] - withdrawn).days <= 7
        elif withdrawn and not s["lost_reported_date"]:
            # still open only because the 2-7 day courier return falls after the snapshot
            assert full.as_of < withdrawn + timedelta(days=7)
            assert not [e for e in events[("asset", s["asset_id"])]
                        if e["event_type"] == "ASSET_RETURNED" and s["assignment_id"] in (e["reason"] or "")]


# ------------------------------------------------------------------ Fix C
def test_grant_events_follow_their_triggering_tasks(t, events, task_ts):
    sso = _sso(t)
    n = 0
    for g in t["access_grant"]:
        evs = {}
        for e in events[("access_grant", g["grant_id"])]:
            evs.setdefault(e["event_type"], e["event_timestamp"])
        if g["request_reason"] == "NEW_HIRE":
            case = g["onboarding_case_id"]
            assert evs["GRANT_REQUESTED"] > task_ts[(case, "ONB_HRIS_RECORD")]["TASK_COMPLETED"]
            if g["approval_date"]:
                decided = evs.get("GRANT_APPROVED") or evs["GRANT_REJECTED"]
                assert decided > task_ts[(case, "ONB_ACCESS_APPROVAL")]["TASK_COMPLETED"]
            if g["provisioned_date"]:
                task = task_ts[(case, "ONB_IDENTITY_SSO" if g["access_role_id"] == sso else "ONB_APP_ACCESS")]
                assert task["TASK_STARTED"] < evs["GRANT_PROVISIONED"] < task["TASK_COMPLETED"]
                assert evs["GRANT_PROVISIONED"] > max(e["event_timestamp"] for e in events[("access_grant", g["grant_id"])]
                                                     if e["event_type"] == "GRANT_APPROVED")
            n += 1
        if g["offboarding_case_id"]:
            task = task_ts[(g["offboarding_case_id"], "OFF_REVOKE_ACCESS")]
            assert task["TASK_STARTED"] < evs["GRANT_REVOKED"] < task["TASK_COMPLETED"]
        if g["override_event_id"]:
            assert evs["ACCESS_OVERRIDE_APPROVED"] < evs["GRANT_PROVISIONED"]
    assert n > 2000


def test_asset_events_follow_their_triggering_tasks(t, events, task_ts):
    for s in t["asset_assignment"]:
        asg = {e["event_type"]: e["event_timestamp"] for e in events[("asset_assignment", s["assignment_id"])]}
        if s["onboarding_case_id"]:
            ship = task_ts[(s["onboarding_case_id"], "ONB_EQUIPMENT_SHIP")]
            assigned = next(e["event_timestamp"] for e in events[("asset", s["asset_id"])]
                            if e["event_type"] == "ASSET_ASSIGNED" and s["assignment_id"] in e["reason"])
            assert ship["TASK_STARTED"] < assigned
            if s["delivered_date"]:
                assert assigned < asg["ASSET_DELIVERED"] < ship["TASK_COMPLETED"]
            if s["acknowledged_date"]:
                ack = task_ts[(s["onboarding_case_id"], "ONB_EQUIPMENT_ACK")]
                assert asg["ASSET_DELIVERED"] < asg["ASSET_ACKNOWLEDGED"] < ack["TASK_COMPLETED"]
        if s["offboarding_case_id"] and (s["returned_date"] or s["lost_reported_date"]):
            rec = task_ts[(s["offboarding_case_id"], "OFF_RECOVER_EQUIPMENT")]
            back = next(e["event_timestamp"] for e in events[("asset", s["asset_id"])]
                        if e["event_type"] in ("ASSET_RETURNED", "ASSET_LOST") and s["assignment_id"] in e["reason"])
            assert rec["TASK_STARTED"] < back < rec["TASK_COMPLETED"]


# ------------------------------------------------------------------ Fix D
def test_every_started_task_has_a_task_started_event(t, events):
    for x in t["task"]:
        evs = events[("task", x["task_id"])]
        started = [e for e in evs if e["event_type"] == "TASK_STARTED"]
        completed = [e for e in evs if e["event_type"] == "TASK_COMPLETED"]
        assert len(started) == (1 if x["started_date"] else 0), x["task_id"]
        assert len(completed) == (1 if x["completed_date"] else 0), x["task_id"]
        if started:
            assert started[0]["event_timestamp"].date() == x["started_date"]
        if completed:
            assert completed[0]["event_timestamp"].date() == x["completed_date"]
            assert started[0]["event_timestamp"] < completed[0]["event_timestamp"]


def test_task_events_respect_dependencies(t, events):
    ts = {x["task_id"]: {e["event_type"]: e["event_timestamp"] for e in events[("task", x["task_id"])]} for x in t["task"]}
    for d in t["task_dependency"]:
        dep_start = ts[d["task_id"]].get("TASK_STARTED")
        if dep_start:
            assert ts[d["depends_on_task_id"]]["TASK_COMPLETED"] < dep_start, d


# ------------------------------------------------------------------ Fix 6 & 7
def test_privileged_app_access_due_date_covers_training(t):
    tasks = defaultdict(dict)
    for x in t["task"]:
        if x["onboarding_case_id"]:
            tasks[x["onboarding_case_id"]][x["task_code"]] = x
    cases = {c["onboarding_case_id"]: c for c in t["onboarding_case"]}
    n = 0
    for case_id, ts in tasks.items():
        if "ONB_PRIVILEGED_TRAINING" in ts and "ONB_APP_ACCESS" in ts:
            n += 1
            assert ts["ONB_APP_ACCESS"]["due_date"] == cases[case_id]["planned_start_date"] + timedelta(days=9)
            assert ts["ONB_APP_ACCESS"]["due_date"] > ts["ONB_PRIVILEGED_TRAINING"]["due_date"]
    assert n > 0


def test_asset_purchase_dates_not_before_2017(t):
    assert min(a["purchase_date"] for a in t["asset"]) >= date(2017, 1, 1)
    first_use = defaultdict(list)
    for s in t["asset_assignment"]:
        first_use[s["asset_id"]].append(s["assigned_date"])
    for a in t["asset"]:
        if first_use[a["asset_id"]]:
            assert a["purchase_date"] <= min(first_use[a["asset_id"]])


# ------------------------------------------------------------------ audit relationships & chronology
def test_event_dates_match_record_dates(t, events):
    checks = [
        ("access_grant", "grant_id", "provisioned_date", "GRANT_PROVISIONED"),
        ("access_grant", "grant_id", "revoked_date", "GRANT_REVOKED"),
        ("access_grant", "grant_id", "requested_date", "GRANT_REQUESTED"),
        ("asset_assignment", "assignment_id", "delivered_date", "ASSET_DELIVERED"),
        ("asset_assignment", "assignment_id", "acknowledged_date", "ASSET_ACKNOWLEDGED"),
    ]
    for table, pk, col, etype in checks:
        for r in t[table]:
            evs = [e for e in events[(table, r[pk])] if e["event_type"] == etype]
            if r[col]:
                assert len(evs) == 1 and evs[0]["event_timestamp"].date() == r[col], (r[pk], col)
            else:
                assert not evs, (r[pk], etype)
    for e in t["employee"]:
        started = [x for x in events[("employee", e["employee_id"])] if x["event_type"] == "EMPLOYEE_STARTED"]
        if e["employment_status"] in ("ACTIVE", "TERMINATED"):
            assert len(started) == 1 and started[0]["event_timestamp"].date() == e["start_date"]


INITIAL_STATE = {"access_grant": None, "task": "READY", "asset": None, "asset_assignment": "ASSIGNED"}
EMPLOYEE_STATUS_EVENTS = {"EMPLOYEE_HIRED", "EMPLOYEE_STARTED", "EMPLOYEE_TERMINATED", "OFFER_WITHDRAWN"}


def test_entity_lifecycle_chains(events):
    """Each event's from_state equals the previous event's to_state for the same entity."""
    for (etype, eid), evs in events.items():
        if etype == "employee":
            evs = [e for e in evs if e["event_type"] in EMPLOYEE_STATUS_EVENTS]
            state = None
        elif etype in INITIAL_STATE:
            state = INITIAL_STATE[etype]
        else:
            continue
        for e in evs:
            if e["to_state"] is None:          # informational events (e.g. override approval)
                continue
            if e["from_state"] is not None:
                assert e["from_state"] == state, (etype, eid, e["event_type"], e["from_state"], state)
            state = e["to_state"]


def test_event_ids_are_chronological(t):
    stamps = [e["event_timestamp"] for e in t["audit_event"]]
    assert stamps == sorted(stamps)
    assert [e["event_id"] for e in t["audit_event"]] == [f"EVT-{i:08d}" for i in range(1, len(stamps) + 1)]


def test_case_events_bracket_their_tasks(t, events):
    for table, pk in (("onboarding_case", "onboarding_case_id"), ("offboarding_case", "offboarding_case_id")):
        task_events = defaultdict(list)
        for e in t["audit_event"]:
            if e["entity_type"] == "task" and e["related_case_id"]:
                task_events[e["related_case_id"]].append(e["event_timestamp"])
        for c in t[table]:
            evs = {e["event_type"]: e["event_timestamp"] for e in events[(table, c[pk])]}
            if task_events[c[pk]]:
                assert evs["CASE_CREATED"] < min(task_events[c[pk]])
            if "CASE_COMPLETED" in evs:
                assert evs["CASE_COMPLETED"] > max(task_events[c[pk]])
                assert evs["CASE_COMPLETED"].date() == c["case_completed_date"]


def test_rejected_grant_stays_rejected_when_offer_is_withdrawn(t, events):
    """Regression: a manager's rejection is final; withdrawal must not re-label it CANCELLED."""
    cancelled = {c["onboarding_case_id"] for c in t["onboarding_case"] if c["cancelled_date"]}
    for g in t["access_grant"]:
        if g["approval_status"] == "REJECTED":
            assert g["grant_status"] == "REJECTED"
            assert "GRANT_CANCELLED" not in {e["event_type"] for e in events[("access_grant", g["grant_id"])]}
        elif g["onboarding_case_id"] in cancelled and not g["provisioned_date"]:
            assert g["grant_status"] == "CANCELLED"


def test_pending_withdrawn_return_stays_open_without_fabricated_event():
    """Regression (fix B), seed 8 contains withdrawals whose courier return falls after the snapshot."""
    from orchestrator.config import load_config
    from orchestrator.generator.pipeline import generate

    ctx = generate(load_config("full", seed=8), inject=False)
    people = ctx.plan["people"]
    pending = [s for s in ctx.tables["asset_assignment"]
               if people[s["employee_id"]]["withdrawn_on"] and not s["returned_date"] and not s["lost_reported_date"]]
    assert pending, "seed 8 is expected to contain a pending return"
    returned_refs = {e["reason"] for e in ctx.tables["audit_event"] if e["event_type"] == "ASSET_RETURNED"}
    for s in pending:
        assert ctx.as_of < people[s["employee_id"]]["withdrawn_on"] + timedelta(days=7)
        assert s["assignment_status"] in ("ASSIGNED", "DELIVERED")
        assert not any(s["assignment_id"] in r for r in returned_refs)
