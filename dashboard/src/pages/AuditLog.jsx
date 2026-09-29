import { useEffect, useState } from "react";
import apiClient from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Icon, Notice, PageHeader } from "../components/ui";

const ACTIONS = {
  account_created: { label: "Account created", icon: "user-plus" },
  account_updated: { label: "Account updated", icon: "user-gear" },
  account_deactivated: { label: "Account deactivated", icon: "user-minus" },
  deactivation_requested: { label: "Deactivation requested", icon: "clock" },
  deactivation_approved: { label: "Deactivation approved", icon: "check-circle" },
  deactivation_rejected: { label: "Deactivation rejected", icon: "x-circle" },
  manual_override: { label: "Manual override logged", icon: "hand-pointing" },
  person_deleted_permanently: { label: "Person deleted permanently", icon: "trash", danger: true },
};

const COLS = "170px 140px minmax(0,1.3fr) minmax(0,1.6fr) minmax(0,1.6fr)";

function formatTimestamp(value) {
  const date = new Date(value);
  const day = date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const time = date.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return `${day} · ${time}`;
}

// The detail blob is free-form JSON - shown as "key: value" pairs rather
// than raw JSON so a reason or note reads as text.
function formatDetail(detail) {
  if (!detail) return "—";
  if (typeof detail !== "object") return String(detail);
  const parts = Object.entries(detail).map(([key, value]) =>
    `${key.replace(/_/g, " ")}: ${typeof value === "object" ? JSON.stringify(value) : value}`
  );
  return parts.length ? parts.join(" · ") : "—";
}

export default function AuditLog() {
  const { user } = useAuth();
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState("");
  const [actorFilter, setActorFilter] = useState("");
  const [actionFilter, setActionFilter] = useState("");

  useEffect(() => {
    apiClient
      .get("/audit-log/", { params: { page_size: 100 } })
      .then(({ data }) => setEntries(data.results || data))
      .catch(() => setError("Could not load the audit log. Try refreshing the page."));
  }, []);

  const isSaso = user?.role === "saso";
  const actors = [...new Set(entries.map((entry) => entry.actor_username).filter(Boolean))].sort();
  const visible = entries.filter(
    (entry) => (!actorFilter || entry.actor_username === actorFilter) && (!actionFilter || entry.action === actionFilter)
  );

  return (
    <div className="flex flex-col">
      <PageHeader eyebrow={isSaso ? "Your actions · SASO view" : "All actors · Admin view"} title="Audit Log" />

      <p className="mt-s3 max-w-[760px] text-sm text-ink-600">
        {isSaso
          ? "A record of actions you've taken - account and settings changes made by others aren't shown here."
          : "A record of account changes, deactivation requests, and manual overrides across the system."}
      </p>

      <div className="mt-s5 flex flex-none flex-wrap gap-s4">
        {!isSaso && (
          <select value={actorFilter} onChange={(e) => setActorFilter(e.target.value)} className="input w-[170px]">
            <option value="">All actors</option>
            {actors.map((actor) => (
              <option key={actor} value={actor}>
                {actor}
              </option>
            ))}
          </select>
        )}
        <select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)} className="input w-[220px]">
          <option value="">All actions</option>
          {Object.entries(ACTIONS).map(([key, action]) => (
            <option key={key} value={key}>
              {action.label}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <Notice tone="danger" className="mt-s4">
          {error}
        </Notice>
      )}

      <div className="card mt-s4 overflow-hidden">
        <div className="overflow-x-auto">
          <div className="min-w-[900px]">
            <div className="table-head grid h-9 items-center gap-s4 border-b border-line px-s4" style={{ gridTemplateColumns: COLS }}>
              <span>Timestamp</span>
              <span>Actor</span>
              <span>Action</span>
              <span>Target</span>
              <span>Detail</span>
            </div>
            {visible.map((entry) => {
              const action = ACTIONS[entry.action] || { label: entry.action, icon: "note" };
              const bar = entry.action === "manual_override" ? "#C89B3C" : action.danger ? "#C62828" : "#FFFFFF";
              const detail = formatDetail(entry.detail);
              return (
                <div
                  key={entry.id}
                  className="grid min-h-10 items-center gap-s4 border-b border-l-[3px] border-b-line py-s1 pl-[13px] pr-s4 text-sm"
                  style={{ gridTemplateColumns: COLS, borderLeftColor: bar }}
                >
                  <span className="font-mono text-xs font-medium text-ink-600">{formatTimestamp(entry.timestamp)}</span>
                  <span className="truncate font-mono text-[13px] font-medium">{entry.actor_username || "—"}</span>
                  <span
                    className={`flex items-center gap-s2 whitespace-nowrap font-bold ${action.danger ? "text-danger" : "text-ink"}`}
                  >
                    <Icon name={action.icon} size={16} />
                    {action.label}
                  </span>
                  <span className="truncate" title={entry.target_description || undefined}>
                    {entry.target_description || "—"}
                  </span>
                  <span className="truncate text-ink-600" title={detail}>
                    {detail}
                  </span>
                </div>
              );
            })}
            {visible.length === 0 && !error && (
              <p className="px-s4 py-s5 text-sm text-ink-600">
                {entries.length === 0 ? "No audit entries yet." : "No entries match these filters."}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
