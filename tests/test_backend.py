"""Backend tier - FastAPI endpoints. Runs the real Uvicorn server on a free port with a fake database
(no MySQL needed) and calls it over HTTP with the standard library.
"""

import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

uvicorn = pytest.importorskip("uvicorn")
pytest.importorskip("fastapi")

from backend import main  # noqa: E402
from backend import repository as repo  # noqa: E402

GT = [
    {"anomaly_id": "ANOM-0001", "issue_type": "TERMINATED_EMPLOYEE_RETAINS_GRANT", "sub_type": "VOLUNTARY",
     "severity": "CRITICAL", "rule_id": "RULE-ACCESS-02", "entity_type": "access_grant",
     "entity_id": "GRANT-EMP-000002-001", "detection_key": "GRANT-EMP-000002-001", "employee_id": "EMP-000002",
     "case_id": "OFF-2026-000001", "related_ids": [], "field_changes": [], "expected_decision": "REVOKE",
     "description": "d"},
    {"anomaly_id": "ANOM-0002", "issue_type": "TASK_DEPENDENCY_CYCLE", "sub_type": "BACK_EDGE",
     "severity": "MEDIUM", "rule_id": "RULE-TASK-01", "entity_type": "task_dependency", "entity_id": "DEPN-000009",
     "detection_key": "ONB-2026-000001", "employee_id": "EMP-000003", "case_id": "ONB-2026-000001",
     "related_ids": [], "field_changes": [], "expected_decision": "REMOVE", "description": "d"},
]
EMP = {"employee_id": "EMP-000002", "first_name": "Ana", "last_name": "Lee", "employment_status": "TERMINATED"}


class FakeDB:
    """Answers by query name; records every call so tests can inspect parameters."""

    def __init__(self, fail: bool = False):
        self.calls, self.fail = [], fail

    def fetch(self, name, sql, params=None, session_vars=None):
        self.calls.append((name, params, session_vars))
        if self.fail:
            raise ConnectionError("Can't connect to MySQL")
        if name == "health":
            return [{"version": "8.4.0", "db": "test_db", "tables": 15}]
        if name == "summary":
            return [{"grp": "row_count", "k": "employee", "n": 3},
                    {"grp": "employees_by_status", "k": "ACTIVE", "n": 2},
                    {"grp": "employees_by_status", "k": "TERMINATED", "n": 1},
                    {"grp": "kpi", "k": "tasks_completed", "n": 4},
                    {"grp": "kpi", "k": "tasks_completed_on_time", "n": 3}]
        if name == "check:TERMINATED_EMPLOYEE_RETAINS_GRANT":
            return [{"grant_id": "GRANT-EMP-000002-001"}]
        if name == "check:TASK_DEPENDENCY_CYCLE":
            return [{"case_id": "ONB-2026-000001"}, {"case_id": "ONB-2026-000777"}]   # one unexpected
        if name.startswith("check:"):
            return []
        if name == "employee_count":
            return [{"n": 1}]
        if name == "employee_list":
            return [EMP]
        if name == "employee_profile":
            return [EMP] if params["id"] == "EMP-000002" else []
        if name in ("employee_onboarding_case", "employee_offboarding_case"):
            return [{"case": name}] if name.endswith("offboarding_case") else []
        return [{"row": name}]


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    d = tmp_path_factory.mktemp("ds")
    (d / "ground_truth").mkdir()
    (d / "manifest.json").write_text(json.dumps({"dataset": {"as_of_date": "2026-10-22"}}))
    (d / "ground_truth" / "anomalies.json").write_text(json.dumps({"anomalies": GT}))
    return d


@pytest.fixture(scope="module")
def server(dataset):
    fake = {"db": FakeDB()}
    main.app.dependency_overrides[main.get_db] = lambda: fake["db"]
    import os
    os.environ["DATASET_DIR"] = str(dataset)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", fake
    srv.should_exit = True
    thread.join(timeout=5)
    main.app.dependency_overrides.clear()
    os.environ.pop("DATASET_DIR", None)


def get(base, path, headers=None):
    req = urllib.request.Request(base + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), dict(e.headers)


# ------------------------------------------------------------------ endpoints
def test_health(server):
    base, _ = server
    status, body, _ = get(base, "/api/health")
    assert status == 200 and body == {"status": "ok", "database": "test_db", "mysql_version": "8.4.0", "tables": 15}


def test_summary_groups_counts_and_computes_kpis(server):
    base, _ = server
    status, body, _ = get(base, "/api/summary")
    assert status == 200
    assert body["row_count"] == {"employee": 3}
    assert body["employees_by_status"] == {"ACTIVE": 2, "TERMINATED": 1}
    assert body["kpis"]["task_on_time_rate"] == 0.75


