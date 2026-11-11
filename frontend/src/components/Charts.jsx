// Lightweight SVG / CSS charts. Input is always a {label: count} object from the API.
import { fmt, label, tone } from "./common.jsx";

const sorted = (data) => Object.entries(data || {}).sort((a, b) => b[1] - a[1]);

export function Donut({ data, centerLabel }) {
  const entries = sorted(data);
  const total = entries.reduce((s, [, v]) => s + v, 0);
  const r = 52;
  const c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <div className="donut-wrap">
      <svg viewBox="0 0 140 140" className="donut" role="img" aria-label={`${centerLabel}: ${total}`}>
        <circle cx="70" cy="70" r={r} className="donut-track" />
        {total > 0 &&
          entries.map(([k, v]) => {
            const len = (v / total) * c;
            const seg = (
              <circle key={k} cx="70" cy="70" r={r} className={`donut-seg stroke-${tone(k)}`}
                strokeDasharray={`${len} ${c - len}`} strokeDashoffset={-offset} transform="rotate(-90 70 70)">
                <title>{`${label(k)}: ${fmt(v)}`}</title>
              </circle>
            );
            offset += len;
            return seg;
          })}
        <text x="70" y="68" className="donut-total">{fmt(total)}</text>
        <text x="70" y="86" className="donut-caption">{centerLabel}</text>
      </svg>
      <ul className="legend">
        {entries.map(([k, v]) => (
          <li key={k}>
            <span className={`dot bg-${tone(k)}`} />
            <span className="legend-label">{label(k)}</span>
            <b>{fmt(v)}</b>
            <span className="muted">({total ? ((v / total) * 100).toFixed(1) : 0}%)</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ColumnChart({ data }) {
  const entries = sorted(data);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <div className="columns">
      {entries.map(([k, v]) => (
        <div className="column" key={k} title={`${label(k)}: ${fmt(v)}`}>
          <span className="column-value">{fmt(v)}</span>
          <div className="column-track">
            <div className={`column-bar bg-${tone(k)}`} style={{ height: `${(v / max) * 100}%` }} />
          </div>
          <span className="column-label">{label(k)}</span>
        </div>
      ))}
    </div>
  );
}

export function BarList({ data, limit }) {
  const entries = sorted(data).slice(0, limit || undefined);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <div className="barlist">
      {entries.map(([k, v]) => (
        <div className="barlist-row" key={k} title={`${label(k)}: ${fmt(v)}`}>
          <span className="barlist-label">{label(k)}</span>
          <div className="barlist-track">
            <div className={`barlist-bar bg-${tone(k)}`} style={{ width: `${(v / max) * 100}%` }} />
          </div>
          <b className="barlist-value">{fmt(v)}</b>
        </div>
      ))}
    </div>
  );
}
