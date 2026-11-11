"""Step 6 - JSON / SQL exports, checksum manifest, reproducibility and committed schema artifacts."""

import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from orchestrator.datasets import load_tables
from orchestrator.export.docs_writer import build_data_dictionary, build_er_diagram
from orchestrator.export.json_writer import write_json
from orchestrator.export.manifest import verify_manifest
from orchestrator.export.sql_writer import (
    MAX_IDENT, build_deferred_sql, build_schema_sql, build_seed_sql, split_statements, sql_literal,
)
from orchestrator.publish import publish
from orchestrator.schema import TABLES, deferred_foreign_keys, foreign_keys

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def published(tmp_path_factory, starter):
    out = tmp_path_factory.mktemp("pub") / "starter"
    result = publish(starter.cfg, out)
    return out, result


# ------------------------------------------------------------------ JSON
def test_json_round_trips_every_table_with_types(published):
    out, _ = published
    csv_tables = load_tables(out / "csv")
    for t in TABLES:
        payload = json.loads((out / "json" / f"{t.name}.json").read_text(encoding="utf-8"))
        assert payload["table"] == t.name and payload["primary_key"] == t.pk and payload["columns"] == t.columns
        assert payload["row_count"] == len(payload["rows"]) == len(csv_tables[t.name])
        for jrow, crow in zip(payload["rows"], csv_tables[t.name]):
            assert list(jrow) == t.columns
            for f in t.fields:
                v, c = jrow[f.name], crow[f.name]
                if c is None:
                    assert v is None
                elif f.type == "date":
                    assert date.fromisoformat(v) == c
                elif f.type == "datetime":
                    assert datetime.strptime(v, "%Y-%m-%d %H:%M:%S") == c
                elif f.type == "bool":
                    assert v is c
                elif f.type == "decimal":
                    assert isinstance(v, float) and v == round(c, 2)
                else:
                    assert v == c and type(v) is type(c)


def test_json_export_is_deterministic(tmp_path, starter):
    a, b = write_json(starter.tables, tmp_path / "a"), write_json(starter.tables, tmp_path / "b")
    assert [p.read_bytes() for p in a] == [p.read_bytes() for p in b]


# ------------------------------------------------------------------ SQL DDL
def test_schema_sql_defines_every_table_key_and_constraint():
    ddl = build_schema_sql()
    created = re.findall(r"CREATE TABLE `(\w+)`", ddl)
    assert created == [t.name for t in TABLES]
    deferred = set(deferred_foreign_keys())
    for t in TABLES:
        block = ddl.split(f"CREATE TABLE `{t.name}` (")[1].split(") ENGINE=InnoDB")[0]
        assert f"PRIMARY KEY (`{t.pk}`)" in block
        for f in t.fields:
            assert f"`{f.name}` " in block
            assert (" NOT NULL " in block.split(f"`{f.name}` ")[1].split("\n")[0]) == f.required
            if f.enum:
                assert f"CONSTRAINT `ck_{t.name}_{f.name}`" in block
            if f.unique and f.name != t.pk:
                assert f"UNIQUE KEY `uq_{t.name}_{f.name}`" in block
    for child, col, parent, pcol in foreign_keys():
        fk = f"FOREIGN KEY (`{col}`) REFERENCES `{parent}` (`{pcol}`)"
        block = ddl.split(f"CREATE TABLE `{child}` (")[1].split(") ENGINE=InnoDB")[0]
        if (child, col, parent, pcol) in deferred:
            assert fk not in block and f"ALTER TABLE `{child}` ADD CONSTRAINT `fk_{child}_{col}` {fk}" in build_deferred_sql()
        else:  # inline FKs only reference tables created earlier
            assert fk in block
            assert created.index(parent) < created.index(child)
    assert all(len(n) <= MAX_IDENT for n in re.findall(r"CONSTRAINT `(\w+)`|KEY `(\w+)`", ddl) for n in n if n)


def test_deferred_sql_adds_exactly_the_forward_references():
    alters = re.findall(r"ALTER TABLE `(\w+)` ADD CONSTRAINT `\w+` FOREIGN KEY \(`(\w+)`\) REFERENCES `(\w+)`",
                        build_deferred_sql())
    assert set(alters) == {(c, col, p) for c, col, p, _ in deferred_foreign_keys()} and len(alters) == 2


# ------------------------------------------------------------------ SQL seed
@pytest.mark.parametrize("value, ftype, expected", [
    (None, "str", "NULL"), (True, "bool", "1"), (False, "bool", "0"), (7, "int", "7"),
    (1240.0, "decimal", "1240.00"), (date(2026, 1, 2), "date", "'2026-01-02'"),
    (datetime(2026, 1, 2, 3, 4, 5), "datetime", "'2026-01-02 03:04:05'"),
    ("O'Brien", "str", "'O''Brien'"), ("a\\b", "str", "'a\\\\b'"),
])
def test_sql_literal_escaping(value, ftype, expected):
    assert sql_literal(value, ftype) == expected


