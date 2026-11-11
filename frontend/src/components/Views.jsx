import { useState } from "react";
import Icon from "./Icons.jsx";
import { api } from "../api.js";
import { AnomalyTable, GroundTruthTable, MatchSummary } from "./Anomalies.jsx";
import { BarList, ColumnChart, Donut } from "./Charts.jsx";
import { EmployeeSearch } from "./Employees.jsx";
import { Async, Card, ErrorState, Loading, Unavailable, fmt, label, pct } from "./common.jsx";

const ACCESS_TYPES = ["ACCESS_BEFORE_START_OR_APPROVAL", "TERMINATED_EMPLOYEE_RETAINS_GRANT"];
const ASSET_TYPES = ["DUPLICATE_ASSET_ASSIGNMENT", "RECOVERED_ASSET_REMAINS_ASSIGNED"];
const TASK_TYPES = ["TASK_DEPENDENCY_CYCLE"];

function Kpi({ icon, color, value, labelText, note }) {
  return (
    <div className={`kpi kpi-${color}`}>
      <div className="kpi-icon"><Icon name={icon} size={26} /></div>
      <div>
        <div className="kpi-value">{value}</div>
        <div className="kpi-label">{labelText}</div>
      </div>
      {note && <span className={`kpi-note kpi-note-${color}`}>{note}</span>}
    </div>
  );
}

function KpiRow({ summary, anomalies }) {
  const s = summary.data;
  const a = anomalies.data;
  const types = a?.summary?.length;
  return (
    <div className="kpi-row">
      <Kpi icon="users" color="blue" value={s ? fmt(s.row_count.employee) : "…"} labelText="Total Employees"
        note={s && `${fmt(s.employees_by_status.ACTIVE)} active`} />
      <Kpi icon="clipboard" color="green" value={s ? fmt(s.row_count.task) : "…"} labelText="Total Tasks"
        note={s && `${fmt(s.kpis.open_overdue_tasks)} open overdue`} />
      <Kpi icon="target" color="amber" value={s ? pct(s.kpis.task_on_time_rate) : "…"} labelText="On-Time Rate"
        note={s && `${fmt(s.kpis.tasks_completed_on_time)}/${fmt(s.kpis.tasks_completed)} tasks`} />
      <Kpi icon="alert" color="red" value={a ? fmt(a.total) : anomalies.error ? "—" : "…"} labelText="Anomalies Found"
        note={types ? `${types} types` : null} />
    </div>
  );
}

const needSummary = (summary, render) => <Async q={summary} text="Loading summary…">{render}</Async>;

