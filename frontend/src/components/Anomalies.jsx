// Anomalies come from GET /api/anomalies: the backend runs the SQL rule checks live in MySQL and joins
// them with the ground truth. This component only displays and filters what the API returns.
import { useMemo, useState } from "react";
import Icon from "./Icons.jsx";
import { Card, Empty, Pill, label } from "./common.jsx";

const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

export function MatchSummary({ summary }) {
  const found = summary.reduce((s, r) => s + r.found_in_db, 0);
  const truth = summary.reduce((s, r) => s + r.ground_truth, 0);
  const all = summary.every((r) => r.match);
  return (
    <span className={`match-chip ${all ? "ok" : "warn"}`} title="SQL checks run in MySQL vs ground-truth labels">
      <Icon name={all ? "check" : "alert"} size={14} /> {found} / {truth} matched{all ? " (100%)" : ""}
    </span>
  );
}

export function GroundTruthTable({ summary }) {
  return (
    <table className="table compact">
      <thead>
        <tr><th>Rule check (run live in MySQL)</th><th className="num">Found in DB</th><th className="num">Ground truth</th><th>Result</th></tr>
      </thead>
      <tbody>
        {summary.map((r) => (
          <tr key={r.issue_type}>
            <td><code className="type-tag">{r.issue_type}</code></td>
            <td className="num">{r.found_in_db}</td>
            <td className="num">{r.ground_truth}</td>
            <td>{r.match ? <span className="ok-text"><Icon name="check" size={14} /> {r.found_in_db}/{r.ground_truth} match</span>
              : <span className="bad-text"><Icon name="alert" size={14} /> mismatch</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function AnomalyTable({ data, onSelectEmployee, limit, title = "Anomaly Detection", restrictTypes }) {
  const [type, setType] = useState("");
  const [severity, setSeverity] = useState("");
  const types = useMemo(() => data.summary.map((s) => s.issue_type)
    .filter((t) => !restrictTypes || restrictTypes.includes(t)), [data, restrictTypes]);
  const rows = data.items.filter((i) => (!restrictTypes || restrictTypes.includes(i.issue_type))
    && (!type || i.issue_type === type) && (!severity || i.severity === severity));
  const shown = limit ? rows.slice(0, limit) : rows;
  const summary = data.summary.filter((s) => !restrictTypes || restrictTypes.includes(s.issue_type));

  return (
    <Card title={title} icon="alert" className="anomaly-card"
      actions={<>
        <select className="select" value={type} onChange={(e) => setType(e.target.value)} aria-label="Filter by type">
          <option value="">All types</option>
          {types.map((t) => <option key={t} value={t}>{label(t)}</option>)}
        </select>
        <select className="select" value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Filter by severity">
          <option value="">All severities</option>
          {SEVERITIES.map((s) => <option key={s} value={s}>{label(s)}</option>)}
        </select>
        <MatchSummary summary={type ? summary.filter((s) => s.issue_type === type) : summary} />
      </>}>
      {shown.length === 0 ? <Empty text="No anomalies match these filters." /> : (
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr><th>ID</th><th>Type</th><th>Description</th><th>Employee</th><th>Severity</th><th>Expected decision</th><th>In DB</th></tr>
            </thead>
            <tbody>
              {shown.map((a) => (
                <tr key={a.anomaly_id}>
                  <td className="mono muted">{a.anomaly_id}</td>
                  <td><code className={`type-tag t-${a.severity.toLowerCase()}`}>{a.issue_type}</code>
                    <div className="sub muted">{label(a.sub_type)} · {a.rule_id}</div></td>
                  <td className="desc">{a.description}</td>
                  <td>{a.employee_id ? <button className="link" onClick={() => onSelectEmployee(a.employee_id)}>{a.employee_id}</button> : "—"}</td>
                  <td><Pill value={a.severity} /></td>
                  <td className="mono small">{a.expected_decision}</td>
                  <td>{a.confirmed_in_db ? <span className="ok-text" title="Detected by the SQL rule check in MySQL"><Icon name="check" size={15} /></span>
                    : <span className="bad-text" title="Not found by the SQL check">✕</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="card-foot muted">
        Showing {shown.length} of {rows.length} · detection date reference: as of {data.as_of_date}
        {data.unexpected_findings?.length ? ` · ${data.unexpected_findings.length} unexpected finding(s)` : " · 0 unexpected findings"}
      </div>
    </Card>
  );
}
