import { useEffect, useState } from "react";
import apiClient from "../api/client";
import StatusBadge from "../components/StatusBadge";
import { useAuth } from "../auth/AuthContext";

const emptyFilters = { date: "", user: "", gate: "", status: "" };
const emptyOverrideForm = { student_or_employee_id: "", direction: "entry", reason: "" };

const filterInputClass =
  "rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm placeholder:text-ink-400 focus:border-maroon focus:outline-none focus:ring-1 focus:ring-maroon";

function ManualOverrideForm({ onLogged }) {
  const [form, setForm] = useState(emptyOverrideForm);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleChange = (field) => (event) => setForm((prev) => ({ ...prev, [field]: event.target.value }));

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");
    setNotice("");
    setIsSubmitting(true);
    try {
      const { data } = await apiClient.post("/logs/manual-override", form);
      setNotice(`Logged: ${data.person_name} (${data.direction}).`);
      setForm(emptyOverrideForm);
      onLogged();
    } catch (err) {
      setError(err.response?.data?.detail || "Could not log this entry. Check the ID and try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="mb-4 rounded-xl border border-ink-900/10 bg-white p-4 shadow-sm">
      <h2 className="font-display text-base font-semibold text-ink-900">Manual override</h2>
      <p className="mt-1 text-xs text-ink-500">
        If the scanner fails, visually check the person&rsquo;s physical ID, then log the entry here. This is
        tied to your account and recorded distinctly from an automatic face/card match - it is never merged
        with one.
      </p>
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-4">
        <input
          required
          placeholder="Student / Employee ID"
          value={form.student_or_employee_id}
          onChange={handleChange("student_or_employee_id")}
          className={filterInputClass}
        />
        <select value={form.direction} onChange={handleChange("direction")} className={filterInputClass}>
          <option value="entry">Entry</option>
          <option value="exit">Exit</option>
        </select>
        <input
          required
          placeholder="What did you check? (e.g. checked physical school ID)"
          value={form.reason}
          onChange={handleChange("reason")}
          className={`${filterInputClass} sm:col-span-2`}
        />
      </div>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      {notice && <p className="mt-2 text-sm text-emerald-700">{notice}</p>}
      <button
        type="submit"
        disabled={isSubmitting}
        className="mt-3 rounded-lg bg-maroon px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-maroon-600 disabled:opacity-50"
      >
        Log entry
      </button>
    </form>
  );
}

export default function Logs() {
  const { user } = useAuth();
  const isSecurityOfficer = user?.role === "security_officer";
  const [filters, setFilters] = useState(emptyFilters);
  const [logs, setLogs] = useState([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(false);
  const [hideUnknown, setHideUnknown] = useState(false);
  const [loadError, setLoadError] = useState("");

  const [refreshKey, setRefreshKey] = useState(0);
  const visibleLogs = hideUnknown ? logs.filter((log) => log.status !== "failed") : logs;

  useEffect(() => {
    let isCancelled = false;
    setIsLoading(true);
    setLoadError("");
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
      .catch(() => {
        if (isCancelled) return;
        setLogs([]);
        setCount(0);
        setLoadError("Could not load logs. Try refreshing the page.");
      })
      .finally(() => !isCancelled && setIsLoading(false));

    return () => {
      isCancelled = true;
    };
  }, [filters, page, refreshKey]);

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
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="font-display text-2xl font-semibold text-ink-900">Entry / Exit Logs</h1>
          <p className="mt-1 text-sm text-ink-500">
            {isSecurityOfficer
              ? "Today's entries at your assigned gate."
              : "Filterable history of every gate verification."}
          </p>
        </div>
        {!isSecurityOfficer && (
          <button
            type="button"
            onClick={exportCsv}
            className="rounded-lg bg-maroon px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-maroon-600"
          >
            Export CSV
          </button>
        )}
      </div>

      {isSecurityOfficer && <ManualOverrideForm onLogged={() => setRefreshKey((k) => k + 1)} />}

      {loadError && <p className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{loadError}</p>}

      <div className="mb-4 rounded-xl border border-ink-900/10 bg-white p-4 shadow-sm">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {!isSecurityOfficer && (
            <input
              type="date"
              value={filters.date}
              onChange={handleFilterChange("date")}
              className={filterInputClass}
            />
          )}
          <input
            type="text"
            placeholder="Search name"
            value={filters.user}
            onChange={handleFilterChange("user")}
            className={filterInputClass}
          />
          <input
            type="text"
            placeholder="Gate"
            value={filters.gate}
            onChange={handleFilterChange("gate")}
            className={filterInputClass}
          />
          <select value={filters.status} onChange={handleFilterChange("status")} className={filterInputClass}>
            <option value="">All statuses</option>
            <option value="success">Success</option>
            <option value="failed">Failed</option>
            <option value="spoof_suspected">Spoof suspected</option>
            <option value="occlusion_detected">Occlusion detected</option>
          </select>
        </div>

        <label className="mt-3 flex items-center gap-2 text-sm text-ink-600">
          <input
            type="checkbox"
            checked={hideUnknown}
            onChange={(e) => setHideUnknown(e.target.checked)}
            className="rounded border-ink-300 text-maroon focus:ring-maroon"
          />
          Hide unknown/failed entries
        </label>
      </div>

      <div className="overflow-x-auto rounded-xl border border-ink-900/10 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-ink-900/10 text-sm">
          <thead className="bg-parchment-100">
            <tr>
              {["Name", "Timestamp", "Direction", "Method", "Status", "Confidence", "Reason", "Gate"].map((heading) => (
                <th
                  key={heading}
                  className="border-b-2 border-maroon/20 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-ink-500"
                >
                  {heading}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-900/5">
            {visibleLogs.map((log) => (
              <tr
                key={log.id}
                className={`transition-colors hover:bg-parchment-50 ${
                  log.status === "spoof_suspected"
                    ? "bg-purple-50/40"
                    : log.status === "occlusion_detected"
                      ? "bg-teal-50/40"
                      : log.status === "failed"
                        ? "bg-red-50/40"
                        : ""
                }`}
              >
                <td className="px-4 py-2.5 font-medium text-ink-900">{log.person_name || "Unknown"}</td>
                <td className="px-4 py-2.5 text-ink-700">{new Date(log.timestamp).toLocaleString()}</td>
                <td className="px-4 py-2.5 capitalize text-ink-700">{log.direction}</td>
                <td className="px-4 py-2.5 text-ink-700">
                  {log.performed_by_username ? (
                    // A manual override is never just labeled as one - it's
                    // traceably tied to the specific dashboard account that
                    // made the call, and that account is what's shown here,
                    // not folded into an unlabeled "face" or "nfc" row.
                    <span
                      title={`Manually logged by ${log.performed_by_username} - scanner was not used`}
                      className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-800"
                    >
                      Manual override &middot; {log.performed_by_username}
                    </span>
                  ) : log.verification_method === "confusable_pair_tiebreak" ? (
                    // The face match itself was fine here - this row exists
                    // ONLY because the matched person is on record as
                    // confusable with someone else (see users.models.
                    // ConfusablePair), so a card tap was forced regardless.
                    // Kept visually distinct from an ordinary ambiguous-match
                    // tiebreak for exactly that reason.
                    <span
                      title="Confirmed match, but flagged as easily confused with someone similar - a card tap was required regardless of score"
                      className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-800"
                    >
                      Confusable pair tiebreak
                    </span>
                  ) : (
                    log.verification_method
                  )}
                </td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={log.status} />
                </td>
                <td className="px-4 py-2.5 text-ink-700">
                  {log.match_confidence != null ? `${Math.round(log.match_confidence * 100)}%` : "—"}
                </td>
                <td className="px-4 py-2.5 text-ink-500">
                  {log.failure_reason || "—"}
                  {/* occlusion_detected can be true on a SUCCESS/FAILED/spoof row too - a
                      moment of occlusion seen during this encounter that later resolved.
                      Only shown here when it's not already what failure_reason says (an
                      OCCLUSION_DETECTED row's own reason already covers it). */}
                  {log.occlusion_detected && log.status !== "occlusion_detected" && (
                    <span className="ml-1 text-teal-700">(face briefly covered earlier)</span>
                  )}
                  {log.verification_method === "confusable_pair_tiebreak" && log.distinguishing_note && (
                    <span className="ml-1 text-amber-700">(note: {log.distinguishing_note})</span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-ink-700">{log.gate_location}</td>
              </tr>
            ))}
            {!isLoading && visibleLogs.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-6 text-center text-ink-400">
                  No matching logs.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex items-center justify-between text-sm text-ink-600">
        <span>{count} total records</span>
        <div className="flex gap-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
            className="rounded-lg border border-ink-200 bg-white px-3 py-1.5 font-medium shadow-sm disabled:opacity-50"
          >
            Previous
          </button>
          <button
            type="button"
            disabled={logs.length < 25}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-lg border border-ink-200 bg-white px-3 py-1.5 font-medium shadow-sm disabled:opacity-50"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
