import { useCallback, useEffect, useState } from "react";
import { api, useApi } from "./api.js";
import { EmployeeDetail } from "./components/Employees.jsx";
import { Header, NAV, Sidebar } from "./components/Layout.jsx";
import {
  AccessView, AnomaliesView, AssetsView, DashboardView, EmployeesView, HealthView, ReportsView, TasksView,
} from "./components/Views.jsx";

export default function App() {
  const [view, setView] = useState("dashboard");
  const [selectedId, setSelectedId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const health = useApi(() => api.health(), [refreshKey]);
  const summary = useApi(() => api.summary(), [refreshKey]);
  const anomalies = useApi(() => api.anomalies(), [refreshKey]);

  // Once, on first load: pre-select the employee of the first CRITICAL anomaly so the panel is populated.
  // (Closing the panel afterwards keeps it closed.)
  const [autoSelected, setAutoSelected] = useState(false);
  useEffect(() => {
    if (!autoSelected && anomalies.data?.items?.length) {
      const first = anomalies.data.items.find((a) => a.severity === "CRITICAL" && a.employee_id) || anomalies.data.items[0];
      setSelectedId(first.employee_id);
      setAutoSelected(true);
    }
  }, [anomalies.data, autoSelected]);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);
  const refreshing = health.loading || summary.loading || anomalies.loading;
  const common = { summary, anomalies, onSelectEmployee: setSelectedId, selectedId, refreshKey };
  const title = NAV.find((n) => n.id === view)?.label;

  return (
    <div className="app">
      <Sidebar view={view} onNavigate={setView} anomalyCount={anomalies.data?.total ?? 0}
        summary={summary.data} asOf={anomalies.data?.as_of_date} />
      <main className="main">
        <Header health={health} onRefresh={refresh} refreshing={refreshing} />
        {view !== "dashboard" && <h2 className="view-title">{title}</h2>}
        <div className="layout">
          <div className="content">
            {view === "dashboard" && <DashboardView {...common} />}
            {view === "employees" && <EmployeesView {...common} />}
            {view === "tasks" && <TasksView {...common} />}
            {view === "access" && <AccessView {...common} />}
            {view === "assets" && <AssetsView {...common} />}
            {view === "anomalies" && <AnomaliesView {...common} />}
            {view === "reports" && <ReportsView {...common} />}
            {view === "health" && <HealthView health={health} />}
          </div>
          <div className="detail-col">
            <EmployeeDetail employeeId={selectedId} onClose={() => setSelectedId(null)} refreshKey={refreshKey} />
          </div>
        </div>
      </main>
    </div>
  );
}