def test_anomalies_run_sql_checks_and_join_ground_truth(server):
    base, fake = server
    status, body, _ = get(base, "/api/anomalies")
    assert status == 200 and body["as_of_date"] == "2026-10-22"
    s = {r["issue_type"]: r for r in body["summary"]}
    assert len(s) == 5 and s["TERMINATED_EMPLOYEE_RETAINS_GRANT"] == {
        "issue_type": "TERMINATED_EMPLOYEE_RETAINS_GRANT", "found_in_db": 1, "ground_truth": 1, "match": True}
    assert s["TASK_DEPENDENCY_CYCLE"]["match"] is False            # 2 found vs 1 labelled
    assert all(i["confirmed_in_db"] for i in body["items"])
    assert body["unexpected_findings"] == [{"issue_type": "TASK_DEPENDENCY_CYCLE", "detection_key": "ONB-2026-000777"}]
    checks = [c for c in fake["db"].calls if c[0].startswith("check:")]
    assert len(checks) >= 5 and all(c[2] == {"as_of": "2026-10-22"} for c in checks)


def test_anomalies_filters(server):
    base, _ = server
    _, body, _ = get(base, "/api/anomalies?severity=CRITICAL")
    assert [i["anomaly_id"] for i in body["items"]] == ["ANOM-0001"]
    _, body, _ = get(base, "/api/anomalies?issue_type=TASK_DEPENDENCY_CYCLE")
    assert body["total"] == 1 and body["items"][0]["severity"] == "MEDIUM"


def test_employee_search_passes_user_input_only_as_parameters(server):
    base, fake = server
    q = "50%_o'brien"
    status, body, _ = get(base, "/api/employees?q=" + urllib.parse.quote(q) + "&status=TERMINATED&limit=10&offset=5")
    assert status == 200 and body["total"] == 1 and body["items"][0]["employee_id"] == "EMP-000002"
    name, params, _ = next(c for c in reversed(fake["db"].calls) if c[0] == "employee_list")
    assert params == {"q": q, "like": "%50\\%\\_o'brien%", "status": "TERMINATED", "limit": 10, "offset": 5}
    assert q not in repo.EMPLOYEE_LIST_SQL                         # never interpolated into SQL text


def test_employee_detail_and_not_found(server):
    base, _ = server
    status, body, _ = get(base, "/api/employees/EMP-000002")
    assert status == 200 and body["profile"]["first_name"] == "Ana"
    assert set(body) == {"profile", "role_history", "onboarding_case", "offboarding_case", "tasks",
                         "access_grants", "assets", "audit_trail"}
    assert body["onboarding_case"] is None and body["offboarding_case"] == {"case": "employee_offboarding_case"}
    status, body, _ = get(base, "/api/employees/EMP-999999")
    assert status == 404 and body["detail"] == "EMP-999999 not found"


@pytest.mark.parametrize("path", [
    "/api/employees/" + urllib.parse.quote("abc'; DROP TABLE employee; --"),
    "/api/employees/EMP-12",
    "/api/employees?status=BOGUS",
    "/api/employees?limit=0",
    "/api/employees?limit=5000",
    "/api/employees?q=" + "x" * 61,
    "/api/anomalies?severity=EXTREME",
    "/api/anomalies?issue_type=NOPE",
])
def test_invalid_input_is_rejected_before_reaching_the_database(server, path):
    base, fake = server
    before = len(fake["db"].calls)
    status, _, _ = get(base, path)
    assert status == 422 and len(fake["db"].calls) == before


def test_cors_allows_frontend_origin_only(server):
    base, _ = server
    _, _, headers = get(base, "/api/health", {"Origin": "http://127.0.0.1:5500"})
    assert headers.get("access-control-allow-origin") == "http://127.0.0.1:5500"
    _, _, headers = get(base, "/api/health", {"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in headers}


def test_database_failure_returns_503_without_leaking_details(server):
    base, fake = server
    fake["db"] = FakeDB(fail=True)
    try:
        status, body, _ = get(base, "/api/summary")
        assert status == 503 and "ConnectionError" in body["detail"] and "password" not in json.dumps(body).lower()
    finally:
        fake["db"] = FakeDB()


def test_openapi_docs_list_the_five_endpoints(server):
    base, _ = server
    _, spec, _ = get(base, "/openapi.json")
    assert set(spec["paths"]) == {"/api/health", "/api/summary", "/api/anomalies", "/api/employees",
                                  "/api/employees/{employee_id}"}
    assert all(set(ops) == {"get"} for ops in spec["paths"].values()), "read-only API"


def test_api_never_writes_to_the_database():
    sql = " ".join([repo.SUMMARY_SQL, repo.EMPLOYEE_LIST_SQL, repo.EMPLOYEE_COUNT_SQL, *repo.DETAIL_SQL.values()]).upper()
    assert not any(f" {w} " in f" {sql} " for w in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"))
