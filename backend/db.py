"""Database access for the API (PyMySQL). Read-only: the API never writes to MySQL.

Settings come from the same place as the loader: environment variables or the git-ignored .env
(MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from orchestrator.db.mysql_loader import MySQLSettings, settings_from_env  # noqa: E402

Row = dict[str, Any]


class Database(Protocol):
    """Anything that can run a named, parameterized SELECT and return dict rows."""

    def fetch(self, name: str, sql: str, params: tuple | dict | None = None,
              session_vars: dict[str, str] | None = None) -> list[Row]: ...


class MySQLDatabase:
    def __init__(self, settings: MySQLSettings | None = None) -> None:
        self.settings = settings or settings_from_env(env_file=ROOT / ".env")

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        import pymysql
        conn = pymysql.connect(host=self.settings.host, port=self.settings.port, user=self.settings.user,
                               password=self.settings.password, database=self.settings.database,
                               charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                               autocommit=True, connect_timeout=5, read_timeout=30)
        try:
            yield conn
        finally:
            conn.close()

    def fetch(self, name: str, sql: str, params: tuple | dict | None = None,
              session_vars: dict[str, str] | None = None) -> list[Row]:
        with self._connection() as conn, conn.cursor() as cur:
            for var, value in (session_vars or {}).items():
                cur.execute(f"SET @{var} = %s", (value,))   # var names are fixed in code, values parameterized
            cur.execute(sql, params)
            return list(cur.fetchall())
