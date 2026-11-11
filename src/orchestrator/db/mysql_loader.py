"""Load a published dataset into MySQL and verify it.

Credentials come ONLY from environment variables (optionally read from a git-ignored .env file):
    MYSQL_HOST (default 127.0.0.1)   MYSQL_PORT (default 3306)   MYSQL_USER (required)
    MYSQL_PASSWORD (default empty)   MYSQL_DATABASE (default hackathon_db)
    MYSQL_CLIENT (optional path to the mysql command-line client)

Backends:
  * PyMySQL, if installed (pip install PyMySQL)
  * otherwise the standard `mysql` command-line client. The password is passed through a temporary
    option file (--defaults-extra-file, owner-only permissions, deleted immediately), never on the
    command line.

Steps: verify the manifest checksums -> create database if missing -> sql/schema.sql ->
output/<scale>/sql/seed.sql -> sql/deferred_constraints.sql -> verify row counts and FK constraints
-> run the SQL anomaly checks in sql/queries/anomaly_checks.sql and compare with the ground truth.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from glob import glob
from pathlib import Path
from typing import Protocol

from orchestrator.export.sql_writer import split_statements
from orchestrator.schema import TABLES, foreign_keys

PROTECTED_DATABASES = {"mysql", "sys", "information_schema", "performance_schema"}
IDENT_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")


@dataclass(frozen=True)
class MySQLSettings:
    host: str
    port: int
    user: str
    password: str
    database: str

    def describe(self) -> str:
        """Connection summary that never includes the password."""
        return f"{self.user}@{self.host}:{self.port}/{self.database} (password {'set' if self.password else 'empty'})"


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE lines, '#' comments, optional surrounding quotes."""
    values: dict[str, str] = {}
    path = Path(path)
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        values[key.strip()] = val
    return values


def settings_from_env(environ: dict[str, str] | None = None, env_file: Path | None = None) -> MySQLSettings:
    """Process environment wins over the .env file. Raises ValueError with a helpful message."""
    merged = {**(read_env_file(env_file) if env_file else {}), **(dict(os.environ) if environ is None else environ)}
    user = merged.get("MYSQL_USER", "").strip()
    if not user:
        raise ValueError("MYSQL_USER is not set. Copy .env.example to .env and fill in your credentials, "
                         "or export MYSQL_USER / MYSQL_PASSWORD in your shell.")
    database = merged.get("MYSQL_DATABASE", "hackathon_db").strip()
    if not IDENT_RE.match(database):
        raise ValueError(f"MYSQL_DATABASE {database!r} must contain only letters, digits and underscores.")
    if database.lower() in PROTECTED_DATABASES:
        raise ValueError(f"Refusing to load into system database {database!r}.")
    try:
        port = int(merged.get("MYSQL_PORT", "3306"))
    except ValueError as exc:
        raise ValueError("MYSQL_PORT must be an integer.") from exc
    return MySQLSettings(merged.get("MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1", port, user,
                         merged.get("MYSQL_PASSWORD", ""), database)


# ------------------------------------------------------------------ backends
class Backend(Protocol):
    def run_script(self, sql: str, database: str | None) -> None: ...
    def query(self, sql: str, database: str | None) -> list[tuple[str, ...]]: ...


def find_mysql_client(environ: dict[str, str] | None = None) -> str | None:
    env = dict(os.environ) if environ is None else environ
    if env.get("MYSQL_CLIENT"):
        return env["MYSQL_CLIENT"]
    found = shutil.which("mysql")
    if found:
        return found
    candidates = sorted(glob("C:/Program Files/MySQL/*/bin/mysql.exe"), reverse=True)
    return candidates[0] if candidates else None


class CliBackend:
    """Runs SQL through the mysql command-line client; credentials via a temporary option file."""

    def __init__(self, settings: MySQLSettings, client: str) -> None:
        self.s, self.client = settings, client

    def _option_file(self) -> str:
        def esc(v: str) -> str:
            return v.replace("\\", "\\\\").replace('"', '\\"')
        fd, path = tempfile.mkstemp(prefix="mysql-", suffix=".cnf")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f'[client]\nuser="{esc(self.s.user)}"\npassword="{esc(self.s.password)}"\n'
                     f'host="{esc(self.s.host)}"\nport={self.s.port}\n')
        os.chmod(path, 0o600)
        return path

    def _run(self, sql: str, database: str | None, capture: bool) -> str:
        opt = self._option_file()
        try:
            args = [self.client, f"--defaults-extra-file={opt}", "--batch", "--raw", "--skip-column-names",
                    "--default-character-set=utf8mb4"]
            if database:
                args.append(f"--database={database}")
            proc = subprocess.run(args, input=sql.encode("utf-8"), capture_output=True, check=False)
        finally:
            os.remove(opt)
        if proc.returncode != 0:
            raise RuntimeError(f"mysql client failed (exit {proc.returncode}): "
                               f"{proc.stderr.decode('utf-8', 'replace').strip()[:2000]}")
        return proc.stdout.decode("utf-8", "replace") if capture else ""

    def run_script(self, sql: str, database: str | None) -> None:
        self._run(sql, database, capture=False)

    def query(self, sql: str, database: str | None) -> list[tuple[str, ...]]:
        out = self._run(sql, database, capture=True)
        return [tuple(line.split("\t")) for line in out.splitlines() if line.strip()]


