# Onboarding & Offboarding Orchestrator: Synthetic Data Generator

A deterministic, rule-driven generator of synthetic HR / IT / Facilities workflow data:
pre-boarding, access provisioning, equipment delivery and exit tasks. Every status, total and
date is derived from source records, and every state change is kept as an append-only audit event.

> **Status:** Steps 1–5 complete (schema, master data, onboarding/offboarding transactions,
> labelled anomaly injection + ground truth, evaluation metrics & reporting). JSON/SQL seed
> exports, the checksum manifest and the MySQL load are added in the next step.

## Quick start

```bash
pip install -r requirements.txt          # only pytest; the generator uses the standard library
python scripts/generate.py --scale starter   # 100 employees  -> output/starter/{csv,ground_truth,reports}
python scripts/generate.py --scale full      # 1,000 employees -> output/full/{csv,ground_truth,reports}
python scripts/evaluate.py --scale full      # re-evaluate an existing output folder (optional)
python -m pytest -q                          # tests
```

Options: `--seed <int>` (default **20261022** from `config/settings.json`), `--employees <n>`
(other volumes scale proportionally), `--out <dir>`, `--no-anomalies` (write the clean,
rule-compliant baseline without injected edge cases, e.g. `--out output/full_clean`).
Starter and full datasets come from the same generator and the same seed; only the scale
parameter differs. `generate.py` evaluates the files it has just written and exits non-zero if
the evaluation verdict is FAIL (see "Evaluation metrics and reporting").

Output layout:

```
output/<scale>/csv/<table>.csv              input tables (what a consumer / model sees)
output/<scale>/ground_truth/anomalies.csv   labels for injected anomalies (kept separate)
output/<scale>/ground_truth/anomalies.json  same, plus seed / scale / requested vs injected counts
output/<scale>/reports/evaluation_report.md    human-readable evaluation (verdict, metrics, KPIs)
output/<scale>/reports/evaluation_report.json  the same, machine-readable
```

## Project layout

```
config/settings.json            seed, as-of date, scales, scenario rates, SLAs
src/orchestrator/
  schema.py                     single source of truth: tables, fields, types, enums, keys, rules
  ids.py                        deterministic ID formats + validation patterns
  reference.py                  org, role templates, access catalog, asset catalog, name pools
  generator/
    master.py                   departments, roles, access roles, templates, employees, managers
    lifecycle.py                exits, role changes, requested access, equipment kits
    tasks.py                    task templates + dependency-aware scheduler
    cases.py                    onboarding / offboarding cases, tasks, dependencies, audit
    access.py                   access grants: request -> approval -> provisioning -> revocation
    assets.py                   inventory, assignments, acknowledgement, recovery
    derive.py                   all derived values (statuses, totals, percentages, due dates)
    anomalies.py                labelled edge-case injection -> ground truth
    pipeline.py                 stage orchestration
  validation/structural.py      schema-driven PK / FK / enum / type / pattern / uniqueness checks
  validation/rules.py           business-rule detector (reads input tables only) + P / R / F1
  validation/consistency.py     date ordering, arithmetic reconciliation, lifecycle, duplicate checks
  metrics/kpis.py               workflow KPIs and scenario coverage
  metrics/evaluation.py         evaluation report (JSON + Markdown)
  datasets.py                   typed loader for published CSV / ground truth; content fingerprints
  export/csv_writer.py          CSV export (stable formatting for checksums)
  export/ground_truth_writer.py ground-truth CSV / JSON export
scripts/generate.py             generator command (also writes the evaluation report)
scripts/evaluate.py             evaluate an existing output folder
tests/                          pytest suite
```

## Data model (15 tables)

Master: `department`, `role`, `access_role`, `employee`, `manager`, `asset` ·
Bridge (N:M / effective-dated): `role_access_template`, `employee_role_history`,
`task_dependency`, `asset_assignment` ·
Transactions: `onboarding_case`, `offboarding_case`, `task`, `access_grant` ·
Events: `audit_event`.

