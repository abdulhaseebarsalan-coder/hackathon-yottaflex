# Onboarding & Offboarding Orchestrator: Synthetic Data Generator

A deterministic, rule-driven generator of synthetic HR / IT / Facilities workflow data:
pre-boarding, access provisioning, equipment delivery and exit tasks. Every status, total and
date is derived from source records, and every state change is kept as an append-only audit event.

> **Status:** Steps 1–6 complete: schema, master data, onboarding/offboarding transactions,
> labelled anomaly injection + ground truth, evaluation metrics & reporting, and CSV / JSON / SQL
> exports with a SHA-256 manifest, a standalone reproducibility check and a MySQL loader.
> **Three-tier delivery:** database tier ✅ (MySQL) · backend tier ✅ (FastAPI REST API) ·
> frontend tier ⏳ **in development (pending)**. See "Three-tier architecture".

## Quick start

```bash
pip install -r requirements.txt               # pytest + backend (fastapi, uvicorn, PyMySQL); generator is stdlib-only
python scripts/generate.py --scale starter    # 100 employees   -> output/starter/
python scripts/generate.py --scale full       # 1,000 employees -> output/full/
python scripts/reproduce.py                   # regenerate both scales twice, compare all SHA-256s
python -m pytest -q                           # 144 tests (+1 opt-in live MySQL test)

# optional
python scripts/evaluate.py --scale full       # re-evaluate an existing output folder
python scripts/reproduce.py --verify-only     # only re-hash output/<scale> against manifest.json
python scripts/export_schema.py               # rebuild sql/schema.sql + docs/ from the schema
python scripts/load_mysql.py --scale starter --yes   # load into MySQL (see "MySQL")
```

Options: `--seed <int>` (default **20261022** from `config/settings.json`), `--employees <n>`
(other volumes scale proportionally), `--out <dir>`, `--no-anomalies` (write the clean,
rule-compliant baseline without injected edge cases, e.g. `--out output/full_clean`).
Starter and full datasets come from the same generator and the same seed; only the scale
parameter differs. `generate.py` evaluates the files it has just written and exits non-zero if
the evaluation verdict is FAIL (see "Evaluation metrics and reporting").

Output layout (one self-describing folder per scale):

```
output/<scale>/csv/<table>.csv                 input tables (what a consumer / model sees)
output/<scale>/json/<table>.json               same tables as typed JSON
output/<scale>/sql/seed.sql                    MySQL INSERTs in dependency order (one transaction)
output/<scale>/ground_truth/anomalies.csv      labels for injected anomalies (kept separate)
output/<scale>/ground_truth/anomalies.json     same, plus seed / scale / requested vs injected counts
output/<scale>/reports/evaluation_report.md    human-readable evaluation (verdict, metrics, KPIs)
output/<scale>/reports/evaluation_report.json  the same, machine-readable
output/<scale>/manifest.json                   SHA-256, bytes and rows of every file above + provenance
```

## Three-tier architecture

```
┌─────────────────────────────┐   HTTP / JSON    ┌──────────────────────────────┐   SQL (PyMySQL)   ┌──────────────────────────┐
│ FRONTEND (presentation)     │ ───────────────► │ BACKEND (application / API)  │ ────────────────► │ DATABASE (data)          │
│ frontend/  - PENDING        │                  │ backend/  FastAPI + Uvicorn  │                   │ MySQL `hackathon_db`     │
│ dashboard in the browser    │ ◄─────────────── │ read-only REST API, :8000    │ ◄──────────────── │ 15 tables, 36 FKs,       │
│ (calls the API only)        │                  │ Swagger UI at /docs          │                   │ CHECK constraints        │
└─────────────────────────────┘                  └──────────────────────────────┘                   └──────────────────────────┘
                                                                                                                ▲
                                          data pipeline (offline): scripts/generate.py -> output/<scale>/ ──────┘
                                          scripts/load_mysql.py (schema.sql -> seed.sql -> deferred FKs)
```

| Tier | Folder | Technology | Status |
|---|---|---|---|
| Database | `sql/`, loaded by `scripts/load_mysql.py` | MySQL 8.0.16+ (tested on 26.10) | ✅ done |
| Backend | `backend/` | Python, FastAPI, Uvicorn, PyMySQL | ✅ done |
| Frontend | `frontend/` | HTML / CSS / JavaScript dashboard | ⏳ pending |

Each tier talks only to its neighbour: the frontend never reads files or MySQL directly, and the
backend is the only component that queries the database. The data pipeline (Steps 1–6) builds and
loads the database; it is not part of the request path.

### Backend: setup and run

```bash
pip install -r requirements.txt               # pytest, fastapi, uvicorn, PyMySQL
copy .env.example .env                        # Windows (cp on macOS/Linux); set MYSQL_USER / MYSQL_PASSWORD
python scripts/load_mysql.py --scale full --yes     # database tier must be loaded first
python -m uvicorn backend.main:app --port 8000      # start the API (run from the repository root)
```

