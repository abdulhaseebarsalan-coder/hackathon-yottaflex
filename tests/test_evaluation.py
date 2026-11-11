"""Step 5 - evaluation metrics and reporting."""

import copy
import json
from collections import Counter

import pytest

from orchestrator.config import load_config
from orchestrator.export.csv_writer import write_csv
from orchestrator.export.ground_truth_writer import write_ground_truth
from orchestrator.generator.pipeline import generate
from orchestrator.metrics.evaluation import (
    build_report, detection_section, evaluate_published, render_markdown, write_report,
)
from orchestrator.metrics.kpis import compute_kpis
from orchestrator.validation.consistency import classify, run_consistency_checks
from orchestrator.validation.rules import detect_all, evaluate


def _publish(ctx, out):
    write_csv(ctx.tables, out / "csv")
    if ctx.ground_truth:
        write_ground_truth(ctx.ground_truth, out / "ground_truth",
                           {"seed": ctx.cfg.seed, "scale": ctx.cfg.scale, "as_of_date": ctx.as_of.isoformat(),
                            **ctx.plan["anomaly_summary"]})
    return out


@pytest.fixture(scope="module")
def full_report(full, full_clean):
    return build_report(full.tables, full.ground_truth, full.plan["anomaly_summary"], full_clean.tables, full.as_of,
                        {"scale": "full", "seed": full.cfg.seed, "as_of_date": full.as_of.isoformat(),
                         "employees": 1000, "anomalies_injected": True, "dataset_sha256": "x", "ground_truth_sha256": "y"})


# ------------------------------------------------------------------ metric arithmetic (not hard-coded)
def test_precision_recall_f1_arithmetic():
    gt = [{"issue_type": "A", "detection_key": k, "severity": "HIGH"} for k in ("k1", "k2", "k3", "k4")]
    findings = [{"issue_type": "A", "detection_key": k} for k in ("k1", "k2", "k3", "x9")]  # 3 TP, 1 FP, 1 FN
    m = evaluate(findings, gt)["A"]
    assert (m["true_positive"], m["false_positive"], m["false_negative"]) == (3, 1, 1)
    assert m["precision"] == 0.75 and m["recall"] == 0.75 and m["f1"] == 0.75
    sec = detection_section(findings, gt)
    assert sec["micro"]["f1"] == 0.75 and sec["by_severity"]["HIGH"] == {"expected": 4, "detected": 3, "recall": 0.75}


def test_metrics_drop_when_detector_misses_or_over_flags(full):
    findings = detect_all(full.tables, full.as_of)
    perfect = detection_section(findings, full.ground_truth)
    degraded = [f for f in findings if f["issue_type"] != "TASK_DEPENDENCY_CYCLE"]          # miss a whole type
    degraded += [{"issue_type": "DUPLICATE_ASSET_ASSIGNMENT", "detection_key": "AST-999999"}]  # one false alarm
    worse = detection_section(degraded, full.ground_truth)
    assert perfect["micro"]["f1"] == 1.0
    assert worse["per_type"]["TASK_DEPENDENCY_CYCLE"]["recall"] == 0.0
    assert worse["per_type"]["DUPLICATE_ASSET_ASSIGNMENT"]["precision"] == round(10 / 11, 4)
    assert worse["micro"]["false_negative"] == 10 and worse["micro"]["false_positive"] == 1
    assert worse["micro"]["f1"] < 1.0 and worse["macro"]["f1"] < 1.0


# ------------------------------------------------------------------ validation (brief Step 8)
def test_clean_baseline_is_fully_consistent(full_clean):
    results = run_consistency_checks(full_clean.tables, full_clean.as_of)
    assert {g: len(v) for g, v in results.items()} == {g: 0 for g in results}
    assert detect_all(full_clean.tables, full_clean.as_of) == []


def test_every_violation_in_published_data_is_explained(full):
    results = run_consistency_checks(full.tables, full.as_of)
    assert sum(len(v) for v in results.values()) > 0, "anomalies must be visible to the consistency checks"
    for group, violations in results.items():
        _, unexplained = classify(violations, full.ground_truth)
        assert unexplained == [], (group, unexplained[:3])


def _wrong_rollup(t):            # stored derived total no longer matches its tasks
    t["onboarding_case"][0]["completed_tasks"] += 1


def _completed_never_started(t):  # completion recorded without a start
    t["task"][0]["started_date"] = None


def _duplicate_active_grant(t):   # same entitlement granted twice to one employee
    g = next(g for g in t["access_grant"] if g["grant_status"] == "ACTIVE")
    t["access_grant"].append(dict(g, grant_id=g["grant_id"][:-3] + "999"))


def _broken_transition(t):        # an event claims a prior state the entity never had
    ev = next(e for e in t["audit_event"] if e["event_type"] == "TASK_COMPLETED")
    ev["from_state"] = "BLOCKED"


@pytest.mark.parametrize("corrupt, group", [
    (_wrong_rollup, "arithmetic_reconciliation"),
    (_completed_never_started, "date_ordering"),
    (_duplicate_active_grant, "duplicates"),
    (_broken_transition, "lifecycle_transitions"),
])
def test_consistency_checks_catch_unlabelled_corruption(starter_clean, corrupt, group):
    """Silent corruption (not in ground truth) must surface as an UNEXPLAINED violation."""
    t = copy.deepcopy(starter_clean.tables)
    corrupt(t)
    _, unexplained = classify(run_consistency_checks(t, starter_clean.as_of)[group], [])
    assert unexplained, group


