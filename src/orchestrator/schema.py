"""Single source of truth for every table: fields, types, keys, enums and validation rules.

This module drives three things so they can never drift apart:
  * the MySQL DDL (sql/schema.sql)
  * the data dictionary (docs/data_dictionary.md)
  * structural validation (PK uniqueness, FK integrity, required fields, enums, ID patterns)

Tables are listed in dependency (load) order: master -> bridge -> transaction -> event.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------
# Enumerations (allowed values)
# --------------------------------------------------------------------------------------
FUNCTION_GROUPS = ["BUSINESS", "HR", "IT", "FACILITIES"]
JOB_LEVELS = ["L1", "L2", "L3", "L4", "L5", "L6", "L7"]
LAPTOP_CLASSES = ["NONE", "STANDARD", "ENGINEERING"]
PRIVILEGE_LEVELS = ["STANDARD", "ELEVATED", "PRIVILEGED"]
APPROVER_TYPES = ["MANAGER", "MANAGER_AND_SYSTEM_OWNER"]
EMPLOYMENT_TYPES = ["FULL_TIME", "CONTRACTOR", "INTERN"]
WORK_LOCATIONS = ["ONSITE", "HYBRID", "REMOTE"]
COHORTS = ["EXISTING_STAFF", "NEW_HIRE"]
EMPLOYMENT_STATUSES = ["PRE_BOARDING", "ACTIVE", "TERMINATED", "WITHDRAWN"]
MANAGER_LEVELS = ["TEAM_LEAD", "SENIOR_MANAGER", "DIRECTOR", "EXECUTIVE"]
MANAGER_STATUSES = ["ACTIVE", "INACTIVE"]
ROLE_CHANGE_REASONS = ["HIRE", "PROMOTION", "TRANSFER"]
CASE_STATUSES = ["OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED"]
OWNER_TEAMS = ["HR", "IT", "FACILITIES", "MANAGER", "EMPLOYEE"]
TASK_STATUSES = ["BLOCKED", "READY", "IN_PROGRESS", "COMPLETED", "CANCELLED"]
DEPENDENCY_TYPES = ["FINISH_TO_START"]
REQUEST_REASONS = ["NEW_HIRE", "ROLE_CHANGE", "EXISTING_BASELINE"]
APPROVAL_STATUSES = ["PENDING", "APPROVED", "REJECTED"]
GRANT_STATUSES = ["REQUESTED", "APPROVED", "REJECTED", "ACTIVE", "REVOKED", "CANCELLED"]
REVOCATION_REASONS = ["TERMINATION", "ROLE_CHANGE", "CONTRACT_END"]
ASSET_TYPES = ["LAPTOP", "MONITOR", "PHONE", "SECURITY_KEY"]
ASSET_STATUSES = ["IN_STOCK", "ASSIGNED", "LOST", "RETIRED"]
ASSIGNMENT_STATUSES = ["ASSIGNED", "DELIVERED", "ACKNOWLEDGED", "RETURNED", "LOST"]
EXIT_TYPES = ["VOLUNTARY", "INVOLUNTARY", "CONTRACT_END", "URGENT_TERMINATION"]
ACTOR_ROLES = ["EMPLOYEE", "MANAGER", "HR_PARTNER", "IT_OPS_PARTNER", "SYSTEM_ADMIN", "SYSTEM"]

ONBOARDING_TASK_CODES = [
    "ONB_CONTRACT", "ONB_BACKGROUND_CHECK", "ONB_HRIS_RECORD", "ONB_ACCESS_APPROVAL",
    "ONB_IDENTITY_SSO", "ONB_APP_ACCESS", "ONB_WORKSPACE_BADGE", "ONB_EQUIPMENT_SHIP",
    "ONB_EQUIPMENT_ACK", "ONB_PRIVILEGED_TRAINING",
]
OFFBOARDING_TASK_CODES = [
    "OFF_CONFIRM_EXIT", "OFF_REVOKE_ACCESS", "OFF_RECOVER_EQUIPMENT", "OFF_DEACTIVATE_BADGE",
    "OFF_FINAL_PAY_EXIT_INTERVIEW", "OFF_ACCESS_RECONCILIATION",
]
TASK_CODES = ONBOARDING_TASK_CODES + OFFBOARDING_TASK_CODES

AUDIT_ENTITY_TYPES = [
    "employee", "onboarding_case", "task", "access_grant", "asset", "asset_assignment",
    "offboarding_case",
]
AUDIT_EVENT_TYPES = [
    "EMPLOYEE_HIRED", "EMPLOYEE_STARTED", "EMPLOYEE_TERMINATED", "OFFER_WITHDRAWN",
    "ROLE_CHANGED", "START_DATE_CHANGED",
    "CASE_CREATED", "CASE_COMPLETED", "CASE_CANCELLED",
    "TASK_STARTED", "TASK_COMPLETED", "TASK_CANCELLED",  # tasks are instantiated with CASE_CREATED
    "GRANT_REQUESTED", "GRANT_APPROVED", "GRANT_REJECTED", "GRANT_PROVISIONED", "GRANT_REVOKED",
    "GRANT_CANCELLED", "ACCESS_OVERRIDE_APPROVED",
    "ASSET_PURCHASED", "ASSET_ASSIGNED", "ASSET_DELIVERED", "ASSET_ACKNOWLEDGED",
    "ASSET_RETURNED", "ASSET_LOST", "ASSET_RETIRED",
]


# --------------------------------------------------------------------------------------
# Schema model
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Field:
    name: str
    type: str                      # str | int | decimal | bool | date | datetime
    description: str
    required: bool = True
    unique: bool = False
    enum: list[str] | None = None
    fk: str | None = None          # "table.column"
    pattern: str | None = None     # key into ids.PATTERNS
    units: str | None = None
    rule: str | None = None        # human-readable validation rule
    derived: bool = False          # computed from other records, never randomly generated
    length: int = 40               # VARCHAR length for str fields


@dataclass(frozen=True)
class Table:
    name: str
    entity: str                    # business entity name from the brief
    kind: str                      # master | bridge | transaction | event
    description: str
    pk: str
    fields: list[Field]
    unique_together: list[tuple[str, ...]] = field(default_factory=list)
    lifecycle: str | None = None   # documented state machine

    def field(self, name: str) -> Field:
        return next(f for f in self.fields if f.name == name)

    @property
    def columns(self) -> list[str]:
        return [f.name for f in self.fields]


F = Field  # short alias for readability below

TABLES: list[Table] = [
    # ------------------------------------------------------------------ master
    Table(
        "department", "Department", "master",
        "Organizational unit. HR, IT and Facilities departments supply the workflow partners.",
        "department_id",
        [
            F("department_id", "str", "Primary key.", unique=True, pattern="department_id"),
            F("department_name", "str", "Display name.", unique=True, length=80),
            F("function_group", "str", "Which function the department belongs to.", enum=FUNCTION_GROUPS),
            F("cost_center", "str", "Finance cost center code (CC-####).", unique=True, rule="matches ^CC-\\d{4}$"),
        ],
    ),
    Table(
        "role", "Role", "master",
        "Job role template. Drives the access template, equipment class and onboarding tasks.",
        "role_id",
        [
            F("role_id", "str", "Primary key.", unique=True, pattern="role_id"),
            F("role_title", "str", "Job title.", length=80),
            F("department_id", "str", "Owning department.", fk="department.department_id"),
            F("job_level", "str", "Seniority band.", enum=JOB_LEVELS),
            F("is_people_manager", "bool", "Role manages people (holder gets a Manager record)."),
            F("laptop_class", "str", "Laptop issued to the role.", enum=LAPTOP_CLASSES),
            F("contractor_eligible", "bool", "Role can be filled by a contractor."),
        ],
        unique_together=[("role_title", "department_id")],
    ),
    Table(
        "access_role", "AccessRole", "master",
        "Catalog of grantable system entitlements.",
        "access_role_id",
        [
            F("access_role_id", "str", "Primary key.", unique=True, pattern="access_role_id"),
            F("access_role_name", "str", "Entitlement name.", unique=True, length=80),
            F("target_system", "str", "System the entitlement lives in.", length=60),
            F("privilege_level", "str", "Risk tier.", enum=PRIVILEGE_LEVELS),
            F("approver_type", "str", "Who must approve before provisioning.", enum=APPROVER_TYPES),
            F("contractor_allowed", "bool", "Contractors/interns may hold it.",
              rule="PRIVILEGED entitlements are never contractor_allowed"),
        ],
    ),
    Table(
        "role_access_template", "Role <-> AccessRole template", "bridge",
        "N:M bridge: which entitlements each role receives by default.",
        "template_id",
        [
            F("template_id", "str", "Primary key.", unique=True, pattern="template_id"),
            F("role_id", "str", "Role.", fk="role.role_id"),
            F("access_role_id", "str", "Entitlement.", fk="access_role.access_role_id"),
            F("is_mandatory", "bool", "Requested for every hire in the role (else requested optionally)."),
        ],
        unique_together=[("role_id", "access_role_id")],
    ),
    Table(
        "employee", "Employee", "master",
        "Employee or candidate (pre-boarding). Status is derived from case records.",
        "employee_id",
        [
            F("employee_id", "str", "Primary key.", unique=True, pattern="employee_id"),
            F("first_name", "str", "Synthetic first name.", length=60),
            F("last_name", "str", "Synthetic last name.", length=60),
            F("work_email", "str", "Synthetic address on reserved domain example.com.", unique=True, length=120,
              rule="lowercase; ends with @example.com"),
            F("employment_type", "str", "Contract type.", enum=EMPLOYMENT_TYPES),
            F("cohort", "str", "Existing staff (hired before 2026) or 2026 new hire.", enum=COHORTS),
            F("department_id", "str", "Current department.", fk="department.department_id", derived=True,
              rule="= department of latest employee_role_history row"),
            F("role_id", "str", "Current role.", fk="role.role_id", derived=True,
              rule="= role of latest employee_role_history row"),
            F("manager_id", "str", "Current line manager (null only for the top executive).",
              required=False, fk="manager.manager_id", derived=True,
              rule="= manager of latest employee_role_history row (offered manager if withdrawn)"),
            F("work_location", "str", "Work pattern.", enum=WORK_LOCATIONS),
            F("hire_date", "date", "Offer accepted / record created.", rule="hire_date <= start_date"),
            F("start_date", "date", "Actual (or currently planned) first day.", derived=True,
              rule="= onboarding_case actual/planned start; existing staff = first role-history effective_from"),
            F("contract_end_date", "date", "Fixed-term end. Required for CONTRACTOR/INTERN.", required=False,
              rule="> start_date; required iff employment_type in (CONTRACTOR, INTERN)"),
            F("termination_date", "date", "Last working date if exited.", required=False, derived=True,
              rule="= offboarding_case.last_working_date"),
            F("employment_status", "str", "Lifecycle status as of the dataset as-of date.", enum=EMPLOYMENT_STATUSES,
              derived=True, rule="derived from onboarding/offboarding cases and as_of_date"),
        ],
        lifecycle="PRE_BOARDING -> ACTIVE -> TERMINATED; PRE_BOARDING -> WITHDRAWN",
    ),
    Table(
        "manager", "Manager", "master",
        "People-manager record. 1:1 with the Employee who holds the manager role.",
        "manager_id",
        [
            F("manager_id", "str", "Primary key.", unique=True, pattern="manager_id"),
            F("employee_id", "str", "Employee who is the manager (1:1).", unique=True, fk="employee.employee_id"),
            F("department_id", "str", "Department managed.", fk="department.department_id"),
            F("manager_level", "str", "Management tier.", enum=MANAGER_LEVELS),
            F("effective_from", "date", "Became a manager."),
            F("effective_to", "date", "Stopped being a manager (null = current).", required=False,
              rule=">= effective_from"),
            F("manager_status", "str", "Derived from effective_to and as-of date.", enum=MANAGER_STATUSES, derived=True),
        ],
    ),
    Table(
        "employee_role_history", "Role change history (effective-dated)", "bridge",
        "Effective-dated Employee <-> Role assignments. Never overwritten; a change closes one row and opens another.",
        "role_history_id",
        [
            F("role_history_id", "str", "Primary key.", unique=True, pattern="role_history_id"),
            F("employee_id", "str", "Employee.", fk="employee.employee_id"),
            F("role_id", "str", "Role held.", fk="role.role_id"),
            F("department_id", "str", "Department during the period.", fk="department.department_id"),
            F("manager_id", "str", "Line manager during the period (null only for the top executive).",
              required=False, fk="manager.manager_id"),
            F("effective_from", "date", "Period start."),
            F("effective_to", "date", "Period end (null = current).", required=False,
              rule=">= effective_from; periods for one employee never overlap"),
            F("change_reason", "str", "Why the period started.", enum=ROLE_CHANGE_REASONS),
        ],
    ),
    # ------------------------------------------------------------------ transactions
    Table(
        "onboarding_case", "OnboardingCase", "transaction",
        "One pre-boarding/onboarding case per new hire (1:1 with Employee for the NEW_HIRE cohort).",
        "onboarding_case_id",
        [
            F("onboarding_case_id", "str", "Primary key.", unique=True, pattern="onboarding_case_id"),
            F("employee_id", "str", "Hire.", unique=True, fk="employee.employee_id"),
            F("manager_id", "str", "Hiring manager (approves access).", fk="manager.manager_id"),
            F("hr_partner_id", "str", "HR partner owning the case.", fk="employee.employee_id"),
            F("it_partner_id", "str", "IT/operations partner provisioning access and equipment.", fk="employee.employee_id"),
            F("case_created_date", "date", "Case opened (offer accepted)."),
            F("original_start_date", "date", "Start date at offer.", rule=">= case_created_date"),
            F("planned_start_date", "date", "Latest agreed start date.", derived=True,
              rule="= last START_DATE_CHANGED event, else original_start_date"),
            F("actual_start_date", "date", "First day worked (null if not started).", required=False,
              rule="<= as_of_date"),
            F("cancelled_date", "date", "Offer withdrawn (case cancelled).", required=False,
              rule="< original_start_date; actual_start_date is null"),
            F("start_delay_days", "int", "Planned minus original start.", units="days", derived=True,
              rule=">= 0"),
            F("total_tasks", "int", "Number of tasks on the case.", units="count", derived=True),
            F("completed_tasks", "int", "Number of COMPLETED tasks.", units="count", derived=True,
              rule="<= total_tasks"),
            F("completion_pct", "decimal", "completed_tasks / total_tasks * 100.", units="percent", derived=True,
              rule="0..100, 2 dp"),
            F("case_status", "str", "Lifecycle status.", enum=CASE_STATUSES, derived=True),
            F("case_completed_date", "date", "Date last task completed.", required=False, derived=True),
        ],
        lifecycle="OPEN -> IN_PROGRESS -> COMPLETED; OPEN|IN_PROGRESS -> CANCELLED (offer withdrawn)",
    ),
    Table(
        "offboarding_case", "OffboardingCase", "transaction",
        "Exit case: drives access revocation and equipment recovery tasks.",
        "offboarding_case_id",
        [
            F("offboarding_case_id", "str", "Primary key.", unique=True, pattern="offboarding_case_id"),
            F("employee_id", "str", "Exiting employee.", unique=True, fk="employee.employee_id"),
            F("initiated_by_manager_id", "str", "Manager who initiated the exit.", fk="manager.manager_id"),
            F("hr_partner_id", "str", "HR partner.", fk="employee.employee_id"),
            F("it_partner_id", "str", "IT partner performing revocation/recovery.", fk="employee.employee_id"),
            F("exit_type", "str", "Exit reason class.", enum=EXIT_TYPES),
            F("is_urgent", "bool", "Urgent termination (same-day revocation SLA).", derived=True,
              rule="= exit_type == URGENT_TERMINATION"),
            F("notice_date", "date", "Exit notified.", rule="<= last_working_date"),
            F("last_working_date", "date", "Final day of employment."),
            F("revocation_due_date", "date", "SLA deadline for revoking all access.", derived=True,
              rule="urgent: notice_date; else last_working_date + 1 day"),
            F("grants_to_revoke", "int", "Grants ACTIVE at exit.", units="count", derived=True),
            F("grants_revoked", "int", "Of those, grants revoked.", units="count", derived=True,
              rule="<= grants_to_revoke"),
            F("assets_to_recover", "int", "Assignments open at exit.", units="count", derived=True),
            F("assets_recovered", "int", "Of those, returned.", units="count", derived=True,
              rule="<= assets_to_recover"),
            F("case_status", "str", "Lifecycle status.", enum=CASE_STATUSES, derived=True),
            F("case_completed_date", "date", "Date last task completed.", required=False, derived=True),
        ],
        lifecycle="OPEN -> IN_PROGRESS -> COMPLETED",
    ),
    Table(
        "task", "Task", "transaction",
        "Workflow task on exactly one onboarding OR offboarding case.",
        "task_id",
        [
            F("task_id", "str", "Primary key: TASK-<case-id>-NNN.", unique=True, pattern="task_id"),
            F("onboarding_case_id", "str", "Parent onboarding case.", required=False,
              fk="onboarding_case.onboarding_case_id", rule="exactly one of onboarding/offboarding case id"),
            F("offboarding_case_id", "str", "Parent offboarding case.", required=False,
              fk="offboarding_case.offboarding_case_id"),
            F("task_seq", "int", "Sequence within the case.", units="ordinal", rule=">= 1"),
            F("task_code", "str", "Task template code.", enum=TASK_CODES),
            F("task_name", "str", "Human readable name.", length=100),
            F("owner_team", "str", "Team accountable.", enum=OWNER_TEAMS),
            F("assignee_employee_id", "str", "Person doing the task.", fk="employee.employee_id"),
            F("created_date", "date", "Task instantiated."),
            F("due_date", "date", "SLA due date.", rule=">= created_date"),
            F("started_date", "date", "Work started (all prerequisites complete).", required=False,
              rule=">= created_date; >= completed_date of every prerequisite"),
            F("completed_date", "date", "Completion date.", required=False, rule=">= started_date"),
            F("task_status", "str", "Status as of as_of_date.", enum=TASK_STATUSES, derived=True),
            F("is_overdue", "bool", "Completed after due (or open past due).", derived=True),
        ],
        lifecycle="BLOCKED -> READY -> IN_PROGRESS -> COMPLETED; any open -> CANCELLED",
    ),
    Table(
        "task_dependency", "Dependency", "bridge",
        "N:M bridge Task <-> Task: task_id cannot start until depends_on_task_id is complete. Graph must be acyclic.",
        "dependency_id",
        [
            F("dependency_id", "str", "Primary key.", unique=True, pattern="dependency_id"),
            F("task_id", "str", "Dependent task.", fk="task.task_id"),
            F("depends_on_task_id", "str", "Prerequisite task (same case).", fk="task.task_id",
              rule="!= task_id; same case as task_id"),
            F("dependency_type", "str", "Dependency semantics.", enum=DEPENDENCY_TYPES),
        ],
        unique_together=[("task_id", "depends_on_task_id")],
    ),
    Table(
        "access_grant", "AccessGrant", "transaction",
        "Request -> approval -> provisioning -> revocation of one entitlement for one employee.",
        "grant_id",
        [
            F("grant_id", "str", "Primary key: GRANT-<employee-id>-NNN.", unique=True, pattern="grant_id"),
            F("employee_id", "str", "Grantee.", fk="employee.employee_id"),
            F("access_role_id", "str", "Entitlement.", fk="access_role.access_role_id"),
            F("onboarding_case_id", "str", "Case that requested it.", required=False,
              fk="onboarding_case.onboarding_case_id"),
            F("offboarding_case_id", "str", "Case that revoked it.", required=False,
              fk="offboarding_case.offboarding_case_id"),
            F("request_reason", "str", "Why requested.", enum=REQUEST_REASONS),
            F("requested_date", "date", "Request raised."),
            F("approval_status", "str", "Approval outcome.", enum=APPROVAL_STATUSES),
            F("approved_by_manager_id", "str", "Approving manager.", required=False, fk="manager.manager_id"),
            F("approval_date", "date", "Approval/rejection date.", required=False, rule=">= requested_date"),
            F("provisioned_date", "date", "Access went live.", required=False,
              rule=">= approval_date and >= employee start_date, unless an ACCESS_OVERRIDE_APPROVED event exists"),
            F("override_event_id", "str", "Audit event authorising an approved exception (early access).",
              required=False, fk="audit_event.event_id"),
            F("revoked_date", "date", "Access removed.", required=False, rule=">= provisioned_date"),
            F("revocation_reason", "str", "Why revoked.", required=False, enum=REVOCATION_REASONS),
            F("grant_status", "str", "Lifecycle status as of as_of_date.", enum=GRANT_STATUSES, derived=True),
        ],
        lifecycle="REQUESTED -> APPROVED -> ACTIVE -> REVOKED; REQUESTED -> REJECTED; REQUESTED|APPROVED -> CANCELLED (offer withdrawn)",
    ),
    Table(
        "asset", "Asset", "master",
        "Physical equipment inventory item. Status is derived from the asset's lifecycle events.",
        "asset_id",
        [
            F("asset_id", "str", "Primary key.", unique=True, pattern="asset_id"),
            F("asset_type", "str", "Equipment category.", enum=ASSET_TYPES),
            F("model", "str", "Make/model.", length=80),
            F("serial_number", "str", "Manufacturer serial.", unique=True),
            F("purchase_date", "date", "Acquired."),
            F("purchase_cost", "decimal", "Acquisition cost.", units="USD", rule="> 0, 2 dp"),
            F("asset_status", "str", "Inventory status as of as_of_date.", enum=ASSET_STATUSES, derived=True,
              rule="= latest asset lifecycle event (purchased/assigned/returned/lost)"),
        ],
        lifecycle="IN_STOCK -> ASSIGNED -> IN_STOCK (returned) -> ... | LOST; IN_STOCK -> RETIRED",
    ),
    Table(
        "asset_assignment", "Asset <-> Employee assignment", "bridge",
        "Effective-dated N:M bridge Asset <-> Employee. One asset may be re-issued over time, never concurrently.",
        "assignment_id",
        [
            F("assignment_id", "str", "Primary key.", unique=True, pattern="assignment_id"),
            F("asset_id", "str", "Asset.", fk="asset.asset_id"),
            F("employee_id", "str", "Holder.", fk="employee.employee_id"),
            F("onboarding_case_id", "str", "Case that issued it.", required=False,
              fk="onboarding_case.onboarding_case_id"),
            F("offboarding_case_id", "str", "Case that recovered it.", required=False,
              fk="offboarding_case.offboarding_case_id"),
            F("assigned_date", "date", "Allocated to employee.", rule=">= asset.purchase_date"),
            F("delivered_date", "date", "Delivered to employee.", required=False, rule=">= assigned_date"),
            F("acknowledged_date", "date", "Employee acknowledged receipt.", required=False, rule=">= delivered_date"),
            F("returned_date", "date", "Recovered.", required=False, rule=">= assigned_date"),
            F("lost_reported_date", "date", "Reported lost at recovery (mutually exclusive with returned_date).",
              required=False, rule=">= assigned_date"),
            F("assignment_status", "str", "Status as of as_of_date.", enum=ASSIGNMENT_STATUSES, derived=True),
        ],
        lifecycle="ASSIGNED -> DELIVERED -> ACKNOWLEDGED -> RETURNED | LOST",
    ),
    # ------------------------------------------------------------------ event history
    Table(
        "audit_event", "AuditEvent", "event",
        "Append-only history of every state change, decision, exception and override.",
        "event_id",
        [
            F("event_id", "str", "Primary key (chronological).", unique=True, pattern="event_id"),
            F("event_timestamp", "datetime", "When it happened.", units="local time"),
            F("entity_type", "str", "Table of the entity changed.", enum=AUDIT_ENTITY_TYPES),
            F("entity_id", "str", "PK of the entity changed.", length=60, rule="exists in entity_type table"),
            F("event_type", "str", "What happened.", enum=AUDIT_EVENT_TYPES),
            F("actor_id", "str", "Employee who acted (null for SYSTEM).", required=False, fk="employee.employee_id"),
            F("actor_role", "str", "Capacity the actor acted in.", enum=ACTOR_ROLES),
            F("from_state", "str", "State before.", required=False),
            F("to_state", "str", "State after.", required=False),
            F("related_case_id", "str", "Onboarding/offboarding case context.", required=False, length=60),
            F("reason", "str", "Free-text justification.", required=False, length=200),
        ],
    ),
]

TABLE_BY_NAME: dict[str, Table] = {t.name: t for t in TABLES}
LOAD_ORDER: list[str] = [t.name for t in TABLES]


def foreign_keys() -> list[tuple[str, str, str, str]]:
    """All FKs as (child_table, child_column, parent_table, parent_column)."""
    out = []
    for t in TABLES:
        for f in t.fields:
            if f.fk:
                pt, pc = f.fk.split(".")
                out.append((t.name, f.name, pt, pc))
    return out


def deferred_foreign_keys() -> list[tuple[str, str, str, str]]:
    """FKs that point to a table loaded at the same position or later.

    These cannot be declared inline in CREATE TABLE (forward reference / cycle), so the DDL
    creates them with ALTER TABLE ... ADD CONSTRAINT after all rows are loaded; MySQL then
    validates every existing row, so integrity is still fully enforced.
      * employee.manager_id        -> manager       (true cycle: manager.employee_id -> employee)
      * access_grant.override_event_id -> audit_event (forward reference; audit_event is loaded last)
    """
    pos = {name: i for i, name in enumerate(LOAD_ORDER)}
    return [fk for fk in foreign_keys() if pos[fk[2]] >= pos[fk[0]]]


def inline_foreign_keys() -> list[tuple[str, str, str, str]]:
    deferred = set(deferred_foreign_keys())
    return [fk for fk in foreign_keys() if fk not in deferred]

# Documented cardinalities (also rendered into docs/er_diagram.md).
RELATIONSHIPS: list[tuple[str, str, str, str]] = [
    # (parent, child, cardinality, note)
    ("department", "role", "1:N", "each role belongs to one department"),
    ("department", "employee", "1:N", "current department"),
    ("role", "employee", "1:N", "current role"),
    ("role", "access_role", "N:M", "via role_access_template"),
    ("employee", "role", "N:M", "over time via employee_role_history"),
    ("employee", "manager", "1:0..1", "a manager is an employee"),
    ("manager", "employee", "1:N", "direct reports (optional for top executive)"),
    ("employee", "onboarding_case", "1:0..1", "only NEW_HIRE cohort"),
    ("employee", "offboarding_case", "1:0..1", "only leavers"),
    ("onboarding_case", "task", "1:N", ""),
    ("offboarding_case", "task", "1:N", ""),
    ("task", "task", "N:M", "via task_dependency (DAG)"),
    ("employee", "access_grant", "1:N", ""),
    ("access_role", "access_grant", "1:N", ""),
    ("onboarding_case", "access_grant", "1:N", "optional"),
    ("offboarding_case", "access_grant", "1:N", "optional (revocation)"),
    ("asset", "employee", "N:M", "over time via asset_assignment"),
    ("audit_event", "every entity", "N:1", "polymorphic entity_type + entity_id"),
]
