"""Step 3 - onboarding/offboarding transactions: every business rule holds on the clean baseline."""

from collections import Counter, defaultdict
from datetime import timedelta

import pytest

from orchestrator.config import load_config
from orchestrator.export.csv_writer import format_value
from orchestrator.generator.pipeline import generate
from orchestrator.schema import TABLES


@pytest.fixture(scope="module")
def full(full_clean):
    """Business rules are asserted on the clean baseline; injected anomalies are covered in test_anomalies.py."""
    return full_clean


@pytest.fixture(scope="module")
def t(full):
    return full.tables


def _by(rows, key):
    return {r[key]: r for r in rows}


# ------------------------------------------------------------------ volumes & coverage
def test_baseline_volumes(t):
    # Employees and assets are exact configured volumes; tasks land near the suggested 8,000.
    # Access grants are NOT tuned to the suggested 1,500: they follow the mandatory role templates
    # (documented deviation, see README "Volumes").
    assert len(t["employee"]) == 1000
    assert len(t["asset"]) == 1200
    assert 7600 <= len(t["task"]) <= 8400, len(t["task"])
    assert len(t["access_grant"]) >= 1500


def test_required_scenarios_are_present(full, t):
    onb = t["onboarding_case"]
    off = Counter(c["exit_type"] for c in t["offboarding_case"])
    assert sum(1 for c in onb if c["start_delay_days"] > 0) > 20, "delayed starts"
    assert sum(1 for h in t["employee_role_history"] if h["change_reason"] != "HIRE") > 20, "role changes"
    assert Counter(e["employment_type"] for e in t["employee"])["CONTRACTOR"] > 100, "contractors"
    assert off["URGENT_TERMINATION"] > 5 and off["CONTRACT_END"] > 5 and off["VOLUNTARY"] > 5
    assert sum(1 for c in onb if c["case_status"] == "CANCELLED") > 5, "withdrawn offers"
    assert sum(1 for g in t["access_grant"] if g["override_event_id"]) > 3, "approved exceptions"
    assert Counter(g["grant_status"] for g in t["access_grant"])["REJECTED"] > 5
    assert Counter(a["asset_status"] for a in t["asset"])["LOST"] > 0
    assert Counter(a["asset_status"] for a in t["asset"])["RETIRED"] > 0
    assert len({s["asset_id"] for s in t["asset_assignment"]}) < len(t["asset_assignment"]), "assets re-issued"


# ------------------------------------------------------------------ snapshot consistency
FACT_DATES = {
    "onboarding_case": ["case_created_date", "actual_start_date", "cancelled_date", "case_completed_date"],
    "offboarding_case": ["notice_date", "case_completed_date"],
    "task": ["created_date", "started_date", "completed_date"],
    "access_grant": ["requested_date", "approval_date", "provisioned_date", "revoked_date"],
    "asset": ["purchase_date"],
    "asset_assignment": ["assigned_date", "delivered_date", "acknowledged_date", "returned_date", "lost_reported_date"],
    "employee": ["hire_date", "termination_date"],
}


def test_no_recorded_fact_after_as_of(full, t):
    for table, cols in FACT_DATES.items():
        for r in t[table]:
            for c in cols:
                assert r[c] is None or r[c] <= full.as_of, (table, c, r)
    assert max(e["event_timestamp"] for e in t["audit_event"]).date() <= full.as_of


def test_full_pipeline_is_deterministic():
    def snap(ctx):
        return {tb.name: [[format_value(r.get(f.name), f.type) for f in tb.fields] for r in ctx.tables[tb.name]]
                for tb in TABLES}
    assert snap(generate(load_config("starter"))) == snap(generate(load_config("starter")))


# ------------------------------------------------------------------ task graph
def test_task_ids_belong_to_exactly_one_case(t):
    for x in t["task"]:
        case = x["onboarding_case_id"] or x["offboarding_case_id"]
        assert bool(x["onboarding_case_id"]) != bool(x["offboarding_case_id"])
        assert x["task_id"] == f"TASK-{case}-{x['task_seq']:03d}"