// ------------------------------------------------------------------ Dashboard
export function DashboardView({ summary, anomalies, onSelectEmployee, selectedId, refreshKey }) {
  return (
    <>
      <KpiRow summary={summary} anomalies={anomalies} />
      <div className="grid-3">
        <Card title="Employees by Status" icon="users">
          {needSummary(summary, (s) => <Donut data={s.employees_by_status} centerLabel="Employees" />)}
        </Card>
        <Card title="Exits by Type" icon="logout">
          {needSummary(summary, (s) => <ColumnChart data={s.offboarding_by_exit_type} />)}
        </Card>
        <Card title="Access Grants by Status" icon="key">
          {needSummary(summary, (s) => <BarList data={s.grants_by_status} />)}
        </Card>
      </div>
      <div className="grid-main">
        <Async q={anomalies} text="Running SQL rule checks in MySQL…">
          {(a) => <AnomalyTable data={a} onSelectEmployee={onSelectEmployee} limit={6} />}
        </Async>
        <div className="stack">
          <EmployeeSearch compact onSelect={onSelectEmployee} selectedId={selectedId} refreshKey={refreshKey} />
          <Card title="Quick Stats" icon="report">
            {needSummary(summary, (s) => (
              <ul className="quick">
                <li><Icon name="calendar" size={16} />Onboarding in progress<b>{fmt(s.onboarding_by_status.IN_PROGRESS ?? 0)}</b></li>
                <li><Icon name="clock" size={16} />Delayed starts<b>{fmt(s.kpis.delayed_starts)}</b></li>
                <li><Icon name="key" size={16} />Approved access overrides<b>{fmt(s.kpis.approved_overrides)}</b></li>
                <li><Icon name="alert" size={16} />Urgent terminations<b>{fmt(s.kpis.urgent_terminations)}</b></li>
                <li><Icon name="laptop" size={16} />Assets assigned<b>{fmt(s.assets_by_status.ASSIGNED ?? 0)}</b></li>
              </ul>
            ))}
          </Card>
        </div>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ Employees
export function EmployeesView({ summary, onSelectEmployee, selectedId, refreshKey }) {
  return (
    <>
      <div className="grid-2">
        <Card title="Employees by Status" icon="users">
          {needSummary(summary, (s) => <Donut data={s.employees_by_status} centerLabel="Employees" />)}
        </Card>
        <Card title="Employment Type" icon="briefcase">
          {needSummary(summary, (s) => <BarList data={s.employees_by_type} />)}
        </Card>
      </div>
      <EmployeeSearch onSelect={onSelectEmployee} selectedId={selectedId} refreshKey={refreshKey} />
    </>
  );
}

// ------------------------------------------------------------------ Tasks
export function TasksView({ summary, anomalies, onSelectEmployee }) {
  return (
    <>
      {needSummary(summary, (s) => (
        <div className="kpi-row">
          <Kpi icon="clipboard" color="blue" value={fmt(s.row_count.task)} labelText="Tasks" />
          <Kpi icon="check" color="green" value={fmt(s.kpis.tasks_completed)} labelText="Completed" />
          <Kpi icon="target" color="amber" value={pct(s.kpis.task_on_time_rate)} labelText="Completed on time" />
          <Kpi icon="clock" color="red" value={fmt(s.kpis.open_overdue_tasks)} labelText="Open & overdue" />
        </div>
      ))}
      <div className="grid-2">
        <Card title="Tasks by Status" icon="tasks">
          {needSummary(summary, (s) => <Donut data={s.tasks_by_status} centerLabel="Tasks" />)}
        </Card>
        <Card title="Dependencies" icon="layers">
          {needSummary(summary, (s) => (
            <div className="big-number">
              <b>{fmt(s.row_count.task_dependency)}</b>
              <span className="muted">FINISH_TO_START dependencies between tasks. Per-employee task lists with their
                prerequisites are in the employee details panel.</span>
            </div>
          ))}
          <Unavailable what="A cross-employee task list" />
        </Card>
      </div>
      <Async q={anomalies}>{(a) => <AnomalyTable data={a} onSelectEmployee={onSelectEmployee}
        title="Task Graph Anomalies" restrictTypes={TASK_TYPES} />}</Async>
    </>
  );
}

// ------------------------------------------------------------------ Access
export function AccessView({ summary, anomalies, onSelectEmployee }) {
  return (
    <>
      {needSummary(summary, (s) => (
        <div className="kpi-row">
          <Kpi icon="key" color="blue" value={fmt(s.row_count.access_grant)} labelText="Access grants" />
          <Kpi icon="check" color="green" value={fmt(s.grants_by_status.ACTIVE ?? 0)} labelText="Active grants" />
          <Kpi icon="layers" color="amber" value={fmt(s.kpis.approved_overrides)} labelText="Approved overrides (audited)" />
          <Kpi icon="logout" color="red" value={fmt(s.grants_by_status.REVOKED ?? 0)} labelText="Revoked grants" />
        </div>
      ))}
      <div className="grid-2">
        <Card title="Access Grants by Status" icon="key">
          {needSummary(summary, (s) => <BarList data={s.grants_by_status} />)}
        </Card>
        <Card title="Entitlement catalogue" icon="database">
          {needSummary(summary, (s) => (
            <div className="big-number"><b>{fmt(s.row_count.access_role)}</b>
              <span className="muted">entitlements, mapped to roles by {fmt(s.row_count.role_access_template)} template rows.
                Access is provisioned only after approval; per-employee grants are in the details panel.</span></div>
          ))}
        </Card>
      </div>
      <Async q={anomalies}>{(a) => <AnomalyTable data={a} onSelectEmployee={onSelectEmployee}
        title="Access Anomalies" restrictTypes={ACCESS_TYPES} />}</Async>
    </>
  );
}

// ------------------------------------------------------------------ Assets
export function AssetsView({ summary, anomalies, onSelectEmployee }) {
  return (
    <>
      {needSummary(summary, (s) => (
        <div className="kpi-row">
          <Kpi icon="laptop" color="blue" value={fmt(s.row_count.asset)} labelText="Assets in inventory" />
          <Kpi icon="check" color="green" value={fmt(s.assets_by_status.ASSIGNED ?? 0)} labelText="Assigned" />
          <Kpi icon="database" color="amber" value={fmt(s.assets_by_status.IN_STOCK ?? 0)} labelText="In stock" />
          <Kpi icon="alert" color="red" value={fmt((s.assets_by_status.LOST ?? 0) + (s.assets_by_status.RETIRED ?? 0))} labelText="Lost or retired" />
        </div>
      ))}
      <div className="grid-2">
        <Card title="Assets by Status" icon="laptop">
          {needSummary(summary, (s) => <Donut data={s.assets_by_status} centerLabel="Assets" />)}
        </Card>
        <Card title="Assignments" icon="layers">
          {needSummary(summary, (s) => (
            <div className="big-number"><b>{fmt(s.row_count.asset_assignment)}</b>
              <span className="muted">assignment records (issue → delivery → acknowledgement → return). Per-employee
                assignments are in the details panel.</span></div>
          ))}
          <Unavailable what="A searchable asset inventory list" />
        </Card>
      </div>
      <Async q={anomalies}>{(a) => <AnomalyTable data={a} onSelectEmployee={onSelectEmployee}
        title="Asset Anomalies" restrictTypes={ASSET_TYPES} />}</Async>
    </>
  );
}

// ------------------------------------------------------------------ Anomalies
export function AnomaliesView({ anomalies, onSelectEmployee }) {
  return (
    <Async q={anomalies} text="Running SQL rule checks in MySQL…">
      {(a) => (
        <>
          <Card title="Ground-truth comparison" icon="target" actions={<MatchSummary summary={a.summary} />}>
            <p className="muted small intro">Each business rule is executed as SQL inside MySQL by the backend
              (<code>sql/queries/anomaly_checks.sql</code>) and compared with the separately stored ground-truth labels.</p>
            <GroundTruthTable summary={a.summary} />
          </Card>
          <AnomalyTable data={a} onSelectEmployee={onSelectEmployee} title="All Detected Anomalies" />
        </>
      )}
    </Async>
  );
}

// ------------------------------------------------------------------ Reports
export function ReportsView({ summary }) {
  return needSummary(summary, (s) => (
    <>
      <div className="grid-2">
        <Card title="Records per Table (MySQL)" icon="database">
          <table className="table compact">
            <thead><tr><th>Table</th><th className="num">Rows</th></tr></thead>
            <tbody>{Object.entries(s.row_count).map(([t, n]) => (
              <tr key={t}><td className="mono">{t}</td><td className="num">{fmt(n)}</td></tr>))}</tbody>
            <tfoot><tr><th>Total</th><th className="num">{fmt(Object.values(s.row_count).reduce((x, y) => x + y, 0))}</th></tr></tfoot>
          </table>
        </Card>
        <div className="stack">
          <Card title="Active Headcount by Department" icon="building"><BarList data={s.active_by_department} /></Card>
          <Card title="Onboarding Cases by Status" icon="calendar"><BarList data={s.onboarding_by_status} /></Card>
        </div>
      </div>
      <div className="grid-2">
        <Card title="Exits by Type" icon="logout"><ColumnChart data={s.offboarding_by_exit_type} /></Card>
        <Card title="Tasks by Status" icon="tasks"><BarList data={s.tasks_by_status} /></Card>
      </div>
    </>
  ));
}

// ------------------------------------------------------------------ System health
const ENDPOINTS = [
  ["GET /api/health", () => api.health()],
  ["GET /api/summary", () => api.summary()],
  ["GET /api/anomalies", () => api.anomalies()],
  ["GET /api/employees?limit=5", () => api.employees({ limit: 5 })],
  ["GET /api/employees/EMP-000001", () => api.employee("EMP-000001")],
];

export function HealthView({ health }) {
  const [results, setResults] = useState(null);
  const [running, setRunning] = useState(false);
  const run = async () => {
    setRunning(true);
    const out = [];
    for (const [name, call] of ENDPOINTS) {
      const t0 = performance.now();
      try {
        await call();
        out.push({ name, ok: true, ms: performance.now() - t0 });
      } catch (e) {
        out.push({ name, ok: false, ms: performance.now() - t0, error: e.message });
      }
    }
    setResults(out);
    setRunning(false);
  };
  return (
    <div className="grid-2">
      <Card title="Backend & Database" icon="pulse">
        {health.loading && !health.data ? <Loading /> : health.error ? <ErrorState error={health.error} onRetry={health.reload} /> : (
          <div className="info">
            <div className="info-row"><Icon name="check" size={16} /><span className="muted">API status</span><b>{label(health.data.status)}</b></div>
            <div className="info-row"><Icon name="database" size={16} /><span className="muted">Database</span><b>{health.data.database}</b></div>
            <div className="info-row"><Icon name="layers" size={16} /><span className="muted">MySQL version</span><b>{health.data.mysql_version}</b></div>
            <div className="info-row"><Icon name="report" size={16} /><span className="muted">Tables</span><b>{health.data.tables}</b></div>
          </div>
        )}
        <p className="muted small intro">Architecture: React (this page) → FastAPI REST API → MySQL. The browser never
          connects to the database and never sees credentials.</p>
      </Card>
      <Card title="Endpoint checks" icon="target"
        actions={<button className="btn btn-primary" onClick={run} disabled={running}>{running ? "Running…" : "Run checks"}</button>}>
        {!results ? <p className="muted small intro">Calls every API endpoint from the browser and measures the response time.</p> : (
          <table className="table compact">
            <thead><tr><th>Endpoint</th><th>Result</th><th className="num">Time</th></tr></thead>
            <tbody>{results.map((r) => (
              <tr key={r.name}><td className="mono small">{r.name}</td>
                <td>{r.ok ? <span className="ok-text"><Icon name="check" size={14} /> OK</span> : <span className="bad-text" title={r.error}>Failed</span>}</td>
                <td className="num">{r.ms.toFixed(0)} ms</td></tr>))}</tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
