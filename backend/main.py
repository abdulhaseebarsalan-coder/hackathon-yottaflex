"""Backend tier: FastAPI application.

Run from the repository root:
    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
Interactive API docs: http://127.0.0.1:8000/docs

Data flow:  frontend (browser)  ->  this API (JSON over HTTP)  ->  MySQL hackathon_db
The API is read-only. Ground-truth labels are read from output/<scale>/ground_truth (never from MySQL)
and only used to annotate the anomalies that the SQL rule checks find live in the database.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import repository as repo
from backend.db import ROOT, Database, MySQLDatabase

IssueType = Literal["ACCESS_BEFORE_START_OR_APPROVAL", "DUPLICATE_ASSET_ASSIGNMENT",
                    "TERMINATED_EMPLOYEE_RETAINS_GRANT", "TASK_DEPENDENCY_CYCLE", "RECOVERED_ASSET_REMAINS_ASSIGNED"]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
Status = Literal["PRE_BOARDING", "ACTIVE", "TERMINATED", "WITHDRAWN"]

app = FastAPI(
    title="Onboarding & Offboarding Orchestrator API",
    version="1.0.0",
    description="Read-only API over the MySQL database: workforce KPIs, live business-rule anomaly checks, "
                "and employee lifecycle details (cases, tasks, access, assets, audit trail).",
)
app.add_middleware(  # the frontend tier is served separately (default http://127.0.0.1:5500)
    CORSMiddleware,
    allow_origins=os.environ.get("API_CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500").split(","),
    allow_methods=["GET"],
    allow_headers=["*"],
)

_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = MySQLDatabase()
    return _db


def dataset_dir() -> Path:
    return Path(os.environ.get("DATASET_DIR", ROOT / "output" / "full"))


@app.exception_handler(Exception)
async def database_unavailable(_request, exc: Exception):  # never leak credentials / stack traces
    return JSONResponse(status_code=503, content={"detail": f"Backend error: {type(exc).__name__}. "
                                                            "Is MySQL running and is .env configured?"})


@app.get("/api/health", tags=["system"], summary="API and database status")
def health(db: Database = Depends(get_db)):
    return repo.health(db)


@app.get("/api/summary", tags=["dashboard"], summary="Row counts, status breakdowns and KPIs from MySQL")
def summary(db: Database = Depends(get_db)):
    return repo.summary(db)


@app.get("/api/anomalies", tags=["dashboard"],
         summary="Run the 5 SQL business-rule checks in MySQL and compare with ground truth")
def anomalies(issue_type: IssueType | None = None, severity: Severity | None = None,
              db: Database = Depends(get_db)):
    return repo.anomalies(db, dataset_dir(), issue_type, severity)


@app.get("/api/employees", tags=["employees"], summary="Search employees")
def employees(q: Annotated[str | None, Query(max_length=60, description="ID, name or email fragment")] = None,
              status: Status | None = None,
              limit: Annotated[int, Query(ge=1, le=200)] = 50,
              offset: Annotated[int, Query(ge=0)] = 0,
              db: Database = Depends(get_db)):
    return repo.list_employees(db, q, status, limit, offset)


@app.get("/api/employees/{employee_id}", tags=["employees"],
         summary="Employee lifecycle: profile, role history, cases, tasks, access, assets, audit trail")
def employee(employee_id: Annotated[str, PathParam(pattern=r"^EMP-\d{6}$")], db: Database = Depends(get_db)):
    detail = repo.employee_detail(db, employee_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"{employee_id} not found")
    return detail
