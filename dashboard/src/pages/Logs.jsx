import { useEffect, useState } from "react";
import apiClient from "../api/client";
import StatusBadge, { STATUS_BAR } from "../components/StatusBadge";
import { Checkbox, Field, Icon, Notice, PageHeader, Segmented } from "../components/ui";
import { useAuth } from "../auth/AuthContext";

const PAGE_SIZE = 25;
const emptyFilters = { date: "", user: "", gate: "", status: "" };
const emptyOverrideForm = { student_or_employee_id: "", direction: "entry", reason: "" };

const ADMIN_COLS = "minmax(0,1.6fr) 160px 84px minmax(0,1.5fr) minmax(0,1.2fr) 52px minmax(0,1.3fr) minmax(0,90px)";
const OFFICER_COLS = "minmax(0,1.5fr) 150px 76px minmax(0,1.4fr) minmax(0,1.1fr) 48px minmax(0,1.1fr) minmax(0,80px)";

function formatTimestamp(value) {
  const date = new Date(value);
  const day = date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const time = date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return `${day} · ${time}`;
}

// Method column - icon + word. A manual override is never just labeled as
// one - it's traceably tied to the specific dashboard account that made the
// call, and that account is what's shown, not folded into a "face" row.
function MethodCell({ log }) {
  let method;
  if (log.performed_by_username) {
    method = {
      label: `Manual override · ${log.performed_by_username}`,
      icon: "hand-pointing",
      className: "font-bold text-ink",
      title: `Manually logged by ${log.performed_by_username} - scanner was not used`,
    };
  } else if (log.verification_method === "confusable_pair_tiebreak") {
    // The face match itself was fine here - this row exists ONLY because the
    // matched person is on record as confusable with someone else (see
    // users.models.ConfusablePair), so a card tap was forced regardless.
    method = {
      label: "Lookalike tiebreak",
      icon: "users-three",
      className: "text-ink-600",
      title: "Confirmed match, but flagged as easily confused with someone similar - a card tap was required regardless of score",
    };
  } else if (log.verification_method === "face_and_card_tiebreak") {
    method = { label: "Face + card", icon: "identification-card", className: "text-ink-600" };
  } else if (log.verification_method === "face_only") {
    method = { label: "Face", icon: "user-focus", className: "text-ink-600" };
  } else if (log.verification_method === "nfc_and_face") {
    method = { label: "Card + face", icon: "identification-card", className: "text-ink-600" };
  } else if (log.verification_method === "nfc_only") {
    method = { label: "Card", icon: "identification-card", className: "text-ink-600" };
  } else {
    method = { label: log.verification_method, icon: "question", className: "text-ink-600" };
  }
  return (
    <span title={method.title} className={`flex min-w-0 items-center gap-s1 whitespace-nowrap ${method.className}`}>
      <Icon name={method.icon} bold size={16} />
      <span className="truncate">{method.label}</span>
    </span>
  );
}