def test_task_graph_is_valid_dag_and_respected(t):
    tasks = _by(t["task"], "task_id")
    edges = defaultdict(list)
    for d in t["task_dependency"]:
        a, b = tasks[d["task_id"]], tasks[d["depends_on_task_id"]]
        assert d["task_id"] != d["depends_on_task_id"]
        assert (a["onboarding_case_id"], a["offboarding_case_id"]) == (b["onboarding_case_id"], b["offboarding_case_id"])
        edges[d["task_id"]].append(d["depends_on_task_id"])
        # a task never starts before its prerequisite is complete
        if a["started_date"]:
            assert b["completed_date"] and b["completed_date"] <= a["started_date"]
    state = {}

    def visit(n):  # DFS cycle detection
        state[n] = 1
        for m in edges[n]:
            assert state.get(m) != 1, f"cycle via {n} -> {m}"
            if m not in state:
                visit(m)
        state[n] = 2
    for n in list(edges):
        if n not in state:
            visit(n)


def test_task_dates_and_status(full, t):
    for x in t["task"]:
        assert x["due_date"] >= x["created_date"]
        if x["started_date"]:
            assert x["started_date"] >= x["created_date"]
        if x["completed_date"]:
            assert x["started_date"] and x["completed_date"] >= x["started_date"]
            assert x["task_status"] == "COMPLETED"


# ------------------------------------------------------------------ access lifecycle
def test_access_provisioned_only_after_approval_and_start(t):
    emp = _by(t["employee"], "employee_id")
    events = _by(t["audit_event"], "event_id")
    for g in t["access_grant"]:
        if g["provisioned_date"] is None:
            continue
        assert g["approval_status"] == "APPROVED" and g["approval_date"] <= g["provisioned_date"]
        if g["provisioned_date"] < emp[g["employee_id"]]["start_date"]:
            ev = events[g["override_event_id"]]
            assert ev["event_type"] == "ACCESS_OVERRIDE_APPROVED" and ev["entity_id"] == g["grant_id"]
        elif g["override_event_id"]:
            assert events[g["override_event_id"]]["entity_id"] == g["grant_id"]


def test_access_lifecycle_aligns_with_employment_status(full, t):
    emp = _by(t["employee"], "employee_id")
    off = _by(t["offboarding_case"], "employee_id")
    for g in t["access_grant"]:
        e = emp[g["employee_id"]]
        if g["grant_status"] != "ACTIVE":
            continue
        assert e["employment_status"] in ("ACTIVE", "PRE_BOARDING"), g
        if e["employment_status"] == "PRE_BOARDING":
            assert g["override_event_id"], "pre-boarding access only via approved exception"
        if e["employee_id"] in off:  # serving notice: still inside the revocation SLA
            assert full.as_of <= off[e["employee_id"]]["revocation_due_date"]
    for c in t["offboarding_case"]:
        if full.as_of > c["revocation_due_date"]:
            assert c["grants_revoked"] == c["grants_to_revoke"], c


def test_active_grants_match_current_role_template(t):
    emp = _by(t["employee"], "employee_id")
    template = {(r["role_id"], r["access_role_id"]) for r in t["role_access_template"]}
    for g in t["access_grant"]:
        if g["grant_status"] == "ACTIVE":
            assert (emp[g["employee_id"]]["role_id"], g["access_role_id"]) in template, g


def test_contractors_never_hold_restricted_access(t):
    emp = _by(t["employee"], "employee_id")
    allowed = {a["access_role_id"]: a["contractor_allowed"] for a in t["access_role"]}
    for g in t["access_grant"]:
        if emp[g["employee_id"]]["employment_type"] != "FULL_TIME":
            assert allowed[g["access_role_id"]], g


def test_grant_ids_sequential_per_employee(t):
    per = defaultdict(list)
    for g in t["access_grant"]:
        per[g["employee_id"]].append(g)
    for emp_id, gs in per.items():
        assert [g["grant_id"] for g in gs] == [f"GRANT-{emp_id}-{i:03d}" for i in range(1, len(gs) + 1)]
        assert [g["requested_date"] for g in gs] == sorted(g["requested_date"] for g in gs)


def test_urgent_terminations_revoke_same_day(t):
    grants = defaultdict(list)
    for g in t["access_grant"]:
        grants[g["offboarding_case_id"]].append(g)
    for c in t["offboarding_case"]:
        if c["is_urgent"]:
            for g in grants[c["offboarding_case_id"]]:
                assert g["revoked_date"] == c["notice_date"]


