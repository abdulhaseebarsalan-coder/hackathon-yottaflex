"""Step 6 - MySQL integration. Unit tests use a fake backend / mocked subprocess (no server needed).

Optional live test: set ORCH_MYSQL_LIVE_TEST=1 plus MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD
and MYSQL_TEST_DATABASE (a disposable database; its project tables are dropped and recreated).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.db import mysql_loader as ml
from orchestrator.export.sql_writer import build_deferred_sql, build_schema_sql
from orchestrator.generator.anomalies import ISSUE_TYPES
from orchestrator.publish import publish
from orchestrator.schema import TABLES, foreign_keys

ROOT = Path(__file__).resolve().parents[1]
CHECKS = (ROOT / "sql" / "queries" / "anomaly_checks.sql").read_text(encoding="utf-8")
SETTINGS = ml.MySQLSettings("127.0.0.1", 3306, "tester", "s3cr3t'\"pw", "proj_db")


# ------------------------------------------------------------------ settings & .env
def test_settings_require_user_and_hide_password(tmp_path):
    with pytest.raises(ValueError, match="MYSQL_USER"):
        ml.settings_from_env({})
    s = ml.settings_from_env({"MYSQL_USER": "u", "MYSQL_PASSWORD": "topsecret"})
    assert (s.host, s.port, s.database) == ("127.0.0.1", 3306, "hackathon_db")
    assert "topsecret" not in s.describe() and "password set" in s.describe()


@pytest.mark.parametrize("db", ["mysql", "SYS", "information_schema", "performance_schema"])
def test_settings_refuse_system_databases(db):
    with pytest.raises(ValueError, match="system database"):
        ml.settings_from_env({"MYSQL_USER": "u", "MYSQL_DATABASE": db})


@pytest.mark.parametrize("db", ["bad-name", "x;DROP", "a b", ""])
def test_settings_reject_unsafe_database_names(db):
    with pytest.raises(ValueError):
        ml.settings_from_env({"MYSQL_USER": "u", "MYSQL_DATABASE": db})


def test_env_file_parsing_and_precedence(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nMYSQL_USER=file_user\nMYSQL_PASSWORD='p@ss word'\nMYSQL_PORT=3310\n\nBROKEN LINE\n")
    assert ml.read_env_file(env)["MYSQL_PASSWORD"] == "p@ss word"
    s = ml.settings_from_env({"MYSQL_USER": "shell_user"}, env_file=env)
    assert s.user == "shell_user" and s.port == 3310 and s.password == "p@ss word"
    assert ml.read_env_file(tmp_path / "missing.env") == {}


def test_env_example_has_no_real_secret_and_env_is_ignored():
    example = ml.read_env_file(ROOT / ".env.example")
    assert example["MYSQL_PASSWORD"] == "" and example["MYSQL_DATABASE"] == "hackathon_db"
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


# ------------------------------------------------------------------ CLI backend (mocked subprocess)
def test_cli_backend_keeps_password_off_the_command_line(monkeypatch):
    seen = {}

    def fake_run(args, input, capture_output, check):
        opt = next(a.split("=", 1)[1] for a in args if a.startswith("--defaults-extra-file="))
        seen.update(args=args, input=input, option_file=opt, option_text=Path(opt).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(args, 0, stdout=b"employee\t5\n", stderr=b"")

    monkeypatch.setattr(ml.subprocess, "run", fake_run)
    rows = ml.CliBackend(SETTINGS, "mysql").query("SELECT 1;", "proj_db")
    assert rows == [("employee", "5")]
    assert seen["args"][1].startswith("--defaults-extra-file=")   # must be the first option
    assert not any("s3cr3t" in a for a in seen["args"])
    assert 'password="s3cr3t\'\\"pw"' in seen["option_text"]
    assert not Path(seen["option_file"]).exists(), "temporary credentials file must be deleted"
    assert "--database=proj_db" in seen["args"] and seen["input"] == b"SELECT 1;"


def test_cli_backend_reports_errors_and_still_deletes_credentials(monkeypatch):
    files = []

    def fake_run(args, **_):
        files.append(next(a.split("=", 1)[1] for a in args if a.startswith("--defaults-extra-file=")))
        return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"ERROR 1045: Access denied")

    monkeypatch.setattr(ml.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Access denied"):
        ml.CliBackend(SETTINGS, "mysql").run_script("SELECT 1;", None)
    assert files and not Path(files[0]).exists()


def test_choose_backend_without_driver_or_client(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymysql", None)          # simulate PyMySQL not installed
    monkeypatch.setattr(ml, "find_mysql_client", lambda environ=None: None)
    with pytest.raises(RuntimeError, match="PyMySQL"):
        ml.choose_backend(SETTINGS)
    monkeypatch.setattr(ml, "find_mysql_client", lambda environ=None: "mysql")
    assert isinstance(ml.choose_backend(SETTINGS), ml.CliBackend)


# ------------------------------------------------------------------ load workflow (fake backend)
class FakeBackend:
    def __init__(self, rows, fks, findings):
        self.rows, self.fks, self.findings, self.scripts = rows, fks, findings, []

    def run_script(self, sql, database):
        self.scripts.append((sql, database))

    def query(self, sql, database):
        if "UNION ALL" in sql and "COUNT(*)" in sql:
            return [(t, str(n)) for t, n in self.rows.items()]
        if "REFERENTIAL_CONSTRAINTS" in sql:
            return [(str(self.fks),)]
        name = next(n for n in ml.parse_named_queries(CHECKS) if ml.parse_named_queries(CHECKS)[n] in sql)
        return [(f"key-{i}",) for i in range(self.findings.get(name, 0))]


@pytest.fixture
def expected_rows():
    return {t.name: i + 1 for i, t in enumerate(TABLES)}


def test_load_runs_scripts_in_dependency_order_and_passes(expected_rows):
    gt = {it: 2 for it in ISSUE_TYPES}
    fake = FakeBackend(expected_rows, len(foreign_keys()), gt)
    result = ml.load_dataset(fake, SETTINGS, "SCHEMA;", "SEED;", "DEFERRED;", expected_rows, CHECKS, "2026-10-22", gt)
    assert [s for s, _ in fake.scripts] == ["CREATE DATABASE IF NOT EXISTS `proj_db` CHARACTER SET utf8mb4;",
                                             "SCHEMA;", "SEED;", "DEFERRED;"]
    assert fake.scripts[0][1] is None and all(db == "proj_db" for _, db in fake.scripts[1:])
    assert result["ok"] and result["row_mismatches"] == {}
    assert set(result["anomaly_checks"]) == set(ISSUE_TYPES)


@pytest.mark.parametrize("break_it", ["rows", "fks", "checks"])
def test_load_fails_on_any_mismatch(expected_rows, break_it):
    gt = {it: 2 for it in ISSUE_TYPES}
    rows = dict(expected_rows, employee=0) if break_it == "rows" else expected_rows
    fks = len(foreign_keys()) - 2 if break_it == "fks" else len(foreign_keys())
    found = dict(gt, TASK_DEPENDENCY_CYCLE=1) if break_it == "checks" else gt
    result = ml.load_dataset(FakeBackend(rows, fks, found), SETTINGS, "S;", "D;", "F;", expected_rows, CHECKS,
                             "2026-10-22", gt)
    assert not result["ok"]


def test_sql_anomaly_checks_cover_every_issue_type():
    assert set(ml.parse_named_queries(CHECKS)) == set(ISSUE_TYPES)
    assert "@as_of" in ml.parse_named_queries(CHECKS)["TERMINATED_EMPLOYEE_RETAINS_GRANT"]


# ------------------------------------------------------------------ load_mysql.py CLI (no connection)
@pytest.fixture(scope="module")
def starter_dir(tmp_path_factory, starter):
    out = tmp_path_factory.mktemp("load") / "starter"
    publish(starter.cfg, out)
    return out


def _cli(args, env_extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("MYSQL_")}
    env.update(env_extra)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "load_mysql.py"), *args],
                          capture_output=True, text=True, cwd=ROOT, env=env)


def test_cli_dry_run_validates_without_connecting(starter_dir, tmp_path):
    p = _cli(["--dir", str(starter_dir), "--env-file", str(tmp_path / "none"), "--dry-run"],
             {"MYSQL_USER": "u", "MYSQL_PASSWORD": "never-printed", "MYSQL_PORT": "1"})
    assert p.returncode == 0, p.stdout + p.stderr
    assert "Dry run OK" in p.stdout and "never-printed" not in p.stdout


def test_cli_requires_credentials_and_confirmation(starter_dir, tmp_path):
    p = _cli(["--dir", str(starter_dir), "--env-file", str(tmp_path / "none")], {})
    assert p.returncode == 2 and "MYSQL_USER" in p.stdout
    p = _cli(["--dir", str(starter_dir), "--env-file", str(tmp_path / "none")], {"MYSQL_USER": "u"})
    assert p.returncode == 2 and "--yes" in p.stdout


def test_cli_refuses_tampered_dataset(tmp_path, starter):
    out = tmp_path / "tampered"
    publish(starter.cfg, out)
    seed = out / "sql" / "seed.sql"
    seed.write_text(seed.read_text(encoding="utf-8") + "\n-- tampered\n", encoding="utf-8")
    p = _cli(["--dir", str(out), "--env-file", str(tmp_path / "none"), "--yes"], {"MYSQL_USER": "u"})
    assert p.returncode == 2 and "sql/seed.sql" in p.stdout


# ------------------------------------------------------------------ optional live MySQL test
@pytest.mark.skipif(os.environ.get("ORCH_MYSQL_LIVE_TEST") != "1" or not os.environ.get("MYSQL_TEST_DATABASE"),
                    reason="set ORCH_MYSQL_LIVE_TEST=1 and MYSQL_TEST_DATABASE to run against a real server")
def test_live_load_into_disposable_database(starter_dir):
    s = ml.settings_from_env(dict(os.environ, MYSQL_DATABASE=os.environ["MYSQL_TEST_DATABASE"]))
    manifest = json.loads((starter_dir / "manifest.json").read_text(encoding="utf-8"))
    records = json.loads((starter_dir / "ground_truth" / "anomalies.json").read_text(encoding="utf-8"))["anomalies"]
    result = ml.load_dataset(ml.choose_backend(s), s, build_schema_sql(), (starter_dir / "sql" / "seed.sql").read_text(encoding="utf-8"),
                             build_deferred_sql(), manifest["row_counts"], CHECKS, manifest["dataset"]["as_of_date"],
                             ml.ground_truth_counts(records))
    assert result["ok"], result
