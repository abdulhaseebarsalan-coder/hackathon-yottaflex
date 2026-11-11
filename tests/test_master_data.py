"""Step 2 - master data: organization, role templates, workforce, managers, role history."""

from datetime import timedelta

import pytest

from orchestrator.config import load_config
from orchestrator.export.csv_writer import format_value
from orchestrator.generator.pipeline import generate
from orchestrator.schema import TABLES, deferred_foreign_keys
from orchestrator.validation.structural import run_structural_checks, summarize


def _snapshot(ctx):
    return {t.name: [[format_value(r.get(f.name), f.type) for f in t.fields] for r in ctx.tables[t.name]] for t in TABLES}


@pytest.mark.parametrize("fixture", ["starter", "full"])
def test_structural_checks_pass(fixture, request):
    summary = summarize(run_structural_checks(request.getfixturevalue(fixture).tables))
    assert summary["checks_failed"] == 0, summary["failures"]


def test_volumes_match_scale(starter, full):
    assert len(starter.tables["employee"]) == 100
    assert len(full.tables["employee"]) == 1000
    custom = generate(load_config("starter", employees=250))
    assert len(custom.tables["employee"]) == 250


def test_same_seed_is_identical_and_other_seed_differs(starter):
    again = generate(load_config("starter"))
    assert _snapshot(again) == _snapshot(starter)
    other = generate(load_config("starter", seed=12345))
    assert _snapshot(other)["employee"] != _snapshot(starter)["employee"]


def test_employee_ids_are_contiguous_and_in_hire_order(full):
    emps = full.tables["employee"]
    assert [e["employee_id"] for e in emps] == [f"EMP-{i:06d}" for i in range(1, len(emps) + 1)]
    assert [e["hire_date"] for e in emps] == sorted(e["hire_date"] for e in emps)


def test_reporting_lines(full):
    t = full.tables
    mgr = {m["manager_id"]: m for m in t["manager"]}
    emp = {e["employee_id"]: e for e in t["employee"]}
    roots = [e for e in t["employee"] if e["manager_id"] is None]
    assert len(roots) == 1, "only the CEO has no manager"
    assert emp[roots[0]["employee_id"]]["role_id"] == "ROLE-001"  # Chief Executive Officer
    for e in t["employee"]:
        if e["manager_id"]:
            assert mgr[e["manager_id"]]["employee_id"] != e["employee_id"], "nobody manages themselves"
    # every department with staff has exactly one head (DIRECTOR) except the executive office
    heads = [m["department_id"] for m in t["manager"] if m["manager_level"] == "DIRECTOR"]
    assert len(heads) == len(set(heads)) == len(t["department"]) - 1


def test_workflow_partners_exist(full):
    partners = full.plan["partners"]
    assert len(partners["HRBP"]) >= 2 and len(partners["ITOPS"]) >= 2
    assert len(partners["SYSADMIN"]) >= 1 and len(partners["WORKPLACE"]) >= 1


def test_contractor_and_intern_rules(full):
    roles = {r["role_id"]: r for r in full.tables["role"]}
    kinds = {rid: m["kind"] for rid, m in full.plan["role_meta"].items()}
    for e in full.tables["employee"]:
        if e["employment_type"] == "FULL_TIME":
            assert e["contract_end_date"] is None
            continue
        assert e["contract_end_date"] and e["contract_end_date"] > e["start_date"]
        if e["employment_type"] == "CONTRACTOR":
            assert roles[e["role_id"]]["contractor_eligible"]
        else:
            assert kinds[e["role_id"]] == "INTERN"


def test_new_hire_dates(full):
    for p in full.plan["people"].values():
        if p["cohort"] != "NEW_HIRE":
            assert p["start_date"].year < 2026
            continue
        assert p["hire_date"] <= full.as_of, "no offer accepted after the as-of snapshot"
        assert p["original_start"].weekday() == 0 and p["start_date"].weekday() == 0
        assert timedelta(days=14) <= p["original_start"] - p["hire_date"] <= timedelta(days=45)
        assert p["delay_days"] == (p["start_date"] - p["original_start"]).days >= 0
        if p["delay_days"]:
            assert p["hire_date"] < p["delay_decided_on"] < p["original_start"]
            assert p["delay_decided_on"] <= full.as_of
        if p["withdrawn_on"]:
            assert p["delay_days"] == 0 and p["hire_date"] < p["withdrawn_on"] < p["original_start"]
            assert p["withdrawn_on"] <= full.as_of


def test_role_history_matches_current_role(full):
    hist = {h["employee_id"]: h for h in full.tables["employee_role_history"]}
    for e in full.tables["employee"]:
        if e["employment_status"] == "WITHDRAWN":
            assert e["employee_id"] not in hist
        else:
            h = hist[e["employee_id"]]  # latest row per employee (dict keeps the last one)
            assert (h["role_id"], h["department_id"], h["manager_id"]) == (e["role_id"], e["department_id"], e["manager_id"])


def test_role_templates(full):
    t = full.tables
    # Corporate SSO is a mandatory, approval-gated entitlement for every role.
    sso = next(a["access_role_id"] for a in t["access_role"] if a["access_role_name"] == "Corporate SSO & Email")
    mandatory_sso = {r["role_id"] for r in t["role_access_template"] if r["access_role_id"] == sso and r["is_mandatory"]}
    assert mandatory_sso == {r["role_id"] for r in t["role"]}
    titles = {r["role_id"]: r["role_title"] for r in t["role"]}
    mandatory = {titles[r["role_id"]] for r in t["role_access_template"] if r["is_mandatory"]}
    assert {"Software Engineer", "Account Executive", "Accountant", "HR Business Partner"} <= mandatory
    for a in t["access_role"]:
        if a["privilege_level"] == "PRIVILEGED":
            assert not a["contractor_allowed"]


def test_audit_trail_for_every_hire(full):
    hired = {e["entity_id"] for e in full.tables["audit_event"] if e["event_type"] == "EMPLOYEE_HIRED"}
    assert hired == {e["employee_id"] for e in full.tables["employee"]}


def test_deferred_foreign_keys_are_exactly_the_forward_references():
    assert set(deferred_foreign_keys()) == {
        ("employee", "manager_id", "manager", "manager_id"),
        ("access_grant", "override_event_id", "audit_event", "event_id"),
    }
