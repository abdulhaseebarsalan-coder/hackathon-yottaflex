"""Task templates and a dependency-aware scheduler.

A case instantiates a subset of templates. Each task can start only when every prerequisite is
complete (FINISH_TO_START) and, optionally, not before a gate date (e.g. access is never provisioned
before the employee's start date). The scheduler simulates "true" dates, then exposes only what had
happened by the as-of snapshot (or by the cancellation date for a withdrawn offer).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from orchestrator import ids

# anchor keys: created | start | notice | lwd | revocation_due
Offset = tuple[str, int]


@dataclass(frozen=True)
class TaskTemplate:
    code: str
    name: str
    owner_team: str
    deps: tuple[str, ...]
    duration: tuple[int, int]          # days from earliest start to completion
    due: Offset
    not_before: Offset | None = None


ONBOARDING_TEMPLATES = [
    TaskTemplate("ONB_CONTRACT", "Issue and collect signed employment contract", "HR", (), (0, 3), ("created", 5)),
    TaskTemplate("ONB_BACKGROUND_CHECK", "Complete background and right-to-work check", "HR",
                 ("ONB_CONTRACT",), (2, 8), ("start", -5)),
    TaskTemplate("ONB_HRIS_RECORD", "Create HRIS employee record", "HR", ("ONB_CONTRACT",), (0, 2), ("created", 7)),
    TaskTemplate("ONB_ACCESS_APPROVAL", "Approve role-based access request", "MANAGER",
                 ("ONB_HRIS_RECORD",), (0, 5), ("start", -3)),
    TaskTemplate("ONB_IDENTITY_SSO", "Provision identity and SSO account", "IT",
                 ("ONB_HRIS_RECORD", "ONB_ACCESS_APPROVAL"), (0, 1), ("start", 0), ("start", 0)),
    TaskTemplate("ONB_PRIVILEGED_TRAINING", "Complete privileged-access security training", "EMPLOYEE",
                 ("ONB_IDENTITY_SSO",), (1, 5), ("start", 7), ("start", 0)),
    TaskTemplate("ONB_APP_ACCESS", "Provision approved application access", "IT",
                 ("ONB_IDENTITY_SSO", "ONB_PRIVILEGED_TRAINING"), (0, 3), ("start", 2), ("start", 0)),
    TaskTemplate("ONB_WORKSPACE_BADGE", "Allocate workspace and building badge", "FACILITIES",
                 ("ONB_BACKGROUND_CHECK",), (1, 4), ("start", -1)),
    TaskTemplate("ONB_EQUIPMENT_SHIP", "Prepare and deliver equipment", "IT",
                 ("ONB_HRIS_RECORD",), (2, 6), ("start", -1), ("start", -10)),
    TaskTemplate("ONB_EQUIPMENT_ACK", "Acknowledge equipment receipt and acceptable-use policy", "EMPLOYEE",
                 ("ONB_EQUIPMENT_SHIP", "ONB_IDENTITY_SSO"), (0, 4), ("start", 3), ("start", 0)),
]

OFFBOARDING_TEMPLATES = [
    TaskTemplate("OFF_CONFIRM_EXIT", "Confirm exit details and last working date", "HR", (), (0, 2), ("notice", 2)),
    TaskTemplate("OFF_REVOKE_ACCESS", "Revoke all system access", "IT",
                 ("OFF_CONFIRM_EXIT",), (0, 1), ("revocation_due", 0), ("lwd", 0)),
    TaskTemplate("OFF_RECOVER_EQUIPMENT", "Recover issued equipment", "IT",
                 ("OFF_CONFIRM_EXIT",), (0, 8), ("lwd", 10), ("lwd", 0)),
    TaskTemplate("OFF_DEACTIVATE_BADGE", "Deactivate building badge", "FACILITIES",
                 ("OFF_CONFIRM_EXIT",), (0, 1), ("lwd", 1), ("lwd", 0)),
    TaskTemplate("OFF_FINAL_PAY_EXIT_INTERVIEW", "Final pay and exit interview", "HR",
                 ("OFF_CONFIRM_EXIT",), (0, 5), ("lwd", 5), ("lwd", -3)),
    TaskTemplate("OFF_ACCESS_RECONCILIATION", "Reconcile access and equipment; sign off exit", "IT",
                 ("OFF_REVOKE_ACCESS", "OFF_RECOVER_EQUIPMENT"), (0, 2), ("lwd", 12)),
]


@dataclass
class ScheduledCase:
    tasks: list[dict]
    dependencies: list[dict]
    true_start: dict[str, date | None]
    true_done: dict[str, date | None]
    cutoff: date
    # Timestamps of the recorded (visible) start / completion. Downstream events (grants, assets,
    # case roll-ups) are timed relative to these so the audit trail reads in causal order.
    started_ts: dict[str, datetime] = field(default_factory=dict)
    completed_ts: dict[str, datetime] = field(default_factory=dict)

    def visible(self, d: date | None) -> date | None:
        """A simulated date is recorded only if it happened by the snapshot/cancellation cutoff."""
        return d if d is not None and d <= self.cutoff else None

    def started(self, code: str) -> date | None:
        return self.visible(self.true_start.get(code))

    def done(self, code: str) -> date | None:
        return self.visible(self.true_done.get(code))


def schedule_case(
    rng: random.Random,
    case_id: str,
    case_field: str,
    templates: list[TaskTemplate],
    anchors: dict[str, date],
    as_of: date,
    assignee: dict[str, str],
    dep_counter: ids.Counter,
    cancelled_on: date | None = None,
    never_complete: frozenset[str] = frozenset(),
    duration_override: dict[str, tuple[int, int]] | None = None,
) -> ScheduledCase:
    included = {t.code for t in templates}
    duration_override = duration_override or {}
    created = anchors["created"]
    true_start: dict[str, date | None] = {}
    true_done: dict[str, date | None] = {}

    for t in templates:
        lo, hi = duration_override.get(t.code, t.duration)
        dur = rng.randint(lo, hi)  # always drawn, so optional branches never shift later draws
        deps = [d for d in t.deps if d in included]
        earliest: date | None = created
        for d in deps:
            if true_done[d] is None:
                earliest = None
                break
            earliest = max(earliest, true_done[d])
        if earliest is not None and t.not_before:
            key, off = t.not_before
            earliest = max(earliest, anchors[key] + timedelta(days=off))
        true_start[t.code] = earliest
        true_done[t.code] = None if earliest is None or t.code in never_complete else earliest + timedelta(days=dur)

    cutoff = min(as_of, cancelled_on) if cancelled_on else as_of
    sched = ScheduledCase([], [], true_start, true_done, cutoff)
    task_ids: dict[str, str] = {}
    for seq, t in enumerate(templates, start=1):
        tid = ids.task_id(case_id, seq)
        task_ids[t.code] = tid
        key, off = t.due
        sched.tasks.append({
            "task_id": tid,
            "onboarding_case_id": case_id if case_field == "onboarding_case_id" else None,
            "offboarding_case_id": case_id if case_field == "offboarding_case_id" else None,
            "task_seq": seq, "task_code": t.code, "task_name": t.name, "owner_team": t.owner_team,
            "assignee_employee_id": assignee[t.owner_team], "created_date": created,
            "due_date": max(created, anchors[key] + timedelta(days=off)),
            "started_date": sched.started(t.code), "completed_date": sched.done(t.code),
            "task_status": None, "is_overdue": None,
        })
        for d in t.deps:
            if d in included:
                sched.dependencies.append({
                    "dependency_id": ids.dependency_id(dep_counter.next()),
                    "task_id": tid, "depends_on_task_id": task_ids[d], "dependency_type": "FINISH_TO_START",
                })

    # Timestamps: work starts from 08:00 and never before a same-day prerequisite finished;
    # same-day completion takes 10-60 minutes. Templates are in dependency order.
    for t in templates:
        s, c = sched.started(t.code), sched.done(t.code)
        if s is None:
            continue
        start_ts = datetime.combine(s, time(8, 0)) + timedelta(minutes=rng.randint(0, 120))
        for d in t.deps:
            dep_done = sched.completed_ts.get(d)
            if d in included and dep_done is not None and dep_done.date() == s:
                start_ts = max(start_ts, dep_done + timedelta(minutes=rng.randint(5, 20)))
        sched.started_ts[t.code] = start_ts
        if c is not None:
            if c == s:
                done_ts = start_ts + timedelta(minutes=rng.randint(10, 60))
            else:
                done_ts = datetime.combine(c, time(9, 0)) + timedelta(minutes=rng.randint(0, 420))
            sched.completed_ts[t.code] = done_ts
            assert done_ts.date() == c, "timestamp must stay on the recorded completion date"
        assert start_ts.date() == s, "timestamp must stay on the recorded start date"
    return sched