Interactive API documentation (Swagger UI): **http://127.0.0.1:8000/docs**, where you can try every
endpoint. The OpenAPI spec is at http://127.0.0.1:8000/openapi.json.

The backend reads the same `MYSQL_*` settings as the loader (environment variables or `.env`).
Ground-truth labels are read from `output/full/ground_truth/` (override with `DATASET_DIR`); they are
never stored in MySQL. CORS allows the frontend origin `http://127.0.0.1:5500` / `http://localhost:5500`
(override with `API_CORS_ORIGINS`).

### API endpoints (all `GET`, read-only)

| Endpoint | Description |
|---|---|
| `/api/health` | API and database status: database name, MySQL version, number of tables |
| `/api/summary` | Row counts per table, breakdowns (employees by status / type, onboarding by status, exits by type, grants, assets, tasks, active headcount by department) and KPIs (task on-time rate, open overdue tasks, delayed starts, approved overrides, urgent terminations) |
| `/api/anomalies` | Runs the 5 business-rule checks (`sql/queries/anomaly_checks.sql`) **live in MySQL** and joins them with the ground truth: per type found vs expected, and per anomaly its severity, rule, expected decision and `confirmed_in_db`. Filters: `?issue_type=`, `?severity=` |
| `/api/employees` | Employee search. `?q=` (ID, name or email fragment), `?status=` (PRE_BOARDING / ACTIVE / TERMINATED / WITHDRAWN), `?limit=` (1-200), `?offset=` |
| `/api/employees/{employee_id}` | Full lifecycle of one employee (`EMP-000001` format): profile, role history, onboarding and offboarding case, tasks with dependencies, access grants, asset assignments and audit trail |

Live results on the full dataset: `/api/anomalies` returns 10 found / 10 expected for each of the 5
types (50 / 50 confirmed, 0 unexpected).

Security and robustness:
- Read-only: only `SELECT`s are executed, and only `GET` routes exist.
- User input is passed only as query parameters, never concatenated into SQL. LIKE wildcards in search are escaped.
- Inputs are validated before they reach the database: `employee_id` pattern, enum values, limits. Invalid input gets HTTP 422.
- Database errors return HTTP 503 with a generic message: no stack trace, no credentials.

Backend tests (`tests/test_backend.py`, 18 tests) start the real Uvicorn server on a free port with
a fake database, so no MySQL is needed. They cover every endpoint, filters, parameter binding,
validation, 404 / 503 handling, CORS and the read-only guarantee. If FastAPI / Uvicorn are not
installed, these tests are skipped.

### Frontend

⏳ **Pending.** Planned: a static dashboard in `frontend/` (KPI cards, anomaly table with severities,
employee search and lifecycle view) that reads data only from the backend API.

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
  export/json_writer.py         JSON export
  export/sql_writer.py          MySQL DDL (from schema), deferred FKs, seed INSERTs, statement splitter
  export/ground_truth_writer.py ground-truth CSV / JSON export
  export/manifest.py            SHA-256 manifest build + verification
  export/docs_writer.py         data dictionary + ER diagram (from schema)
  publish.py                    writes one complete output folder (used by generate + reproduce)
  db/mysql_loader.py            env-based settings, mysql CLI / PyMySQL backends, load + verify