ID conventions: `EMP-000001`, `ONB-2026-000001`, `TASK-<case-id>-001`,
`GRANT-<employee-id>-001`, `AST-000001`, `OFF-2026-000001` (plus `DEPT-`, `ROLE-`, `MGR-`,
`AR-`, `RAT-`, `RH-`, `DEPN-`, `ASG-`, `EVT-`). Counters are zero-padded and immutable.

Two foreign keys are forward references (`employee.manager_id -> manager`, a true cycle, and
`access_grant.override_event_id -> audit_event`, loaded last). They are detected automatically
(`schema.deferred_foreign_keys()`) and added with `ALTER TABLE` after load, so integrity is
still enforced without disabling FK checks.

## Business rules implemented

1. **Organization & role templates.** 12 departments, 50 roles, 15 entitlements. Every role's
   template contains *Corporate SSO & Email* (mandatory) plus department/role applications.
2. **Hires & start dates.** Offers accepted 2026-01-01 to the as-of date; starts on Mondays 14-45
   days later. Delayed starts are recorded as `START_DATE_CHANGED` events (history is never overwritten).
3. **Tasks with prerequisites.** Templates instantiate per case with FINISH_TO_START dependencies;
   a task never starts before every prerequisite completes.
4. **Access only after approval.** Requests are raised by HR (or the HRIS feed), approved by the
   line manager (+ a system owner for high-risk systems) and provisioned no earlier than both the
   approval and the start date. The single exception path is an approved early-access override,
   which always has an `ACCESS_OVERRIDE_APPROVED` audit event linked from the grant.
5. **Equipment & acknowledgement.** Assets are allocated chronologically from stock (returned assets
   are re-issued after refurbishment) and acknowledged by the employee.
6. **Exits, revocation & recovery.** Offboarding cases revoke all access (urgent termination: same
   day) and recover equipment; some assets are reported lost.
7. **Reconciliation.** Active grants belong only to active employees (or to an approved early-access
   exception), match the employee's current role template, and terminated employees hold none
   once the revocation SLA has passed. *(Currently enforced by tests; reported by the validator next.)*
8. **Derived values** are computed from source records only (`generator/derive.py`).

## Anomaly injection and ground truth (Step 4)

Pipeline: **clean baseline -> derive -> inject labelled anomalies -> derive again**. The baseline
passes every business rule (asserted by the Step 3 tests on the `--no-anomalies` dataset); the
injected dataset differs from it *only* in rows listed in the ground truth (asserted by test).

| Issue type | Rule | Severity | What is injected | Expected decision |
|---|---|---|---|---|
| `ACCESS_BEFORE_START_OR_APPROVAL` | RULE-ACCESS-01 | HIGH | grant provisioning moved before the start date (`BEFORE_START`) or before approval (`BEFORE_APPROVAL`), with no override event | suspend until start / revoke pending approval |
| `DUPLICATE_ASSET_ASSIGNMENT` | RULE-ASSET-01 | MEDIUM | an asset still held by one employee is issued to a second employee | recall from second holder |
| `TERMINATED_EMPLOYEE_RETAINS_GRANT` | RULE-ACCESS-02 | CRITICAL | one exit revocation (and its `GRANT_REVOKED` event) is missing after the SLA | revoke immediately, review logs |
| `TASK_DEPENDENCY_CYCLE` | RULE-TASK-01 | MEDIUM | a back-edge dependency makes a task wait for its own downstream task | remove the invalid dependency |
| `RECOVERED_ASSET_REMAINS_ASSIGNED` | RULE-ASSET-02 | LOW | asset recovered into stock (`ASSET_RETURNED` kept) but its assignment left open | close assignment from the recovery event |

Each ground-truth record has: `anomaly_id`, `issue_type`, `sub_type`, `severity`, `rule_id`,
`entity_type`, `entity_id`, `detection_key`, `employee_id`, `case_id`, `related_ids`,
`field_changes` (exact before -> after values), `expected_decision`, `description`.

