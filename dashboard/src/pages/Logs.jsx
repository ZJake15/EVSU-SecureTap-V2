import { useEffect, useState } from "react";
import apiClient from "../api/client";
import StatusBadge from "../components/StatusBadge";

const emptyFilters = { date: "", user: "", gate: "", status: "" };

export default function Logs() {
  const [filters, setFilters] = useState(emptyFilters);
  const [logs, setLogs] = useState([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(false);
  const [hideUnknown, setHideUnknown] = useState(false);

  const visibleLogs = hideUnknown ? logs.filter((log) => log.status !== "failed") : logs;

  useEffect(() => {
    let isCancelled = false;
    setIsLoading(true);
    const params = { page };
    if (filters.date) params.timestamp__date = filters.date;
    if (filters.user) params.person__full_name = filters.user;
    if (filters.gate) params.gate_location = filters.gate;
    if (filters.status) params.status = filters.status;

    apiClient
      .get("/logs/", { params })
      .then(({ data }) => {
        if (isCancelled) return;
        setLogs(data.results);
        setCount(data.count);
      })
      .finally(() => !isCancelled && setIsLoading(false));

    return () => {
      isCancelled = true;
    };
  }, [filters, page]);

  const handleFilterChange = (field) => (event) => {
    setPage(1);
    setFilters((prev) => ({ ...prev, [field]: event.target.value }));
  };

  const exportCsv = () => {
    const header = ["Name", "Timestamp", "Direction", "Method", "Status", "Confidence", "Reason", "Gate"];
    const rows = visibleLogs.map((log) => [
      log.person_name,
      log.timestamp,
      log.direction,
      log.verification_method,
      log.status,
      log.match_confidence != null ? Math.round(log.match_confidence * 100) : "",
      log.failure_reason,
      log.gate_location,
    ]);
    const csv = [header, ...rows]
      .map((row) => row.map((cell) => `"${String(cell ?? "").replace(/"/g, '""')}"`).join(","))
      .join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `entry-logs-${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">Entry / Exit Logs</h1>
        <button
          type="button"
          onClick={exportCsv}
          className="rounded bg-maroon px-3 py-1.5 text-sm font-medium text-white hover:bg-maroon-600"
        >
          Export CSV
        </button>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <input
          type="date"
          value={filters.date}
          onChange={handleFilterChange("date")}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
        <input
          type="text"
          placeholder="Search name"
          value={filters.user}
          onChange={handleFilterChange("user")}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
        <input
          type="text"
          placeholder="Gate"
          value={filters.gate}
          onChange={handleFilterChange("gate")}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
        <select
          value={filters.status}
          onChange={handleFilterChange("status")}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm"
        >
          <option value="">All statuses</option>
          <option value="success">Success</option>
          <option value="failed">Failed</option>
          <option value="spoof_suspected">Spoof suspected</option>
        </select>
      </div>

      <label className="mb-3 flex items-center gap-2 text-sm text-gray-600">
        <input
          type="checkbox"
          checked={hideUnknown}
          onChange={(e) => setHideUnknown(e.target.checked)}
          className="rounded border-gray-300"
        />
        Hide unknown/failed entries
      </label>

      <div className="overflow-x-auto rounded border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              {["Name", "Timestamp", "Direction", "Method", "Status", "Confidence", "Reason", "Gate"].map((heading) => (
                <th key={heading} className="px-4 py-2 text-left font-medium text-gray-600">
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {visibleLogs.map((log) => (
              <tr key={log.id}>
                <td className="px-4 py-2">{log.person_name || "Unknown"}</td>
                <td className="px-4 py-2">{new Date(log.timestamp).toLocaleString()}</td>
                <td className="px-4 py-2 capitalize">{log.direction}</td>
                <td className="px-4 py-2">{log.verification_method}</td>
                <td className="px-4 py-2">
                  <StatusBadge status={log.status} />
                </td>
                <td className="px-4 py-2">
                  {log.match_confidence != null ? `${Math.round(log.match_confidence * 100)}%` : "—"}
                </td>
                <td className="px-4 py-2 text-gray-600">{log.failure_reason || "—"}</td>
                <td className="px-4 py-2">{log.gate_location}</td>
              </tr>
            ))}
            {!isLoading && visibleLogs.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-6 text-center text-gray-500">
                  No matching logs.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex items-center justify-between text-sm text-gray-600">
        <span>{count} total records</span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded border border-gray-300 px-2 py-1 disabled:opacity-50"
          >
            Previous
          </button>
          <button
            type="button"
            disabled={logs.length < 25}
            onClick={() => setPage((p) => p + 1)}
            className="rounded border border-gray-300 px-2 py-1 disabled:opacity-50"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
