"""Evaluation report: everything is computed from the published dataset files, the separate ground truth
and the detector. Nothing is hard-coded; the report contains no wall-clock time, so it is reproducible.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from orchestrator.config import GeneratorConfig, load_config
from orchestrator.datasets import fingerprint, load_ground_truth, load_tables
from orchestrator.generator.pipeline import generate
from orchestrator.metrics.kpis import compute_kpis, scenario_coverage
from orchestrator.validation.consistency import classify, run_consistency_checks
from orchestrator.validation.rules import detect_all, evaluate
from orchestrator.validation.structural import run_structural_checks, summarize

SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
REPORT_VERSION = 1


# ------------------------------------------------------------------ building blocks
def validation_section(tables: dict, as_of: date, ground_truth: list[dict]) -> dict[str, Any]:
    results = run_structural_checks(tables)
    s = summarize(results)
    by_check = Counter(r.check for r in results)
    consistency = {}
    for group, violations in run_consistency_checks(tables, as_of).items():
        explained, unexplained = classify(violations, ground_truth)
        consistency[group] = {
            "violations": len(violations), "explained_by_ground_truth": len(explained), "unexplained": len(unexplained),
            "by_check": dict(sorted(Counter(v["check"] for v in violations).items())),
            "unexplained_examples": unexplained[:5],
        }
    return {
        "structural": {"checks_run": s["checks_run"], "checks_passed": s["checks_passed"],
                       "checks_failed": s["checks_failed"], "checks_by_type": dict(sorted(by_check.items())),
                       "failures": s["failures"]},
        "consistency": consistency,
    }


def detection_section(findings: list[dict], ground_truth: list[dict]) -> dict[str, Any]:
    per_type = evaluate(findings, ground_truth)
    tp = sum(r["true_positive"] for r in per_type.values())
    fp = sum(r["false_positive"] for r in per_type.values())
    fn = sum(r["false_negative"] for r in per_type.values())
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    micro = {"true_positive": tp, "false_positive": fp, "false_negative": fn, "precision": round(p, 4),
             "recall": round(r, 4), "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0}
    n = len(per_type) or 1
    macro = {k: round(sum(v[k] for v in per_type.values()) / n, 4) for k in ("precision", "recall", "f1")}
    found = {(f["issue_type"], f["detection_key"]) for f in findings}
    by_sev = {}
    for sev in SEVERITY_ORDER:
        recs = [g for g in ground_truth if g["severity"] == sev]
        if recs:
            hit = sum(1 for g in recs if (g["issue_type"], g["detection_key"]) in found)
            by_sev[sev] = {"expected": len(recs), "detected": hit, "recall": round(hit / len(recs), 4)}
    return {"findings": len(findings), "per_type": per_type, "micro": micro, "macro": macro, "by_severity": by_sev}


def anomaly_section(ground_truth: list[dict], gt_meta: dict) -> dict[str, Any]:
    by_ts: dict[str, dict[str, int]] = {}
    for g in ground_truth:
        by_ts.setdefault(g["issue_type"], {}).setdefault(g["severity"], 0)
        by_ts[g["issue_type"]][g["severity"]] += 1
    return {
        "total": len(ground_truth),
        "by_type": dict(sorted(Counter(g["issue_type"] for g in ground_truth).items())),
        "by_severity": {s: n for s in SEVERITY_ORDER if (n := sum(1 for g in ground_truth if g["severity"] == s))},
        "by_type_and_severity": dict(sorted(by_ts.items())),
        "by_sub_type": dict(sorted(Counter(f"{g['issue_type']}/{g['sub_type']}" for g in ground_truth).items())),
        "requested_per_type": gt_meta.get("requested_per_type"),
        "shortfall": gt_meta.get("shortfall", {}),
    }


IMPACT_KPIS = [  # (label, section, key, field)
    ("Active grants reconciled to active employment", "access", "active_grants_reconciled_to_active_employment", "rate"),
    ("Active grants NOT reconciled", "access", "active_grants_not_reconciled", None),
    ("Exits revoked within SLA", "offboarding", "revocation_sla_rate", "rate"),
    ("Exits with grants unrevoked after due date", "offboarding", "unrevoked_after_due_count", None),
    ("Exit asset recovery rate", "offboarding", "asset_recovery_rate", "rate"),
    ("Asset utilization (ASSIGNED / total)", "assets", "utilization_rate", "rate"),
    ("Day-one readiness", "onboarding", "day_one_readiness_rate", "rate"),
]


def _kpi(k: dict, section: str, key: str, field: str | None):
    v = k[section][key]
    return v[field] if field else v


# ------------------------------------------------------------------ report
def build_report(tables: dict, ground_truth: list[dict], gt_meta: dict, clean_tables: dict, as_of: date,
                 dataset_meta: dict, volume_targets: dict | None = None) -> dict[str, Any]:
    findings = detect_all(tables, as_of)
    clean_findings = detect_all(clean_tables, as_of)
    validation = {"published_dataset": validation_section(tables, as_of, ground_truth),
                  "clean_baseline": validation_section(clean_tables, as_of, [])}
    detection = {"published_dataset": detection_section(findings, ground_truth),
                 "clean_baseline": {"findings": len(clean_findings),
                                    "false_positives_by_type": dict(Counter(f["issue_type"] for f in clean_findings)),
                                    "examples": clean_findings[:5]}}
    kpis = {"published_dataset": compute_kpis(tables, as_of), "clean_baseline": compute_kpis(clean_tables, as_of)}
    impact = []
    for label, sec, key, field in IMPACT_KPIS:
        c, p = _kpi(kpis["clean_baseline"], sec, key, field), _kpi(kpis["published_dataset"], sec, key, field)
        impact.append({"kpi": label, "clean_baseline": c, "published_dataset": p,
                       "delta": round(p - c, 4) if isinstance(p, (int, float)) and isinstance(c, (int, float)) else None})
    volumes = {name: len(rows) for name, rows in tables.items()}
    unexplained = sum(g["unexplained"] for v in validation.values() for g in v["consistency"].values())
    verdict = {
        "structural_checks_all_passed": all(v["structural"]["checks_failed"] == 0 for v in validation.values()),
        "clean_baseline_consistency_violations": sum(g["violations"] for g in validation["clean_baseline"]["consistency"].values()),
        "unexplained_consistency_violations": unexplained,
        "clean_baseline_detector_findings": len(clean_findings),
        "detector_micro_f1": detection["published_dataset"]["micro"]["f1"],
    }
    verdict["passed"] = (verdict["structural_checks_all_passed"] and verdict["unexplained_consistency_violations"] == 0
                         and verdict["clean_baseline_consistency_violations"] == 0
                         and verdict["clean_baseline_detector_findings"] == 0 and verdict["detector_micro_f1"] == 1.0)
    return {
        "report_version": REPORT_VERSION,
        "dataset": dataset_meta,
        "verdict": verdict,
        "volumes": volumes,
        "volume_targets": volume_targets or {},
        "anomalies": anomaly_section(ground_truth, gt_meta),
        "detection": detection,
        "validation": validation,
        "kpis": kpis,
        "kpi_impact_of_anomalies": impact,
        "scenario_coverage": scenario_coverage(tables, as_of, ground_truth),
    }


def evaluate_published(out_dir: Path, cfg: GeneratorConfig) -> dict[str, Any]:
    """Evaluate the files in out_dir (csv/ + ground_truth/) against a clean baseline regenerated in memory."""
    out_dir = Path(out_dir)
    tables = load_tables(out_dir / "csv")
    ground_truth, gt_meta = load_ground_truth(out_dir / "ground_truth")
    clean = generate(cfg, inject=False).tables
    csv_files = sorted((out_dir / "csv").glob("*.csv"))
    gt_files = sorted((out_dir / "ground_truth").glob("anomalies.*")) if ground_truth else []
    meta = {"scale": cfg.scale, "seed": cfg.seed, "as_of_date": cfg.as_of_date.isoformat(), "employees": cfg.employees,
            "anomalies_injected": bool(ground_truth), "dataset_sha256": fingerprint(csv_files),
            "ground_truth_sha256": fingerprint(gt_files) if gt_files else None}
    targets = None
    if cfg.scale == "full" and cfg.employees == 1000:
        raw = json.loads((Path(__file__).resolve().parents[3] / "config" / "settings.json").read_text(encoding="utf-8"))
        targets = raw["volume_targets_full"]
    return build_report(tables, ground_truth, gt_meta, clean, cfg.as_of_date, meta, targets)


def write_report(report: dict[str, Any], reports_dir: Path) -> list[Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    js, md = reports_dir / "evaluation_report.json", reports_dir / "evaluation_report.md"
    js.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8", newline="\n")
    md.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return [js, md]


# ------------------------------------------------------------------ markdown
def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _rate_cell(d: dict) -> str:
    return f"{_pct(d['rate'])} ({d['numerator']}/{d['denominator']})"


def render_markdown(r: dict[str, Any]) -> str:
    d, v = r["dataset"], r["verdict"]
    L = [f"# Evaluation report: {d['scale']} dataset", "",
         f"Seed `{d['seed']}` · as-of `{d['as_of_date']}` · employees {d['employees']} · "
         f"anomalies injected: {'yes' if d['anomalies_injected'] else 'no'}  ",
         f"Dataset SHA-256 `{d['dataset_sha256'][:16]}…`"
         + (f" · ground truth SHA-256 `{d['ground_truth_sha256'][:16]}…`" if d["ground_truth_sha256"] else ""), "",
         "Generated by `scripts/evaluate.py` (also run by `scripts/generate.py`). All figures are computed from the",
         "published CSV files, the separate ground truth and a clean baseline regenerated from the same seed.", "",
         "## Verdict", "",
         f"**{'PASS' if v['passed'] else 'FAIL'}**", "",
         "| Check | Result |", "|---|---|",
         f"| Structural checks (PK, FK, required, types, enums, ID patterns, uniqueness) | {'all passed' if v['structural_checks_all_passed'] else 'FAILURES'} |",
         f"| Consistency violations on the clean baseline | {v['clean_baseline_consistency_violations']} |",
         f"| Consistency violations not explained by ground truth | {v['unexplained_consistency_violations']} |",
         f"| Detector findings on the clean baseline (false positives) | {v['clean_baseline_detector_findings']} |",
         f"| Detector micro-F1 on the published dataset | {v['detector_micro_f1']:.4f} |", ""]

    L += ["## Volumes", "", "| Table | Rows | Suggested |", "|---|---:|---:|"]
    target_key = {"employee": "employees", "task": "tasks", "access_grant": "access_grants", "asset": "assets"}
    for t, n in r["volumes"].items():
        tgt = r["volume_targets"].get(target_key.get(t, ""), "")
        L.append(f"| {t} | {n:,} | {f'{tgt:,}' if tgt else ''} |")
    L.append("")

    a = r["anomalies"]
    L += ["## Injected anomalies (ground truth)", "", f"Total: **{a['total']}**"
          + (f" · shortfall vs requested {a['requested_per_type']}/type: {a['shortfall']} (not enough eligible records; none fabricated)"
             if a["shortfall"] else ""), "",
          "| Issue type | " + " | ".join(SEVERITY_ORDER) + " | Total |", "|---|" + "---:|" * (len(SEVERITY_ORDER) + 1)]
    for it, sev in a["by_type_and_severity"].items():
        L.append(f"| {it} | " + " | ".join(str(sev.get(s, "")) for s in SEVERITY_ORDER) + f" | {sum(sev.values())} |")
    L += [f"| **Total** | " + " | ".join(str(a["by_severity"].get(s, "")) for s in SEVERITY_ORDER) + f" | {a['total']} |", ""]

    det = r["detection"]["published_dataset"]
    L += ["## Detector evaluation", "",
          "Rule detector (`validation/rules.py`) reads only the input tables; matches are on `detection_key`.", "",
          "| Issue type | Expected | Detected | TP | FP | FN | Precision | Recall | F1 |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for it, m in det["per_type"].items():
        L.append(f"| {it} | {m['expected']} | {m['detected']} | {m['true_positive']} | {m['false_positive']} | "
                 f"{m['false_negative']} | {m['precision']:.4f} | {m['recall']:.4f} | {m['f1']:.4f} |")
    mi, ma = det["micro"], det["macro"]
    L += [f"| **Micro average** | | | {mi['true_positive']} | {mi['false_positive']} | {mi['false_negative']} | "
          f"{mi['precision']:.4f} | {mi['recall']:.4f} | {mi['f1']:.4f} |",
          f"| **Macro average** | | | | | | {ma['precision']:.4f} | {ma['recall']:.4f} | {ma['f1']:.4f} |", "",
          "Recall by severity: " + ", ".join(f"{s} {x['detected']}/{x['expected']}" for s, x in det["by_severity"].items()), "",
          f"Clean baseline (same seed, no injection): **{r['detection']['clean_baseline']['findings']} findings**.", ""]

    L += ["## Validation (brief Step 8)", "",
          "| Check group | Published: violations | explained by ground truth | unexplained | Clean baseline: violations |",
          "|---|---:|---:|---:|---:|"]
    pub, cln = r["validation"]["published_dataset"], r["validation"]["clean_baseline"]
    s1, s2 = pub["structural"], cln["structural"]
    L.append(f"| Structural ({s1['checks_run']} checks: " + ", ".join(f"{k} {n}" for k, n in s1["checks_by_type"].items())
             + f") | {s1['checks_failed']} failed | – | – | {s2['checks_failed']} failed |")
    for g, x in pub["consistency"].items():
        L.append(f"| {g.replace('_', ' ')} | {x['violations']} | {x['explained_by_ground_truth']} | {x['unexplained']} | "
                 f"{cln['consistency'][g]['violations']} |")
    L.append("")
    for g, x in pub["consistency"].items():
        if x["by_check"]:
            L.append(f"- *{g.replace('_', ' ')}*: " + ", ".join(f"{k} {n}" for k, n in x["by_check"].items()))
    L.append("")

    L += ["## Impact of anomalies on business KPIs", "", "| KPI | Clean baseline | Published | Delta |", "|---|---:|---:|---:|"]
    for row in r["kpi_impact_of_anomalies"]:
        fmt = (lambda x: _pct(x)) if isinstance(row["clean_baseline"], float) or isinstance(row["published_dataset"], float) else str
        delta = row["delta"]
        dfmt = "" if delta is None else (f"{delta * 100:+.1f} pp" if isinstance(row["clean_baseline"], float) else f"{delta:+d}")
        L.append(f"| {row['kpi']} | {fmt(row['clean_baseline'])} | {fmt(row['published_dataset'])} | {dfmt} |")
    L.append("")

    k = r["kpis"]["published_dataset"]
    o, tk, ac, of, asg = k["onboarding"], k["tasks"], k["access"], k["offboarding"], k["assets"]
    L += ["## Workflow KPIs (published dataset)", "",
          "| Area | KPI | Value |", "|---|---|---|",
          f"| Onboarding | Cases / status | {o['cases']} · {o['by_status']} |",
          f"| Onboarding | Day-one readiness (SSO active + equipment delivered by start) | {_rate_cell(o['day_one_readiness_rate'])} |",
          f"| Onboarding | Delayed starts | {_rate_cell(o['delayed_start_rate'])}, avg {o['avg_start_delay_days_when_delayed']} days |",
          f"| Onboarding | Offers withdrawn | {_rate_cell(o['offer_withdrawal_rate'])} |",
          f"| Onboarding | Avg offer-to-start / case cycle (days) | {o['avg_offer_to_start_days']} / {o['avg_case_cycle_days']} |",
          f"| Onboarding | Median days approval -> provisioning | {o['median_days_approval_to_provisioning']} |",
          f"| Tasks | On-time completion | {_rate_cell(tk['on_time_rate'])} · open overdue {tk['open_overdue']} |"]
    for team, x in tk["on_time_rate_by_owner_team"].items():
        L.append(f"| Tasks | On-time: {team} | {_rate_cell(x)} |")
    L += [f"| Access | Grants / status | {ac['grants']} · {ac['by_status']} |",
          f"| Access | Rejection rate | {_rate_cell(ac['rejection_rate'])} |",
          f"| Access | Approved overrides with linked audit event | {_rate_cell(ac['overrides_with_linked_audit_event'])} |",
          f"| Access | Active grants reconciled to active employment | {_rate_cell(ac['active_grants_reconciled_to_active_employment'])} |",
          f"| Offboarding | Exits by type | {of['by_exit_type']} |",
          f"| Offboarding | Revocation within SLA | {_rate_cell(of['revocation_sla_rate'])} · late {of['revoked_late_count']} · unrevoked {of['unrevoked_after_due_count']} |",
          f"| Offboarding | Urgent terminations revoked same day | {_rate_cell(of['urgent_same_day_rate'])} |",
          f"| Offboarding | Asset recovery (past 10-day SLA) | {_rate_cell(of['asset_recovery_rate'])} · lost {of['assets_reported_lost']} |",
          f"| Assets | Inventory / status | {asg['assets']} · {asg['by_status']} |",
          f"| Assets | Utilization · re-issued · acknowledgement | {_pct(asg['utilization_rate']['rate'])} · {asg['reissued_assets']} · {_rate_cell(asg['acknowledgement_rate'])} |",
          f"| Audit | Events | {k['audit']['events']:,} |", ""]

    L += ["## Scenario coverage", "", "| Category | Scenario | Count |", "|---|---|---:|"]
    L += [f"| {c['category']} | {c['scenario']} | {c['count']} |" for c in r["scenario_coverage"]]
    L.append("")
    return "\n".join(L)


def config_for(scale: str, seed: int | None, employees: int | None) -> GeneratorConfig:
    return load_config(scale, seed=seed, employees=employees)


def evaluate_and_write(out_dir: Path, cfg: GeneratorConfig) -> dict[str, Any]:
    report = evaluate_published(out_dir, cfg)
    write_report(report, Path(out_dir) / "reports")
    return report


def summary_lines(report: dict[str, Any], out_dir: Path) -> list[str]:
    v, det = report["verdict"], report["detection"]["published_dataset"]
    lines = [f"Evaluation ({report['dataset']['scale']}): {'PASS' if v['passed'] else 'FAIL'}",
             f"  anomalies injected: {report['anomalies']['total']} by severity {report['anomalies']['by_severity']}"]
    if report["anomalies"]["shortfall"]:
        lines.append(f"  shortfall vs requested {report['anomalies']['requested_per_type']}/type: "
                     f"{report['anomalies']['shortfall']} (not enough eligible records; none fabricated)")
    for it, m in det["per_type"].items():
        lines.append(f"  {it:<36} TP={m['true_positive']:>3} FP={m['false_positive']:>2} FN={m['false_negative']:>2} "
                     f"P={m['precision']:.2f} R={m['recall']:.2f} F1={m['f1']:.2f}")
    lines.append(f"  micro F1={det['micro']['f1']:.4f}  macro F1={det['macro']['f1']:.4f}  "
                 f"clean-baseline findings={v['clean_baseline_detector_findings']}  "
                 f"unexplained violations={v['unexplained_consistency_violations']}")
    lines.append(f"  reports: {Path(out_dir) / 'reports' / 'evaluation_report.md'} (+ .json)")
    return lines