# ------------------------------------------------------------------ assets
def test_assets_reconcile(full, t):
    assets = _by(t["asset"], "asset_id")
    by_asset = defaultdict(list)
    for s in t["asset_assignment"]:
        by_asset[s["asset_id"]].append(s)
        a = assets[s["asset_id"]]
        assert s["assigned_date"] >= a["purchase_date"]
        if s["delivered_date"]:
            assert s["delivered_date"] >= s["assigned_date"]
        if s["acknowledged_date"]:
            assert s["delivered_date"] and s["acknowledged_date"] >= s["delivered_date"]
        assert not (s["returned_date"] and s["lost_reported_date"])
    for aid, a in assets.items():
        rows = sorted(by_asset.get(aid, []), key=lambda s: s["assigned_date"])
        open_rows = [s for s in rows if not s["returned_date"] and not s["lost_reported_date"]]
        assert len(open_rows) <= 1, f"{aid} assigned twice"
        for prev, nxt in zip(rows, rows[1:]):  # never two holders at once
            assert prev["returned_date"] and prev["returned_date"] < nxt["assigned_date"]
        expected = "ASSIGNED" if open_rows else a["asset_status"]
        assert a["asset_status"] == expected
        if not open_rows and rows:
            assert a["asset_status"] in ("IN_STOCK", "LOST", "RETIRED")
            assert (a["asset_status"] == "LOST") == bool(rows[-1]["lost_reported_date"])


def test_terminated_and_withdrawn_hold_no_unrecovered_assets_past_sla(full, t):
    emp = _by(t["employee"], "employee_id")
    off = _by(t["offboarding_case"], "employee_id")
    for s in t["asset_assignment"]:
        if s["returned_date"] or s["lost_reported_date"]:
            continue
        e = emp[s["employee_id"]]
        if e["employment_status"] == "WITHDRAWN":  # courier return still pending at the snapshot
            assert full.as_of < full.plan["people"][e["employee_id"]]["withdrawn_on"] + timedelta(days=7)
        if e["employment_status"] == "TERMINATED":
            assert full.as_of <= off[e["employee_id"]]["last_working_date"] + timedelta(days=10)


# ------------------------------------------------------------------ cases & derived values
def test_onboarding_case_rollups(full, t):
    tasks = defaultdict(list)
    for x in t["task"]:
        tasks[x["onboarding_case_id"] or x["offboarding_case_id"]].append(x)
    emp = _by(t["employee"], "employee_id")
    for c in t["onboarding_case"]:
        ts = tasks[c["onboarding_case_id"]]
        assert c["total_tasks"] == len(ts)
        assert c["completed_tasks"] == sum(1 for x in ts if x["task_status"] == "COMPLETED")
        assert c["completion_pct"] == round(100 * c["completed_tasks"] / c["total_tasks"], 2)
        assert c["start_delay_days"] == (c["planned_start_date"] - c["original_start_date"]).days
        assert emp[c["employee_id"]]["start_date"] == c["planned_start_date"]
        if c["cancelled_date"]:
            assert c["actual_start_date"] is None and c["case_status"] == "CANCELLED"
        elif c["planned_start_date"] <= full.as_of:
            assert c["actual_start_date"] == c["planned_start_date"]


def test_every_state_change_has_an_audit_event(t):
    ev = defaultdict(set)
    for e in t["audit_event"]:
        ev[(e["entity_type"], e["entity_id"])].add(e["event_type"])
    for x in t["task"]:
        if x["completed_date"]:
            assert "TASK_COMPLETED" in ev[("task", x["task_id"])]
    for g in t["access_grant"]:
        k = ("access_grant", g["grant_id"])
        assert "GRANT_REQUESTED" in ev[k]
        if g["provisioned_date"]:
            assert "GRANT_PROVISIONED" in ev[k]
        if g["revoked_date"]:
            assert "GRANT_REVOKED" in ev[k]
    for s in t["asset_assignment"]:
        if s["acknowledged_date"]:
            assert "ASSET_ACKNOWLEDGED" in ev[("asset_assignment", s["assignment_id"])]
    for c in t["onboarding_case"]:
        if c["start_delay_days"]:
            assert "START_DATE_CHANGED" in ev[("onboarding_case", c["onboarding_case_id"])]
    pk = {tb.name: tb.pk for tb in TABLES}
    keys = {name: {r[pk[name]] for r in t[name]} for name in pk}
    for e in t["audit_event"]:
        assert e["entity_id"] in keys[e["entity_type"]], e
