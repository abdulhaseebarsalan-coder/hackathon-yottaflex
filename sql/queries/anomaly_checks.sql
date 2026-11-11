-- SQL versions of the five business rules in src/orchestrator/validation/rules.py.
-- Each '-- check: NAME' block returns the detection key of every violation (one row per key).
-- scripts/load_mysql.py runs them after loading and compares the counts with the ground truth.
-- To run by hand in MySQL Workbench, first: SET @as_of = '2026-10-22';

-- check: ACCESS_BEFORE_START_OR_APPROVAL
-- RULE-ACCESS-01: provisioned before approval, or before the start date without an approved override.
SELECT g.grant_id
FROM access_grant g
JOIN employee e ON e.employee_id = g.employee_id
LEFT JOIN audit_event o
       ON o.event_id = g.override_event_id
      AND o.event_type = 'ACCESS_OVERRIDE_APPROVED'
      AND o.entity_id = g.grant_id
WHERE g.provisioned_date IS NOT NULL
  AND (g.approval_status <> 'APPROVED'
       OR g.approval_date IS NULL
       OR g.provisioned_date < g.approval_date
       OR (g.provisioned_date < e.start_date AND o.event_id IS NULL));

-- check: DUPLICATE_ASSET_ASSIGNMENT
-- RULE-ASSET-01: two assignment periods of the same asset overlap.
SELECT DISTINCT a.asset_id
FROM asset_assignment a
JOIN asset_assignment b
  ON b.asset_id = a.asset_id
 AND b.assignment_id <> a.assignment_id
 AND (b.assigned_date > a.assigned_date
      OR (b.assigned_date = a.assigned_date AND b.assignment_id > a.assignment_id))
WHERE COALESCE(a.returned_date, a.lost_reported_date) IS NULL
   OR b.assigned_date < COALESCE(a.returned_date, a.lost_reported_date);

-- check: TERMINATED_EMPLOYEE_RETAINS_GRANT
-- RULE-ACCESS-02: active grant of a terminated employee after the revocation due date.
SELECT g.grant_id
FROM access_grant g
JOIN employee e ON e.employee_id = g.employee_id
JOIN offboarding_case c ON c.employee_id = e.employee_id
WHERE g.grant_status = 'ACTIVE'
  AND e.employment_status = 'TERMINATED'
  AND CAST(@as_of AS DATE) > c.revocation_due_date;

-- check: TASK_DEPENDENCY_CYCLE
-- RULE-TASK-01: a task can reach itself through its prerequisites.
WITH RECURSIVE reach (start_task, task_id, depth) AS (
    SELECT d.task_id, d.depends_on_task_id, 1
    FROM task_dependency d
    UNION ALL
    SELECT r.start_task, d.depends_on_task_id, r.depth + 1
    FROM reach r
    JOIN task_dependency d ON d.task_id = r.task_id
    WHERE r.depth < 25 AND r.task_id <> r.start_task
)
SELECT DISTINCT COALESCE(t.onboarding_case_id, t.offboarding_case_id) AS case_id
FROM reach r
JOIN task t ON t.task_id = r.start_task
WHERE r.task_id = r.start_task;

-- check: RECOVERED_ASSET_REMAINS_ASSIGNED
-- RULE-ASSET-02: assignment still open although the asset is back in stock / retired / lost.
SELECT s.assignment_id
FROM asset_assignment s
JOIN asset a ON a.asset_id = s.asset_id
WHERE s.returned_date IS NULL
  AND s.lost_reported_date IS NULL
  AND a.asset_status IN ('IN_STOCK', 'RETIRED', 'LOST');