def test_split_statements_respects_quotes_and_comments():
    script = "-- c; x\nINSERT INTO `t;x` VALUES ('a;b', 'it''s', 'x\\';y');\nSELECT 1; -- tail; comment\nSELECT 2"
    assert split_statements(script) == ["INSERT INTO `t;x` VALUES ('a;b', 'it''s', 'x\\';y')", "SELECT 1", "SELECT 2"]


def test_seed_sql_contains_every_row_in_dependency_order(starter):
    seed = build_seed_sql(starter.tables, "test")
    stmts = [s for s in split_statements(seed) if s.startswith("INSERT")]
    order, counts = [], {}
    for s in stmts:
        table = re.match(r"INSERT INTO `(\w+)`", s).group(1)
        if table not in order:
            order.append(table)
        counts[table] = counts.get(table, 0) + s.count("),\n(") + 1
    assert order == [t.name for t in TABLES if starter.tables[t.name]]
    assert counts == {t.name: len(starter.tables[t.name]) for t in TABLES if starter.tables[t.name]}
    assert seed.rstrip().endswith("COMMIT;") and "START TRANSACTION;" in seed


# ------------------------------------------------------------------ manifest
def test_manifest_lists_and_verifies_every_published_file(published):
    out, result = published
    m = result["manifest"]
    paths = {f["path"] for f in m["files"]}
    assert {f"csv/{t.name}.csv" for t in TABLES} <= paths and {f"json/{t.name}.json" for t in TABLES} <= paths
    assert {"sql/seed.sql", "ground_truth/anomalies.csv", "ground_truth/anomalies.json",
            "reports/evaluation_report.json", "reports/evaluation_report.md"} <= paths
    assert "manifest.json" not in paths and all(not p.startswith("/") and ":" not in p for p in paths)
    assert all(re.fullmatch(r"[0-9a-f]{64}", f["sha256"]) for f in m["files"])
    rows = {f["path"]: f.get("rows") for f in m["files"]}
    for t in TABLES:
        assert rows[f"csv/{t.name}.csv"] == rows[f"json/{t.name}.json"] == m["row_counts"][t.name]
    assert m["dataset"]["seed"] == 20261022 and m["generator"]["command"].startswith("python scripts/generate.py")
    assert m["sources"][0]["external_data"] is False and m["evaluation_verdict"]["passed"]
    assert verify_manifest(out)["ok"]


def test_manifest_detects_tampering(tmp_path, starter):
    out = tmp_path / "t"
    publish(starter.cfg, out)
    (out / "csv" / "employee.csv").write_bytes((out / "csv" / "employee.csv").read_bytes() + b"x")
    (out / "json" / "asset.json").unlink()
    (out / "reports" / "extra.txt").write_text("x")
    v = verify_manifest(out)
    assert not v["ok"]
    assert v["changed"] == ["csv/employee.csv"] and v["missing"] == ["json/asset.json"] and v["unlisted"] == ["reports/extra.txt"]


def test_republishing_is_byte_identical_and_removes_stale_files(tmp_path, starter):
    a, b = tmp_path / "a", tmp_path / "b"
    publish(starter.cfg, a)
    (b / "csv").mkdir(parents=True)
    (b / "csv" / "stale.csv").write_text("old")          # leftover from an earlier run must not survive
    publish(starter.cfg, b)
    files_a = sorted(p.relative_to(a).as_posix() for p in a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b).as_posix() for p in b.rglob("*") if p.is_file())
    assert files_a == files_b
    assert all((a / f).read_bytes() == (b / f).read_bytes() for f in files_a)


# ------------------------------------------------------------------ committed artifacts & CLI
@pytest.mark.parametrize("rel, build", [
    ("sql/schema.sql", build_schema_sql), ("sql/deferred_constraints.sql", build_deferred_sql),
    ("docs/data_dictionary.md", build_data_dictionary), ("docs/er_diagram.md", build_er_diagram),
])
def test_committed_schema_artifacts_are_up_to_date(rel, build):
    assert (ROOT / rel).read_text(encoding="utf-8") == build(), f"run: python scripts/export_schema.py ({rel})"


def test_data_dictionary_covers_every_field():
    doc = build_data_dictionary()
    for t in TABLES:
        assert f"## {t.name}" in doc
        for f in t.fields:
            assert f"| `{f.name}` |" in doc


def test_generate_cli_publishes_a_verifiable_folder(tmp_path):
    out = tmp_path / "cli"
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "generate.py"), "--scale", "starter", "--out", str(out)],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert verify_manifest(out)["ok"]
