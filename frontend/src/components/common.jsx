import Icon from "./Icons.jsx";

export const fmt = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US"));
export const pct = (r) => (r === null || r === undefined ? "—" : `${(r * 100).toFixed(1)}%`);
export const label = (s) =>
  String(s ?? "")
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
export const date = (d) => (d ? String(d).slice(0, 10) : "—");

// Status -> colour token (used by pills, legends and charts)
const TONES = {
  ACTIVE: "green", COMPLETED: "green", ACKNOWLEDGED: "green", APPROVED: "blue", RETURNED: "slate",
  IN_STOCK: "slate", PRE_BOARDING: "blue", IN_PROGRESS: "blue", READY: "indigo", DELIVERED: "indigo",
  ASSIGNED: "indigo", REQUESTED: "amber", OPEN: "amber", BLOCKED: "amber", PENDING: "amber",
  TERMINATED: "red", REVOKED: "red", REJECTED: "red", LOST: "red", WITHDRAWN: "slate", CANCELLED: "slate",
  RETIRED: "slate", CRITICAL: "red", HIGH: "orange", MEDIUM: "amber", LOW: "blue",
  VOLUNTARY: "indigo", INVOLUNTARY: "amber", CONTRACT_END: "blue", URGENT_TERMINATION: "red",
  FULL_TIME: "indigo", CONTRACTOR: "amber", INTERN: "green",
};
export const tone = (s) => TONES[s] || "slate";

export function Pill({ value, children }) {
  return <span className={`pill pill-${tone(value)}`}>{children ?? label(value)}</span>;
}

export function Card({ title, icon, actions, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <h3>
            {icon && <Icon name={icon} size={18} className="card-icon" />}
            {title}
          </h3>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Loading({ text = "Loading…" }) {
  return (
    <div className="state">
      <span className="spinner" /> {text}
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="state state-error" role="alert">
      <Icon name="alert" size={18} />
      <span>{error?.message || String(error)}</span>
      {onRetry && (
        <button className="btn btn-ghost" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ text }) {
  return <div className="state state-empty">{text}</div>;
}

export function Unavailable({ what }) {
  return (
    <div className="state state-empty">
      <Icon name="circle" size={16} /> {what} is not exposed by the current API, so it is not shown here
      rather than being invented.
    </div>
  );
}

/** Renders loading / error / content for a useApi() result. */
export function Async({ q, children, text }) {
  if (q.loading && !q.data) return <Loading text={text} />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.reload} />;
  return children(q.data);
}