# ------------------------------------------------------------------ report content
def test_report_counts_by_type_and_severity(full, full_report):
    a = full_report["anomalies"]
    assert a["total"] == len(full.ground_truth) == 50
    assert a["by_type"] == dict(Counter(g["issue_type"] for g in full.ground_truth))
    assert a["by_severity"] == {"CRITICAL": 10, "HIGH": 10, "MEDIUM": 20, "LOW": 10}
    assert sum(sum(v.values()) for v in a["by_type_and_severity"].values()) == 50


def test_report_verdict_and_clean_baseline(full_report):
    v = full_report["verdict"]
    assert v["passed"] and v["detector_micro_f1"] == 1.0
    assert v["clean_baseline_detector_findings"] == 0 and v["clean_baseline_consistency_violations"] == 0
    assert full_report["detection"]["clean_baseline"]["findings"] == 0


def test_kpis_reflect_the_injected_anomalies(full, full_clean, full_report):
    n = full.cfg.anomalies_per_type
    clean, pub = compute_kpis(full_clean.tables, full.as_of), compute_kpis(full.tables, full.as_of)
    assert clean["access"]["active_grants_not_reconciled"] == 0
    assert pub["access"]["active_grants_not_reconciled"] == n          # TERMINATED_EMPLOYEE_RETAINS_GRANT
    assert clean["offboarding"]["unrevoked_after_due_count"] == 0
    assert pub["offboarding"]["unrevoked_after_due_count"] == n
    assert pub["offboarding"]["asset_recovery_rate"]["rate"] < clean["offboarding"]["asset_recovery_rate"]["rate"]
    assert pub["access"]["overrides_with_linked_audit_event"]["rate"] == 1.0
    assert pub["offboarding"]["urgent_same_day_rate"]["rate"] == 1.0
    impact = {r["kpi"]: r for r in full_report["kpi_impact_of_anomalies"]}
    assert impact["Active grants NOT reconciled"]["delta"] == n


def test_kpi_rates_are_consistent_fractions(full):
    def walk(d):
        for v in d.values():
            if isinstance(v, dict) and {"numerator", "denominator", "rate"} <= set(v):
                assert 0 <= v["numerator"] <= v["denominator"]
                assert v["rate"] is None or v["rate"] == round(v["numerator"] / v["denominator"], 4)
            elif isinstance(v, dict):
                walk(v)
    walk(compute_kpis(full.tables, full.as_of))


def test_scenario_coverage_spans_all_categories(full_report):
    cats = Counter(c["category"] for c in full_report["scenario_coverage"])
    assert set(cats) == {"normal", "boundary", "exception", "failure (injected)"}
    assert all(c["count"] > 0 for c in full_report["scenario_coverage"]), "every listed scenario is present"


def test_markdown_report_is_readable(full_report):
    md = render_markdown(full_report)
    for heading in ("## Verdict", "## Volumes", "## Injected anomalies", "## Detector evaluation",
                    "## Validation (brief Step 8)", "## Impact of anomalies", "## Workflow KPIs", "## Scenario coverage"):
        assert heading in md
    assert "**PASS**" in md and "Micro average" in md and "ACCESS_BEFORE_START_OR_APPROVAL" in md


# ------------------------------------------------------------------ computed from the published files, reproducibly
def test_report_from_published_files_matches_in_memory(tmp_path, starter, starter_clean):
    out = _publish(starter, tmp_path / "starter")
    from_disk = evaluate_published(out, starter.cfg)
    in_memory = build_report(starter.tables, starter.ground_truth, starter.plan["anomaly_summary"],
                             starter_clean.tables, starter.as_of, from_disk["dataset"])
    for key in ("verdict", "anomalies", "detection", "kpis", "kpi_impact_of_anomalies", "scenario_coverage", "volumes"):
        assert json.dumps(from_disk[key], sort_keys=True, default=str) == json.dumps(in_memory[key], sort_keys=True, default=str), key


def test_reports_are_byte_identical_across_runs(tmp_path, starter):
    texts = []
    for run in ("a", "b"):
        out = _publish(starter, tmp_path / run)
        paths = write_report(evaluate_published(out, starter.cfg), out / "reports")
        texts.append([p.read_bytes() for p in paths])
    assert texts[0] == texts[1]


def test_without_ground_truth_the_published_data_fails_evaluation(tmp_path, starter):
    """Labels come only from the separate ground-truth folder: drop it and the anomalies become false positives."""
    out = tmp_path / "no_labels"
    write_csv(starter.tables, out / "csv")
    report = evaluate_published(out, starter.cfg)
    assert report["anomalies"]["total"] == 0
    assert report["detection"]["published_dataset"]["micro"]["false_positive"] == len(starter.ground_truth)
    assert report["verdict"]["unexplained_consistency_violations"] > 0
    assert not report["verdict"]["passed"]


def test_clean_dataset_evaluates_as_pass(tmp_path, starter_clean):
    out = _publish(starter_clean, tmp_path / "clean")
    report = evaluate_published(out, starter_clean.cfg)
    assert report["verdict"]["passed"] and report["anomalies"]["total"] == 0
    assert report["dataset"]["anomalies_injected"] is False
