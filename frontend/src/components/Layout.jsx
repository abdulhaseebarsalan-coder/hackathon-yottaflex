import Icon from "./Icons.jsx";
import { fmt } from "./common.jsx";
import { API_BASE } from "../api.js";

export const NAV = [
  { id: "dashboard", label: "Dashboard", icon: "dashboard" },
  { id: "employees", label: "Employees", icon: "users" },
  { id: "tasks", label: "Tasks", icon: "tasks" },
  { id: "access", label: "Access & Grants", icon: "key" },
  { id: "assets", label: "Assets", icon: "laptop" },
  { id: "anomalies", label: "Anomalies", icon: "alert" },
  { id: "reports", label: "Reports", icon: "report" },
  { id: "health", label: "System Health", icon: "pulse" },
];

export function Sidebar({ view, onNavigate, anomalyCount, summary, asOf }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-logo"><Icon name="layers" size={26} /></div>
        <div>
          <div className="brand-name">Workforce<br />Intelligence</div>
          <div className="brand-sub">HR • IT • Facilities</div>
        </div>
      </div>
      <nav className="nav">
        {NAV.map((n) => (
          <button key={n.id} className={`nav-item ${view === n.id ? "active" : ""}`} onClick={() => onNavigate(n.id)}
            aria-current={view === n.id ? "page" : undefined}>
            <Icon name={n.icon} size={19} />
            <span>{n.label}</span>
            {n.id === "anomalies" && anomalyCount > 0 && <span className="nav-badge">{anomalyCount}</span>}
          </button>
        ))}
      </nav>
      <div className="dataset-card">
        <div className="dataset-head"><Icon name="database" size={22} /> Synthetic HR/IT Dataset</div>
        {summary ? (
          <div className="dataset-meta">
            <span>{fmt(summary.row_count?.employee)} employees · {fmt(summary.row_count?.task)} tasks</span>
            <span>{fmt(summary.row_count?.audit_event)} audit events</span>
            {asOf && <span>Snapshot as of {asOf}</span>}
          </div>
        ) : (
          <div className="dataset-meta"><span>Waiting for API…</span></div>
        )}
        <div className="dataset-tag"><Icon name="check" size={13} /> 3-tier: React · FastAPI · MySQL</div>
      </div>
    </aside>
  );
}

function StatusChip({ ok, loading, title, detail }) {
  const cls = loading ? "pending" : ok ? "ok" : "down";
  return (
    <div className={`status-chip ${cls}`} title={detail}>
      <span className="status-dot" />
      <div>
        <div className="status-title">{title}</div>
        <div className="status-detail">{detail}</div>
      </div>
    </div>
  );
}

export function Header({ health, onRefresh, refreshing }) {
  const ok = !!health.data && health.data.status === "ok";
  return (
    <header className="topbar">
      <div>
        <h1>Workforce Intelligence Dashboard</h1>
        <p className="subtitle">End-to-End HR, IT &amp; Facilities Workflow (Onboarding • Access • Assets • Tasks • Exit)</p>
      </div>
      <div className="topbar-right">
        <StatusChip loading={health.loading} ok={!health.error && !health.loading}
          title={health.loading ? "Connecting…" : health.error ? "API Disconnected" : "API Connected"}
          detail={API_BASE.replace(/\/api$/, "")} />
        <StatusChip loading={health.loading} ok={ok}
          title={ok ? "Database (MySQL)" : health.loading ? "Database…" : "Database Unavailable"}
          detail={ok ? `${health.data.database} · ${health.data.tables} tables` : health.error ? "no connection" : "checking"} />
        <button className="btn btn-icon" onClick={onRefresh} title="Refresh all data" aria-label="Refresh all data">
          <Icon name="refresh" size={18} className={refreshing ? "spin" : ""} />
        </button>
        <div className="avatar" title="Local demo session (no authentication)">WI</div>
      </div>
    </header>
  );
}
