"""Step 4 - labelled anomaly injection and ground truth."""

import csv
import json
from collections import Counter

import pytest

from orchestrator.config import load_config
from orchestrator.export.csv_writer import format_value
from orchestrator.export.ground_truth_writer import COLUMNS, write_ground_truth
from orchestrator.generator.anomalies import ISSUE_TYPES
from orchestrator.generator.pipeline import generate
from orchestrator.schema import TABLES
from orchestrator.validation.rules import detect_all, evaluate
from orchestrator.validation.structural import run_structural_checks, summarize

REQUIRED_TYPES = {  # the brief's five required edge cases
    "ACCESS_BEFORE_START_OR_APPROVAL", "DUPLICATE_ASSET_ASSIGNMENT", "TERMINATED_EMPLOYEE_RETAINS_GRANT",
    "TASK_DEPENDENCY_CYCLE", "RECOVERED_ASSET_REMAINS_ASSIGNED",
}


def _snapshot(ctx):
    return {t.name: {r[t.pk]: tuple(format_value(r.get(f.name), f.type) for f in t.fields) for r in ctx.tables[t.name]}
            for t in TABLES}


# ------------------------------------------------------------------ coverage
def test_full_scale_injects_every_required_type_exactly(full):
    assert set(ISSUE_TYPES) == REQUIRED_TYPES
    counts = Counter(r["issue_type"] for r in full.ground_truth)
    assert counts == {it: full.cfg.anomalies_per_type for it in REQUIRED_TYPES}
    assert full.plan["anomaly_summary"]["shortfall"] == {}


def test_starter_shortfall_is_reported_not_fabricated(starter):
    s = starter.plan["anomaly_summary"]
    counts = Counter(r["issue_type"] for r in starter.ground_truth)
    assert set(counts) == REQUIRED_TYPES, "every type is still represented at starter scale"
    for it in REQUIRED_TYPES:
        assert counts[it] + s["shortfall"].get(it, 0) == s["requested_per_type"]


def test_access_anomaly_covers_both_sub_types(full):
    subs = {r["sub_type"] for r in full.ground_truth if r["issue_type"] == "ACCESS_BEFORE_START_OR_APPROVAL"}
    assert subs == {"BEFORE_START", "BEFORE_APPROVAL"}


# ------------------------------------------------------------------ detection vs ground truth
@pytest.mark.parametrize("fixture", ["starter_clean", "full_clean"])
def test_detector_finds_nothing_on_clean_baseline(fixture, request):
    ctx = request.getfixturevalue(fixture)
    assert ctx.ground_truth == []
    assert detect_all(ctx.tables, ctx.as_of) == []


@pytest.mark.parametrize("fixture", ["starter", "full"])
def test_detector_matches_ground_truth_exactly(fixture, request):
    ctx = request.getfixturevalue(fixture)
    report = evaluate(detect_all(ctx.tables, ctx.as_of), ctx.ground_truth)
    assert set(report) == REQUIRED_TYPES
    for it, r in report.items():
        assert r["precision"] == 1.0 and r["recall"] == 1.0, (it, r)


def test_approved_exceptions_are_not_flagged(full):
    """Boundary case: early access WITH an approved override is legitimate and must not be detected."""
    overrides = {g["grant_id"] for g in full.tables["access_grant"] if g["override_event_id"]}
    flagged = {f["detection_key"] for f in detect_all(full.tables, full.as_of)}
    assert overrides and not (overrides & flagged)


# ------------------------------------------------------------------ integrity & isolation
@pytest.mark.parametrize("fixture", ["starter", "full"])
def test_structural_integrity_preserved_after_injection(fixture, request):
    summary = summarize(run_structural_checks(request.getfixturevalue(fixture).tables))
    assert summary["checks_failed"] == 0, summary["failures"]


