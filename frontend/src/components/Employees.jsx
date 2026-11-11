import { useEffect, useState } from "react";
import Icon from "./Icons.jsx";
import { api, useApi } from "../api.js";
import { Async, Card, Empty, ErrorState, Loading, Pill, date, fmt, label } from "./common.jsx";

const STATUSES = ["ACTIVE", "PRE_BOARDING", "TERMINATED", "WITHDRAWN"];

function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Search box + result list. compact = dashboard widget; otherwise full table with paging. */
export function EmployeeSearch({ onSelect, selectedId, compact = false, refreshKey }) {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(0);
  const pageSize = compact ? 6 : 25;
  const dq = useDebounced(q.trim());
  useEffect(() => setPage(0), [dq, status]);
  const res = useApi(() => api.employees({ q: dq, status, limit: pageSize, offset: page * pageSize }),
    [dq, status, page, pageSize, refreshKey]);

  const form = (
    <form className="search-row" onSubmit={(e) => e.preventDefault()} role="search">
      <div className="search-box">
        <Icon name="search" size={16} />
        <input value={q} onChange={(e) => setQ(e.target.value)} maxLength={60}
          placeholder="Search by ID, name or email…" aria-label="Search employees" />
      </div>
      {!compact && (
        <select className="select" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
          <option value="">All statuses</option>
          {STATUSES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
        </select>
      )}
    </form>
  );

  return (
    <Card title={compact ? "Find Employee" : "Employees"} icon={compact ? "search" : "users"}
      actions={res.data && <span className="muted small">{fmt(res.data.total)} found</span>}>
      {form}
      <Async q={res} text="Searching…">
        {(d) => d.items.length === 0 ? <Empty text={`No employees match "${dq}".`} /> : (
          <>
            <ul className={`emp-list ${compact ? "compact" : ""}`}>
              {d.items.map((e) => (
                <li key={e.employee_id}>
                  <button className={`emp-row ${selectedId === e.employee_id ? "selected" : ""}`} onClick={() => onSelect(e.employee_id)}>
                    <span className="emp-avatar">{e.first_name[0]}{e.last_name[0]}</span>
                    <span className="emp-main">
                      <b>{e.first_name} {e.last_name}</b>
                      <span className="muted small">{e.employee_id} · {e.role_title}{compact ? "" : ` · ${e.department_name}`}</span>
                    </span>
                    {!compact && <span className="muted small hide-sm">{label(e.employment_type)} · {label(e.work_location)}</span>}
                    {!compact && <span className="muted small hide-sm">start {date(e.start_date)}</span>}
                    <Pill value={e.employment_status} />
                  </button>
                </li>
              ))}
            </ul>
            {!compact && (
              <div className="pager">
                <button className="btn btn-ghost" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>← Prev</button>
                <span className="muted small">
                  {fmt(page * pageSize + 1)}–{fmt(Math.min((page + 1) * pageSize, d.total))} of {fmt(d.total)}
                </span>
                <button className="btn btn-ghost" disabled={(page + 1) * pageSize >= d.total} onClick={() => setPage((p) => p + 1)}>Next →</button>
              </div>
            )}
          </>
        )}
      </Async>
    </Card>
  );
}

const TABS = [
  { id: "tasks", label: "Tasks" },
  { id: "access", label: "Access" },
  { id: "assets", label: "Assets" },
  { id: "history", label: "History" },
  { id: "audit", label: "Audit" },
];

function InfoRow({ icon, k, v }) {
  return (
    <div className="info-row">
      <Icon name={icon} size={16} /><span className="muted">{k}</span><b>{v ?? "—"}</b>
    </div>
  );
}

function Timeline({ items, render }) {
  if (!items?.length) return <Empty text="No records." />;
  return <ul className="timeline">{items.map(render)}</ul>;
}

/** Side panel for one employee, fed by GET /api/employees/{id}. */
export function EmployeeDetail({ employeeId, onClose, refreshKey }) {
  const [tab, setTab] = useState("tasks");
  const res = useApi(() => (employeeId ? api.employee(employeeId) : Promise.resolve(null)), [employeeId, refreshKey]);

  if (!employeeId) {
    return (
      <Card title="Employee Details" icon="users" className="detail-card">
        <Empty text="Select an employee from search or the anomaly table to see their full lifecycle." />
      </Card>
    );
  }
  return (
    <Card title="Employee Details" className="detail-card"
      actions={<button className="btn btn-icon" onClick={onClose} aria-label="Close details"><Icon name="close" size={16} /></button>}>
      {res.loading && !res.data ? <Loading text={`Loading ${employeeId}…`} /> : res.error ? (
        <ErrorState error={res.error} onRetry={res.reload} />
      ) : res.data && (() => {
        const d = res.data;
        const p = d.profile;
        const off = d.offboarding_case;
        const onb = d.onboarding_case;
        return (
          <>
            <div className="profile">
              <div className="profile-avatar"><Icon name="users" size={26} /></div>
              <div className="profile-main">
                <div className="mono">{p.employee_id}</div>
                <div className="profile-name">{p.first_name} {p.last_name}</div>
                <div className="muted small">{p.work_email}</div>
              </div>
              <Pill value={p.employment_status} />
            </div>
            <div className="info">
              <InfoRow icon="briefcase" k="Role" v={`${p.role_title} (${p.job_level})`} />
              <InfoRow icon="building" k="Department" v={p.department_name} />
              <InfoRow icon="users" k="Manager" v={p.manager_name} />
              <InfoRow icon="layers" k="Type · Location" v={`${label(p.employment_type)} · ${label(p.work_location)}`} />
              <InfoRow icon="calendar" k="Start date" v={date(p.start_date)} />
              <InfoRow icon="logout" k="Exit" v={off ? `${label(off.exit_type)} · last day ${date(off.last_working_date)}` : "—"} />
            </div>
            <div className="case-strip">
              {onb && <div className="case-box"><span className="muted small">Onboarding {onb.onboarding_case_id}</span>
                <b>{label(onb.case_status)} · {onb.completed_tasks}/{onb.total_tasks} tasks ({Number(onb.completion_pct).toFixed(0)}%)</b>
                {onb.start_delay_days > 0 && <span className="small warn-text">Start delayed {onb.start_delay_days} days</span>}</div>}
              {off && <div className="case-box"><span className="muted small">Offboarding {off.offboarding_case_id}</span>
                <b>{label(off.case_status)} · grants revoked {off.grants_revoked}/{off.grants_to_revoke}</b>
                <span className="small muted">assets recovered {off.assets_recovered}/{off.assets_to_recover} · revoke by {date(off.revocation_due_date)}</span></div>}
              {!onb && !off && <div className="case-box"><span className="muted small">No onboarding/offboarding case (existing staff)</span></div>}
            </div>
            <div className="tabs" role="tablist">
              {TABS.map((t) => {
                const n = { tasks: d.tasks.length, access: d.access_grants.length, assets: d.assets.length,
                  history: d.role_history.length, audit: d.audit_trail.length }[t.id];
                return (
                  <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab ${tab === t.id ? "active" : ""}`}
                    onClick={() => setTab(t.id)}>{t.label} <span className="tab-count">{n}</span></button>
                );
              })}
            </div>
            <div className="tab-body">
              {tab === "tasks" && <Timeline items={d.tasks} render={(t) => (
                <li key={t.task_id} className="tl-item">
                  <span className={`tl-icon ${t.task_status === "COMPLETED" ? "done" : t.is_overdue ? "late" : ""}`}>
                    <Icon name={t.task_status === "COMPLETED" ? "check" : "clock"} size={13} /></span>
                  <div className="tl-main"><b>{t.task_name}</b>
                    <span className="muted small">{t.task_id} · {label(t.owner_team)}{t.depends_on ? ` · after ${t.depends_on.split(",").map((x) => x.slice(-3)).join(", ")}` : ""}</span></div>
                  <div className="tl-side">
                    <Pill value={t.task_status} />
                    <span className={`small ${t.is_overdue ? "warn-text" : "muted"}`}>{t.completed_date ? date(t.completed_date) : `due ${date(t.due_date)}`}</span>
                  </div>
                </li>)} />}
              {tab === "access" && <Timeline items={d.access_grants} render={(g) => (
                <li key={g.grant_id} className="tl-item">
                  <span className={`tl-icon ${g.grant_status === "ACTIVE" ? "done" : ""}`}><Icon name="key" size={13} /></span>
                  <div className="tl-main"><b>{g.access_role_name}</b>
                    <span className="muted small">{g.grant_id} · {label(g.privilege_level)} · {label(g.request_reason)}
                      {g.override_event_id ? ` · override ${g.override_event_id}` : ""}</span></div>
                  <div className="tl-side"><Pill value={g.grant_status} />
                    <span className="small muted">{g.revoked_date ? `revoked ${date(g.revoked_date)}` : g.provisioned_date ? `since ${date(g.provisioned_date)}` : `requested ${date(g.requested_date)}`}</span></div>
                </li>)} />}
              {tab === "assets" && <Timeline items={d.assets} render={(a) => (
                <li key={a.assignment_id} className="tl-item">
                  <span className="tl-icon"><Icon name="laptop" size={13} /></span>
                  <div className="tl-main"><b>{a.model}</b><span className="muted small">{a.asset_id} · {label(a.asset_type)} · {a.assignment_id}</span></div>
                  <div className="tl-side"><Pill value={a.assignment_status} />
                    <span className="small muted">{a.returned_date ? `returned ${date(a.returned_date)}` : `assigned ${date(a.assigned_date)}`}</span></div>
                </li>)} />}
              {tab === "history" && <Timeline items={d.role_history} render={(h) => (
                <li key={h.role_history_id} className="tl-item">
                  <span className="tl-icon"><Icon name="briefcase" size={13} /></span>
                  <div className="tl-main"><b>{h.role_title}</b><span className="muted small">{h.department_name} · {label(h.change_reason)}</span></div>
                  <div className="tl-side"><span className="small muted">{date(h.effective_from)} → {h.effective_to ? date(h.effective_to) : "now"}</span></div>
                </li>)} />}
              {tab === "audit" && <Timeline items={d.audit_trail} render={(e) => (
                <li key={e.event_id} className="tl-item">
                  <span className="tl-icon"><Icon name="clock" size={13} /></span>
                  <div className="tl-main"><b>{label(e.event_type)}</b>
                    <span className="muted small">{e.entity_id}{e.from_state || e.to_state ? ` · ${e.from_state ?? "∅"} → ${e.to_state ?? "∅"}` : ""}{e.reason ? ` · ${e.reason}` : ""}</span></div>
                  <div className="tl-side"><span className="small muted">{String(e.event_timestamp).replace("T", " ").slice(0, 16)}</span>
                    <span className="small muted">{label(e.actor_role)}</span></div>
                </li>)} />}
            </div>
          </>
        );
      })()}
    </Card>
  );
}
