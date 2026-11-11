"""Business-rule detection: finds rule violations using ONLY the published input tables.

The detector never reads ground truth. On the clean baseline it must return nothing; on the
anomaly-injected dataset its findings are compared with ground truth (precision / recall).

RULE-ACCESS-01  ACCESS_BEFORE_START_OR_APPROVAL
    A provisioned grant must be approved, provisioned on/after its approval date, and on/after the
    employee's start date - unless it links (override_event_id) to an ACCESS_OVERRIDE_APPROVED event
    for that same grant. An override can excuse an early start, never a missing approval.
RULE-ACCESS-02  TERMINATED_EMPLOYEE_RETAINS_GRANT
    No ACTIVE grant may belong to a TERMINATED employee once the exit's revocation_due_date has passed.
RULE-ASSET-01   DUPLICATE_ASSET_ASSIGNMENT
    An asset's assignment periods must never overlap (two holders at the same time).
RULE-ASSET-02   RECOVERED_ASSET_REMAINS_ASSIGNED
    An assignment cannot be open (no returned/lost date) when the asset itself is recorded back in
    stock / retired / lost by its lifecycle events.
RULE-TASK-01    TASK_DEPENDENCY_CYCLE
    Each case's task dependency graph must be acyclic.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

Row = dict[str, Any]


def _finding(issue_type: str, rule_id: str, entity_type: str, entity_id: str, evidence: str) -> Row:
    return {"issue_type": issue_type, "rule_id": rule_id, "entity_type": entity_type,
            "detection_key": entity_id, "evidence": evidence}


def detect_access_before_start_or_approval(t: dict[str, list[Row]]) -> list[Row]:
    emp = {e["employee_id"]: e for e in t["employee"]}
    events = {e["event_id"]: e for e in t["audit_event"]}
    out = []
    for g in t["access_grant"]:
        p = g["provisioned_date"]
        if p is None:
            continue
        problems = []
        if g["approval_status"] != "APPROVED" or g["approval_date"] is None or p < g["approval_date"]:
            problems.append(f"provisioned {p} before approval {g['approval_date']}")
        start = emp[g["employee_id"]]["start_date"]
        if p < start:
            ov = events.get(g["override_event_id"]) if g["override_event_id"] else None
            if not (ov and ov["event_type"] == "ACCESS_OVERRIDE_APPROVED" and ov["entity_id"] == g["grant_id"]):
                problems.append(f"provisioned {p} before start {start} without approved override")
        if problems:
            out.append(_finding("ACCESS_BEFORE_START_OR_APPROVAL", "RULE-ACCESS-01", "access_grant",
                                g["grant_id"], "; ".join(problems)))
    return out


def detect_terminated_employee_retains_grant(t: dict[str, list[Row]], as_of: date) -> list[Row]:
    emp = {e["employee_id"]: e for e in t["employee"]}
    off = {c["employee_id"]: c for c in t["offboarding_case"]}
    out = []
    for g in t["access_grant"]:
        e = emp[g["employee_id"]]
        if g["grant_status"] == "ACTIVE" and e["employment_status"] == "TERMINATED":
            due = off[e["employee_id"]]["revocation_due_date"] if e["employee_id"] in off else e["termination_date"]
            if as_of > due:
                out.append(_finding("TERMINATED_EMPLOYEE_RETAINS_GRANT", "RULE-ACCESS-02", "access_grant",
                                    g["grant_id"], f"{e['employee_id']} terminated {e['termination_date']}; "
                                                   f"revocation due {due}; grant still ACTIVE"))
    return out


def detect_duplicate_asset_assignment(t: dict[str, list[Row]]) -> list[Row]:
    by_asset = defaultdict(list)
    for s in t["asset_assignment"]:
        by_asset[s["asset_id"]].append(s)
    out = []
    for aid, rows in sorted(by_asset.items()):
        rows = sorted(rows, key=lambda s: (s["assigned_date"], s["assignment_id"]))
        overlaps = []
        for i, a in enumerate(rows):
            a_end = a["returned_date"] or a["lost_reported_date"]   # None = still held
            for b in rows[i + 1:]:
                if a_end is None or b["assigned_date"] < a_end:
                    overlaps.append(f"{a['assignment_id']}({a['employee_id']}) overlaps {b['assignment_id']}({b['employee_id']})")
        if overlaps:
            out.append(_finding("DUPLICATE_ASSET_ASSIGNMENT", "RULE-ASSET-01", "asset", aid, "; ".join(overlaps)))
    return out


def detect_recovered_asset_remains_assigned(t: dict[str, list[Row]]) -> list[Row]:
    status = {a["asset_id"]: a["asset_status"] for a in t["asset"]}
    out = []
    for s in t["asset_assignment"]:
        if s["returned_date"] is None and s["lost_reported_date"] is None and status[s["asset_id"]] in ("IN_STOCK", "RETIRED", "LOST"):
            out.append(_finding("RECOVERED_ASSET_REMAINS_ASSIGNED", "RULE-ASSET-02", "asset_assignment",
                                s["assignment_id"], f"{s['asset_id']} is {status[s['asset_id']]} but assignment to "
                                                    f"{s['employee_id']} is still open"))
    return out


def find_cycles(edges: dict[str, list[str]]) -> list[list[str]]:
    """Return one representative cycle per strongly-connected component with a cycle (iterative DFS)."""
    state, cycles = {}, []
    for root in sorted(edges):
        if root in state:
            continue
        stack, path = [(root, iter(sorted(edges.get(root, []))))], [root]
        state[root] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                path.pop()
            elif state.get(nxt) == 1:
                cycles.append(path[path.index(nxt):] + [nxt])
            elif nxt not in state:
                state[nxt] = 1
                stack.append((nxt, iter(sorted(edges.get(nxt, [])))))
                path.append(nxt)
    return cycles


def detect_task_dependency_cycle(t: dict[str, list[Row]]) -> list[Row]:
    case_of = {x["task_id"]: x["onboarding_case_id"] or x["offboarding_case_id"] for x in t["task"]}
    edges_by_case: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for d in t["task_dependency"]:
        edges_by_case[case_of[d["task_id"]]][d["task_id"]].append(d["depends_on_task_id"])
    out = []
    for case_id in sorted(edges_by_case):
        cycles = find_cycles(edges_by_case[case_id])
        if cycles:
            out.append(_finding("TASK_DEPENDENCY_CYCLE", "RULE-TASK-01", "case", case_id,
                                " | ".join(" -> ".join(c) for c in cycles)))
    return out


def detect_all(t: dict[str, list[Row]], as_of: date) -> list[Row]:
    return (detect_access_before_start_or_approval(t)
            + detect_duplicate_asset_assignment(t)
            + detect_terminated_employee_retains_grant(t, as_of)
            + detect_task_dependency_cycle(t)
            + detect_recovered_asset_remains_assigned(t))


def evaluate(findings: list[Row], ground_truth: list[Row]) -> dict[str, dict[str, Any]]:
    """Per issue type: TP / FP / FN, precision, recall, F1 (matched on detection_key).

    Convention for empty denominators: precision = 1.0 when nothing was flagged, recall = 1.0 when
    nothing was expected (no evidence of error), F1 = harmonic mean of the two.
    """
    types = sorted({r["issue_type"] for r in ground_truth} | {f["issue_type"] for f in findings})
    report = {}
    for it in types:
        found = {f["detection_key"] for f in findings if f["issue_type"] == it}
        truth = {g["detection_key"] for g in ground_truth if g["issue_type"] == it}
        tp, fp, fn = len(found & truth), len(found - truth), len(truth - found)
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        report[it] = {
            "expected": len(truth), "detected": len(found), "true_positive": tp, "false_positive": fp,
            "false_negative": fn, "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "false_positive_keys": sorted(found - truth), "missed_keys": sorted(truth - found),
        }
    return report