def test_only_labelled_rows_differ_from_clean_baseline(full, full_clean):
    """No silent corruption: every added / removed / changed row is referenced by a ground-truth record."""
    allowed = set()
    for r in full.ground_truth:
        allowed |= {r["entity_id"], r["detection_key"], r["case_id"], *r["related_ids"]} - {None}
    injected, clean = _snapshot(full), _snapshot(full_clean)
    changed = {}
    for table in injected:
        a, b = injected[table], clean[table]
        keys = {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}
        if keys:
            changed[table] = keys
            assert keys <= allowed, (table, sorted(keys - allowed)[:5])
    assert set(changed) <= {"access_grant", "audit_event", "asset_assignment", "task_dependency", "task",
                            "offboarding_case", "asset"}


def test_existing_ids_are_never_renumbered(full, full_clean):
    for t in TABLES:
        clean_ids = [r[t.pk] for r in full_clean.tables[t.name]]
        clean_set = set(clean_ids)
        kept = [r[t.pk] for r in full.tables[t.name] if r[t.pk] in clean_set]
        kept_set = set(kept)
        # baseline rows keep their IDs and relative order; only gaps (removals) or appended rows differ
        assert kept == [i for i in clean_ids if i in kept_set], t.name
    removed = {e["event_id"] for e in full_clean.tables["audit_event"]} - {e["event_id"] for e in full.tables["audit_event"]}
    assert len(removed) == full.cfg.anomalies_per_type  # one GRANT_REVOKED per retained-grant anomaly


def test_injected_facts_respect_the_snapshot(full):
    for g in full.tables["access_grant"]:
        assert g["provisioned_date"] is None or g["provisioned_date"] <= full.as_of
    for s in full.tables["asset_assignment"]:
        for c in ("assigned_date", "delivered_date", "acknowledged_date"):
            assert s[c] is None or s[c] <= full.as_of
    assert max(e["event_timestamp"] for e in full.tables["audit_event"]).date() <= full.as_of


# ------------------------------------------------------------------ ground-truth content & separation
def test_ground_truth_records_are_complete(full):
    ids = [r["anomaly_id"] for r in full.ground_truth]
    assert ids == [f"ANOM-{i:04d}" for i in range(1, len(ids) + 1)]
    for r in full.ground_truth:
        rule_id, severity = ISSUE_TYPES[r["issue_type"]]
        assert (r["rule_id"], r["severity"]) == (rule_id, severity)
        assert r["expected_decision"] and r["description"] and r["field_changes"] and r["related_ids"]


def test_ground_truth_never_leaks_into_input_tables(full):
    label_words = ("anomal", "ground_truth", "issue_type", "is_injected", "label")
    for t in TABLES:
        assert not any(w in c for c in t.columns for w in label_words), t.name
    texts = [str(v) for e in full.tables["audit_event"] for v in (e["reason"], e["event_type"]) if v]
    assert not any(it in x or "anomal" in x.lower() for x in texts for it in ISSUE_TYPES)


def test_ground_truth_files(full, tmp_path):
    paths = write_ground_truth(full.ground_truth, tmp_path, {"seed": full.cfg.seed, **full.plan["anomaly_summary"]})
    rows = list(csv.DictReader(paths[0].open(encoding="utf-8")))
    assert list(rows[0].keys()) == COLUMNS and len(rows) == len(full.ground_truth)
    payload = json.loads(paths[1].read_text(encoding="utf-8"))
    assert payload["anomaly_count"] == len(full.ground_truth) and payload["seed"] == 20261022
    assert rows[0]["related_ids"] == ";".join(full.ground_truth[0]["related_ids"])


# ------------------------------------------------------------------ reproducibility
def test_injection_is_deterministic(starter):
    again = generate(load_config("starter"))
    assert again.ground_truth == starter.ground_truth
    assert _snapshot(again) == _snapshot(starter)
    other = generate(load_config("starter", seed=7))
    assert other.ground_truth != starter.ground_truth
