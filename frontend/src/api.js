// The ONLY place the frontend talks to the outside world: the FastAPI backend.
// The frontend never touches MySQL or credentials.
import { useCallback, useEffect, useState } from "react";

export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000/api";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function apiGet(path, params = {}, { timeoutMs = 20000 } = {}) {
  const url = new URL(API_BASE + path);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  });
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(url, { signal: controller.signal, headers: { Accept: "application/json" } });
  } catch (err) {
    throw new ApiError(
      err.name === "AbortError"
        ? "The API did not respond in time."
        : `Cannot reach the API at ${API_BASE}. Is the backend running?`,
      0,
    );
  } finally {
    clearTimeout(timer);
  }
  let body = null;
  try {
    body = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) {
    const detail = Array.isArray(body?.detail) ? body.detail.map((d) => d.msg).join("; ") : body?.detail;
    throw new ApiError(detail || `Request failed (HTTP ${res.status})`, res.status);
  }
  return body;
}

export const api = {
  health: () => apiGet("/health"),
  summary: () => apiGet("/summary"),
  anomalies: (filters = {}) => apiGet("/anomalies", filters),
  employees: (params = {}) => apiGet("/employees", params),
  employee: (id) => apiGet(`/employees/${encodeURIComponent(id)}`),
};

/** Run an API call when deps change; exposes {data, error, loading, reload}. */
export function useApi(fn, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn()
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((error) => alive && setState({ data: null, error, loading: false }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { ...state, reload };
}