Guarantees:
- **Deterministic:** own seeded RNG stream (`<seed>:anomalies`); same seed -> identical labels.
- **Separate:** labels live only in `ground_truth/`; input tables have no label columns and audit
  reasons contain no label text.
- **No silent corruption:** existing IDs are never renumbered. New rows/events take the next free
  ID, and a removed `GRANT_REVOKED` event leaves a gap that is recorded in the ground truth.
- **Structurally valid:** anomalies break business rules, never PK / FK / enum integrity.
- **Snapshot-safe:** no injected fact is dated after 2026-10-22.
- **Isolated:** each grant / asset / case carries at most one anomaly, so detection is unambiguous.
- **Measured:** `validation/rules.py` detects violations from the input tables alone. It finds nothing
  on the clean baseline, and precision = recall = 1.0 per type on the published datasets.

Volume: `anomalies_per_type` (full 10, starter 3). Full scale injects exactly 10 of each type (50).
Starter injects 14: only 2 assets qualify for `RECOVERED_ASSET_REMAINS_ASSIGNED` at 100 employees,
and the shortfall is reported in `anomalies.json` rather than fabricated.

## Volumes

| Entity | Suggested | Generated (full, seed 20261022) |
|---|---|---|
| Employees | 1,000 | 1,000 |
| Tasks | 8,000 | 7,999 |
| Access grants | 1,500 | **2,456** |
| Assets | 1,200 | 1,200 |

**Why access grants exceed the suggestion:** every employee needs the mandatory Corporate SSO grant
plus the mandatory application(s) for their role, which alone is about 1,780 grants. Reaching 1,500 would
require removing mandatory access or treating SSO as an untracked birthright account, which would
exclude the most critical access from approval and reconciliation controls. Business rules take
precedence over suggested counts; optional-request and other rates are tunable in `config/settings.json`.

## Evaluation metrics and reporting (Step 5)

`generate.py` (or `evaluate.py`) reads the **published files** back from disk: `csv/` plus the
separate `ground_truth/`. It regenerates the clean baseline in memory from the same seed and writes
`reports/evaluation_report.{md,json}`. The report contains no timestamps, so reruns are
byte-identical. It has these sections:

| Section | What it measures |
|---|---|
| Verdict | PASS only if: structural checks all pass, the clean baseline has 0 consistency violations and 0 detector findings, every violation in the published data is explained by ground truth, and detector micro-F1 = 1.0 |
| Volumes | rows per table vs the brief's suggested volumes |
| Injected anomalies | counts by issue type x severity (CRITICAL / HIGH / MEDIUM / LOW), sub-types, requested vs injected (shortfall) |
| Detector evaluation | per type: expected, detected, TP, FP, FN, precision, recall, F1; micro and macro averages; recall by severity; findings on the clean baseline |
| Validation (brief Step 8) | structural checks (PK uniqueness, FK integrity, required, types, enums, ID patterns, uniqueness) and consistency checks: date ordering, arithmetic reconciliation (every stored derived value is re-derived and compared; inventory vs assignments; exit revocation / recovery completeness), lifecycle transitions (audit event chains), duplicates. Each violation is *explained* (referenced by a ground-truth record) or *unexplained* |
| Impact of anomalies on KPIs | clean baseline vs published value and delta for reconciliation-sensitive KPIs |
| Workflow KPIs | day-one readiness, delayed starts, withdrawals, cycle times, task on-time rate by team, rejection rate, override audit linkage, access-to-employment reconciliation, revocation SLA, urgent same-day revocation, asset recovery, utilization, acknowledgement |
| Scenario coverage | counts of normal / boundary / exception / failure (injected) scenarios present |

Metric conventions: precision = TP / (TP + FP), recall = TP / (TP + FN), F1 = harmonic mean;
precision is 1.0 when nothing is flagged and recall is 1.0 when nothing is expected. Micro
averages pool TP / FP / FN across types; macro averages are the mean of per-type values. KPI
definitions are documented in `metrics/kpis.py`.