class PyMySQLBackend:
    def __init__(self, settings: MySQLSettings) -> None:
        import pymysql  # optional dependency
        self.s, self._pymysql = settings, pymysql

    def _connect(self, database: str | None):
        return self._pymysql.connect(host=self.s.host, port=self.s.port, user=self.s.user, password=self.s.password,
                                     database=database, charset="utf8mb4", autocommit=False)

    def run_script(self, sql: str, database: str | None) -> None:
        conn = self._connect(database)
        try:
            with conn.cursor() as cur:
                for stmt in split_statements(sql):
                    cur.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def query(self, sql: str, database: str | None) -> list[tuple[str, ...]]:
        conn = self._connect(database)
        try:
            rows: list[tuple[str, ...]] = []
            with conn.cursor() as cur:
                for stmt in split_statements(sql):
                    cur.execute(stmt)
                    if cur.description:
                        rows = [tuple("" if v is None else str(v) for v in r) for r in cur.fetchall()]
            return rows
        finally:
            conn.close()


def choose_backend(settings: MySQLSettings) -> Backend:
    try:
        return PyMySQLBackend(settings)
    except ImportError:
        client = find_mysql_client()
        if not client:
            raise RuntimeError("No MySQL driver: install PyMySQL (pip install PyMySQL) or put the `mysql` "
                               "command-line client on PATH (or set MYSQL_CLIENT).") from None
        return CliBackend(settings, client)


# ------------------------------------------------------------------ anomaly checks in SQL
def parse_named_queries(text: str) -> dict[str, str]:
    """Blocks introduced by '-- check: NAME' up to the next marker."""
    out, name, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r"^--\s*check:\s*([A-Z_]+)\s*$", line)
        if m:
            if name:
                out[name] = "\n".join(buf).strip()
            name, buf = m.group(1), []
        elif name:
            buf.append(line)
    if name:
        out[name] = "\n".join(buf).strip()
    return out


# ------------------------------------------------------------------ load + verify
def row_count_sql() -> str:
    return "\nUNION ALL\n".join(f"SELECT '{t.name}', COUNT(*) FROM `{t.name}`" for t in TABLES) + ";"


def fk_count_sql(database: str) -> str:
    return ("SELECT COUNT(*) FROM information_schema.REFERENTIAL_CONSTRAINTS "
            f"WHERE CONSTRAINT_SCHEMA = '{database}';")


def load_dataset(backend: Backend, settings: MySQLSettings, schema_sql: str, seed_sql: str, deferred_sql: str,
                 expected_rows: dict[str, int], checks_sql: str, as_of: str,
                 ground_truth_counts: dict[str, int]) -> dict:
    db = settings.database
    backend.run_script(f"CREATE DATABASE IF NOT EXISTS `{db}` CHARACTER SET utf8mb4;", None)
    backend.run_script(schema_sql, db)
    backend.run_script(seed_sql, db)
    backend.run_script(deferred_sql, db)

    loaded = {name: int(n) for name, n in backend.query(row_count_sql(), db)}
    row_mismatches = {t: {"expected": n, "loaded": loaded.get(t)} for t, n in expected_rows.items() if loaded.get(t) != n}
    fks = int(backend.query(fk_count_sql(db), db)[0][0])

    checks = {}
    for name, sql in parse_named_queries(checks_sql).items():
        rows = backend.query(f"SET @as_of = '{as_of}';\n{sql}", db)
        checks[name] = {"found": len({r[0] for r in rows}), "ground_truth": ground_truth_counts.get(name, 0)}
    return {
        "database": db, "rows_loaded": loaded, "row_mismatches": row_mismatches,
        "foreign_keys": {"expected": len(foreign_keys()), "present": fks},
        "anomaly_checks": checks,
        "ok": not row_mismatches and fks == len(foreign_keys())
              and all(c["found"] == c["ground_truth"] for c in checks.values()),
    }


def ground_truth_counts(records: list[dict]) -> dict[str, int]:
    return dict(Counter(r["issue_type"] for r in records))