scripts/generate.py             generate + export + evaluate + manifest
scripts/evaluate.py             evaluate an existing output folder
scripts/reproduce.py            standalone reproducibility / integrity check
scripts/export_schema.py        rebuild sql/schema.sql, sql/deferred_constraints.sql, docs/*.md
scripts/load_mysql.py           load a dataset into MySQL and verify it
sql/schema.sql                  generated DDL (15 tables, PK / FK / UNIQUE / CHECK / indexes)
sql/deferred_constraints.sql    the 2 forward-reference FKs, added after the seed
sql/queries/anomaly_checks.sql  the 5 business rules as SQL (run by the loader)
docs/data_dictionary.md         every field: type, required, allowed values, units, uniqueness, rule
docs/er_diagram.md              Mermaid ER diagram + cardinality table
.env.example                    MySQL settings template (copy to .env; .env is git-ignored)
backend/                        BACKEND TIER (FastAPI)
  main.py                       app, 5 GET endpoints, validation, CORS, error handling
  repository.py                 parameterized SQL queries behind the endpoints
  db.py                         PyMySQL connection (settings from env / .env), read-only
frontend/                       FRONTEND TIER (pending)
tests/                          pytest suite (incl. tests/test_backend.py)
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

## Exports, manifest and reproducibility (Step 6)

**Formats.** Every table is exported three ways from the same in-memory rows:

| Format | Details |
|---|---|
| CSV | schema column order, `true`/`false`, ISO dates, `YYYY-MM-DD HH:MM:SS`, empty = null, LF |
| JSON | `{"table", "primary_key", "row_count", "columns", "rows": [...]}`, one record per line; numbers, booleans and `null` are native, and dates are ISO strings |
| SQL | `sql/schema.sql` + `output/<scale>/sql/seed.sql` + `sql/deferred_constraints.sql` |

**SQL schema.** It is generated from `schema.py`, so it cannot drift (a test compares the
committed file with the generator). It has 15 InnoDB tables, a primary key on each, 34 inline FKs
in load order, 2 deferred FKs added after loading (so MySQL validates every row), UNIQUE keys,
`CHECK` constraints for every enum, structural checks (e.g. a task belongs to exactly one case),
query indexes, and column comments. Business-rule ordering is deliberately *not* a constraint,
because the labelled anomalies must load so they can be detected.

**Manifest.** `output/<scale>/manifest.json` lists, for every published file, its relative path,
size, SHA-256 and row count. It also records the generator command, seed, code version, source
provenance (synthetic, no external data) and the evaluation verdict. It has no timestamps or
absolute paths, so identical inputs give an identical manifest. `verify_manifest()` reports
missing, changed and unlisted files.

**Reproducibility.** `python scripts/reproduce.py` verifies `output/<scale>` against its manifest,
regenerates each scale twice from scratch in git-ignored `output/_repro_*` folders, and requires
every file hash and row count to be identical across the runs **and** with the published manifest.
Current result: starter and full PASS (35 files each, all identical).

## MySQL

1. Create the database once (e.g. in MySQL Workbench: `CREATE DATABASE hackathon_db;`).
2. `copy .env.example .env` (Windows) or `cp .env.example .env`, then set `MYSQL_USER` /
   `MYSQL_PASSWORD`. **Never commit `.env`** (it is in `.gitignore`). Shell environment variables
   override `.env`.
3. `python scripts/load_mysql.py --scale starter --dry-run` checks the files and settings without connecting.
4. `python scripts/load_mysql.py --scale starter --yes` (or `--scale full --yes`).

The loader:
- re-verifies the manifest checksums and refuses to load tampered files;
- creates the database if missing, then runs `schema.sql` -> `seed.sql` -> `deferred_constraints.sql`;
- checks loaded row counts against the manifest and that all 36 FKs exist;
- runs the 5 SQL anomaly checks (`sql/queries/anomaly_checks.sql`) and compares them with the ground truth.

It uses PyMySQL if installed, otherwise the `mysql` command-line client (on PATH, under
`C:/Program Files/MySQL/*/bin`, or `MYSQL_CLIENT`). With the CLI the password goes into a temporary
owner-only option file that is deleted immediately; it is never on the command line and never
printed. **`--yes` drops and recreates the 15 project tables** in `MYSQL_DATABASE`; other tables are
untouched and system databases are refused.

Verified on MySQL 26.10 (isolated throwaway server): starter and full load with all FKs and
CHECK constraints, row counts match, and the 5 SQL checks return exactly the ground-truth counts
(full: 10 each). The clean baseline returns 0 for every check. Invalid enums, orphan FKs (including
the deferred `employee.manager_id`) and a task attached to two cases are rejected by MySQL. To run the
opt-in live test against your own disposable database:
`ORCH_MYSQL_LIVE_TEST=1 MYSQL_TEST_DATABASE=<scratch_db> python -m pytest tests/test_mysql_loader.py -k live`
(with `MYSQL_USER` / `MYSQL_PASSWORD` set).

## Limitations

- **Volumes:** access grants (2,456) exceed the suggested 1,500 because mandatory role access is
  kept (see "Volumes"); starter scale has 14 anomalies, not 15 (shortfall reported, not fabricated).
- **Evaluation is self-consistency:** detector scores are measured against labels produced by this
  project, not real-world data.
- **SQL dialect:** the DDL targets MySQL 8.0.16+ (CHECK constraints, recursive CTEs). The seed uses
  standard quote escaping and doubled backslashes, so it assumes the default `sql_mode` (no
  `NO_BACKSLASH_ESCAPES`). The generated data contains no backslashes.
- **Reproducibility scope:** byte-identical output is guaranteed for the same code version, seed,
  scale and employee count. The manifest records the code version, not the Python version.
- **Output size:** the full folder is about 34 MB (JSON ~18 MB, SQL ~9 MB, CSV ~7 MB), within GitHub limits.
- **Live MySQL tests are opt-in.** Ordinary unit tests use a fake backend and mocked `subprocess`.

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
17. The MySQL target database is dedicated to this project. The loader replaces its 15 project
    tables on every run, so a load is idempotent.
18. Ground truth is not loaded into MySQL; it stays in `output/<scale>/ground_truth/` and is used only
    to compare with the SQL checks.

## Provenance and lawful use

No external data is used. All names, roles, systems and asset models are hand-authored, generic and
combined at random; email addresses use the reserved domain `example.com` (RFC 2606). No real
person, organization or confidential information is represented.