Current results (seed 20261022):

| | Starter | Full |
|---|---|---|
| Verdict | PASS | PASS |
| Anomalies (CRITICAL / HIGH / MEDIUM / LOW) | 14 (3 / 3 / 6 / 2) | 50 (10 / 10 / 20 / 10) |
| Detector micro precision / recall / F1 | 1.0 / 1.0 / 1.0 | 1.0 / 1.0 / 1.0 |
| Clean-baseline detector findings | 0 | 0 |
| Consistency violations: published (all explained) / clean | 18 / 0 | 67 / 0 |

The detector is evaluated against labels produced by the same project, so a perfect score shows
that labels, injected rows and documented rules agree. It is not evidence of performance on
unseen real-world data. Tests prove the metrics react to errors: dropping a type or adding a false
alarm lowers precision / recall / F1; unlabelled corruption shows up as *unexplained* violations;
deleting the ground-truth folder turns every anomaly into a false positive and the verdict into FAIL.

## Assumptions

1. **Snapshot model.** The dataset is a point-in-time snapshot at **2026-10-22**. No recorded fact
   (completion, approval, provisioning, revocation, return, audit event) is dated after it.
   *Planned* future dates are allowed and expected: planned start dates and future-dated
   role-history rows for pre-boarding hires, task due dates, last working dates of people serving
   notice, and contract end dates.
2. Withdrawals and start-date changes exist only if decided on or before the snapshot.
3. Workflow actors (managers, HR partners, IT/operations partners, facilities coordinators, system
   administrators) neither exit nor change role, so every case keeps valid owners.
4. Existing staff (hired 2017-2025) receive baseline access approved by their manager at their
   original start date. The CEO has no line manager; a system administrator approves the CEO's access.
5. Segregation of duties: requesters (HR partner / HRIS feed) never approve; nobody approves their own
   access; a system-owner approval is never given by the grantee.
6. Agency contractors skip the background check (vetted by the agency). Contractors and interns never
   hold entitlements that are not `contractor_allowed`; ~50% of contractors bring their own device.
7. A role change revokes entitlements not in the new template and requests the new ones; it does not
   swap equipment. There is no laptop refresh cycle: laptops older than 5 years are retired when returned.
8. A withdrawn candidate returns shipped equipment 2-7 days after withdrawal; if that date is after
   the snapshot the assignment stays open (pending return) rather than being fabricated.
9. Privileged hires complete security training before application access is provisioned; the
   app-access SLA is start + 9 days for them (start + 2 days otherwise).
10. Asset purchase dates are never earlier than 2017-01-01 (start of the HRIS baseline).
11. Audit timestamps are causal: events triggered by a task are stamped relative to that task's
    start/completion timestamps; case events bracket their tasks.
12. Injected anomalies model process failures inside the systems of record (e.g. an engineer
    provisioning early, a missed revocation, an unclosed assignment). Their audit events therefore
    look like ordinary operational events; only the separate ground truth identifies them.
13. `RECOVERED_ASSET_REMAINS_ASSIGNED` is isolated per asset, not per employee: one leaver may also
    carry a `TERMINATED_EMPLOYEE_RETAINS_GRANT` anomaly on a different record.
14. Evaluation regenerates the clean baseline in memory (it is not written to disk unless you run
    `--no-anomalies`). Evaluating an output folder therefore needs the same code version, seed and scale.
15. Day-one readiness = SSO grant active on/before the start date AND equipment (if any) delivered
    on/before it. Identity provisioning takes 0-1 days from the start date, so about half of hires
    are not ready on day one; this is a genuine property of the generated process, reported as-is.
16. Recovery and revocation KPIs only include exits whose SLA window has closed by the snapshot
    (revocation: after `revocation_due_date`; recovery: 10 days after the last working date).

## Provenance and lawful use

No external data is used. All names, roles, systems and asset models are hand-authored, generic and
combined at random; email addresses use the reserved domain `example.com` (RFC 2606). No real
person, organization or confidential information is represented.