function ManualOverrideForm({ username, onLogged }) {
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
    <form onSubmit={handleSubmit} className="flex w-full flex-none flex-col gap-s4 xl:w-[330px]">
      <div className="flex flex-col gap-s1 border-t-[3px] border-brass pt-s4">
        <span className="t-section flex items-center gap-s2">
          <Icon name="hand-pointing" bold size={20} />
          Manual override
        </span>
        <span className="text-sm leading-snug text-ink-600">
          Use only when the scanner fails. Check the person&rsquo;s physical ID first. Logged as &ldquo;Manual
          &middot; {username}&rdquo; and never merged with an automatic match.
        </span>
      </div>
      <Field label="Student/Employee ID">
        <input
          required
          value={form.student_or_employee_id}
          onChange={handleChange("student_or_employee_id")}
          className="input font-mono"
        />
      </Field>
      <div className="flex flex-col gap-s2">
        <span className="field-label">Direction</span>
        <Segmented
          value={form.direction}
          onChange={(direction) => setForm((prev) => ({ ...prev, direction }))}
          options={[
            { value: "entry", label: "Entry", icon: "sign-in" },
            { value: "exit", label: "Exit", icon: "sign-out" },
          ]}
        />
      </div>
      <Field label="Reason">
        <textarea
          required
          placeholder="What did you check? e.g. checked physical school ID"
          value={form.reason}
          onChange={handleChange("reason")}
          className="input"
        />
      </Field>
      {error && <Notice tone="danger">{error}</Notice>}
      {notice && <Notice tone="verified">{notice}</Notice>}
      <button type="submit" disabled={isSubmitting} className="btn-primary w-full">
        {isSubmitting ? "Logging…" : "Submit"}
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
    const header = ["Name", "Timestamp", "Direction", "Method", "Status", "Confidence", "Reason", "Gate", "On duty"];
    const rows = visibleLogs.map((log) => [
      log.person_name,
      log.timestamp,
      log.direction,
      log.verification_method,
      log.status,
      log.match_confidence != null ? Math.round(log.match_confidence * 100) : "",
      log.failure_reason,
      log.gate_location,
      log.on_duty_name || (log.unattended ? "Unattended" : ""),
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

  const cols = isSecurityOfficer ? OFFICER_COLS : ADMIN_COLS;
  const gate = user?.gateLocation || "no gate assigned";
  const firstShown = count === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const lastShown = (page - 1) * PAGE_SIZE + logs.length;

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow={isSecurityOfficer ? `${gate} · today only` : "Full history"}
        title={isSecurityOfficer ? `Logs · ${gate}` : "Logs"}
      >
        {!isSecurityOfficer && (
          <button type="button" onClick={exportCsv} className="btn-secondary">
            <Icon name="download-simple" size={16} />
            Export CSV
          </button>
        )}
      </PageHeader>

      <div className="mt-s6 flex flex-none flex-wrap items-end gap-s4">
        {!isSecurityOfficer && (
          <Field label="Date" eyebrow>
            <input type="date" value={filters.date} onChange={handleFilterChange("date")} className="input font-mono" />
          </Field>
        )}
        <Field label="Name" eyebrow className="min-w-[180px] flex-1">
          <span className="relative block">
            <Icon name="magnifying-glass" size={16} className="pointer-events-none absolute left-[10px] top-3 text-ink-600" />
            <input
              type="text"
              placeholder="Search by name"
              value={filters.user}
              onChange={handleFilterChange("user")}
              className="input pl-9"
            />
          </span>
        </Field>
        {!isSecurityOfficer && (
          <Field label="Gate" eyebrow className="w-[150px]">
            <input
              type="text"
              placeholder="All gates"
              value={filters.gate}
              onChange={handleFilterChange("gate")}
              className="input"
            />
          </Field>
        )}
        <Field label="Status" eyebrow className="w-[220px]">
          <select value={filters.status} onChange={handleFilterChange("status")} className="input">
            <option value="">All</option>
            <option value="success">Success</option>
            <option value="failed">Failed</option>
            <option value="spoof_suspected">Spoof suspected</option>
            <option value="occlusion_detected">Face covered</option>
          </select>
        </Field>
        <Checkbox checked={hideUnknown} onChange={(e) => setHideUnknown(e.target.checked)} label="Hide unknown" />
      </div>

      {loadError && (
        <Notice tone="danger" className="mt-s4">
          {loadError}
        </Notice>
      )}

      <div className="mt-s5 flex flex-col gap-s6 xl:flex-row">
        <div className="card flex min-w-0 flex-1 flex-col overflow-hidden">
          <div className="overflow-x-auto">
            <div className="min-w-[960px]">
              <div className="table-head grid h-9 items-center gap-s3 border-b border-line px-s4" style={{ gridTemplateColumns: cols }}>
                <span>Name</span>
                <span>Timestamp</span>
                <span>Direction</span>
                <span>Method</span>
                <span>Status</span>
                <span className="text-right">Conf.</span>
                <span>Reason</span>
                <span>Gate</span>
              </div>
              {visibleLogs.map((log) => {
                const bar = log.performed_by_username ? "#C89B3C" : STATUS_BAR[log.status] || "#C62828";
                return (
                  <div
                    key={log.id}
                    className="grid min-h-10 items-center gap-s3 border-b border-l-[3px] border-b-line py-s1 pl-[13px] pr-s4 text-sm"
                    style={{ gridTemplateColumns: cols, borderLeftColor: bar }}
                  >
                    <span className="truncate font-bold">{log.person_name || "Unknown"}</span>
                    <span className="whitespace-nowrap font-mono text-xs font-medium text-ink-600">
                      {formatTimestamp(log.timestamp)}
                    </span>
                    <span className="flex items-center gap-s1 capitalize">
                      <Icon name={log.direction === "exit" ? "sign-out" : "sign-in"} size={16} className="text-ink-600" />
                      {log.direction}
                    </span>
                    <MethodCell log={log} />
                    <StatusBadge status={log.status} />
                    <span className="text-right font-mono font-medium">
                      {log.match_confidence != null ? `${Math.round(log.match_confidence * 100)}%` : "—"}
                    </span>
                    <span className="min-w-0 truncate text-ink-600" title={log.failure_reason || undefined}>
                      {log.failure_reason || "—"}
                      {/* occlusion_detected can be true on a SUCCESS/FAILED/spoof row too - a
                          moment of occlusion seen during this encounter that later resolved.
                          Only shown here when it's not already what failure_reason says (an
                          OCCLUSION_DETECTED row's own reason already covers it). */}
                      {log.occlusion_detected && log.status !== "occlusion_detected" && (
                        <span className="ml-s1 text-prompt">(face briefly covered earlier)</span>
                      )}
                      {log.verification_method === "confusable_pair_tiebreak" && log.distinguishing_note && (
                        <span className="ml-s1 text-caution">(note: {log.distinguishing_note})</span>
                      )}
                    </span>
                    <span className="flex min-w-0 flex-col leading-tight">
                      <span className="truncate whitespace-nowrap text-ink-600">{log.gate_location}</span>
                      {/* Guard sign-in (Settings): who was on duty, or nobody. */}
                      {log.on_duty_name && (
                        <span className="truncate text-xs text-ink-600" title={`On duty: ${log.on_duty_name}`}>
                          On duty: {log.on_duty_name}
                        </span>
                      )}
                      {log.unattended && (
                        <span
                          className="truncate text-xs font-bold text-caution"
                          title="Guard sign-in was on, but no guard was signed in at this gate"
                        >
                          Unattended
                        </span>
                      )}
                    </span>
                  </div>
                );
              })}
              {!isLoading && visibleLogs.length === 0 && (
                <p className="px-s4 py-s5 text-sm text-ink-600">No matching logs.</p>
              )}
            </div>
          </div>
          <div className="flex h-12 flex-none items-center justify-between border-t border-line px-s4 text-sm text-ink-600">
            <span>
              <span className="font-mono font-medium text-ink">
                {firstShown}–{lastShown} of {count.toLocaleString()}
              </span>{" "}
              &middot; {PAGE_SIZE} per page
            </span>
            <div className="flex items-center gap-s2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="btn-secondary btn-sm gap-s1"
              >
                <Icon name="caret-left" bold size={14} />
                Previous
              </button>
              <button
                type="button"
                disabled={logs.length < PAGE_SIZE}
                onClick={() => setPage((p) => p + 1)}
                className="btn-secondary btn-sm gap-s1"
              >
                Next
                <Icon name="caret-right" bold size={14} />
              </button>
            </div>
          </div>
        </div>

        {isSecurityOfficer && (
          <ManualOverrideForm username={user?.username} onLogged={() => setRefreshKey((k) => k + 1)} />
        )}
      </div>
    </div>
  );
}
