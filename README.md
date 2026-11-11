# Onboarding & Offboarding Orchestrator: Synthetic Data Generator

A deterministic, rule-driven generator of synthetic HR / IT / Facilities workflow data:
pre-boarding, access provisioning, equipment delivery and exit tasks. Every status, total and
date is derived from source records, and every state change is kept as an append-only audit event.

> **Status:** Steps 1–3 complete (schema, master data, onboarding/offboarding transactions).
> Labelled anomaly injection + ground truth, exports (JSON/SQL/manifest), metrics and the
> reproducibility report are being added in the next steps; this README is updated as they land.

## Quick start

```bash
pip install -r requirements.txt          # only pytest; the generator uses the standard library
python scripts/generate.py --scale starter   # 100 employees  -> output/starter/csv
python scripts/generate.py --scale full      # 1,000 employees -> output/full/csv
python -m pytest -q                          # tests
```

Options: `--seed <int>` (default **20261022** from `config/settings.json`), `--employees <n>`
(other volumes scale proportionally), `--out <dir>`. Starter and full datasets come from the
same generator and the same seed; only the scale parameter differs.

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
    pipeline.py                 stage orchestration
  validation/structural.py      schema-driven PK / FK / enum / type / pattern / uniqueness checks
  export/csv_writer.py          CSV export (stable formatting for checksums)
scripts/generate.py             generator command
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

## Provenance and lawful use

No external data is used. All names, roles, systems and asset models are hand-authored, generic and
combined at random; email addresses use the reserved domain `example.com` (RFC 2606). No real
person, organization or confidential information is represented.
