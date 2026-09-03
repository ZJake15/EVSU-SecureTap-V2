import { useEffect, useState } from "react";
import apiClient from "../api/client";
import { useAuth } from "../auth/AuthContext";

const ACTION_LABELS = {
  account_created: "Account created",
  account_updated: "Account updated",
  account_deactivated: "Account deactivated",
  deactivation_requested: "Deactivation requested",
  deactivation_approved: "Deactivation approved",
  deactivation_rejected: "Deactivation rejected",
  manual_override: "Manual override logged",
  person_deleted_permanently: "Person deleted permanently",
};

export default function AuditLog() {
  const { user } = useAuth();
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    apiClient
      .get("/audit-log/", { params: { page_size: 100 } })
      .then(({ data }) => setEntries(data.results || data))
      .catch(() => setError("Could not load the audit log. Try refreshing the page."));
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold text-ink-900">Audit Log</h1>
        <p className="mt-1 text-sm text-ink-500">
          {user?.role === "saso"
            ? "A record of actions you've taken - account and settings changes made by others aren't shown here."
            : "A record of account changes, settings changes, deactivation requests, and manual overrides across the system."}
        </p>
      </div>

      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      <div className="overflow-x-auto rounded-xl border border-ink-900/10 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-ink-900/10 text-sm">
          <thead className="bg-parchment-100">
            <tr>
              {["Timestamp", "Actor", "Action", "Target", "Detail"].map((h) => (
                <th
                  key={h}
                  className="border-b-2 border-maroon/20 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-ink-500"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-900/5">
            {entries.map((entry) => (
              <tr key={entry.id} className="transition-colors hover:bg-parchment-50">
                <td className="px-4 py-2.5 text-ink-700">{new Date(entry.timestamp).toLocaleString()}</td>
                <td className="px-4 py-2.5 font-medium text-ink-900">{entry.actor_username || "—"}</td>
                <td className="px-4 py-2.5 text-ink-700">{ACTION_LABELS[entry.action] || entry.action}</td>
                <td className="px-4 py-2.5 text-ink-700">{entry.target_description || "—"}</td>
                <td className="px-4 py-2.5 text-ink-500">
                  {entry.detail ? (
                    <span className="font-mono text-xs">{JSON.stringify(entry.detail)}</span>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
            {entries.length === 0 && !error && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-ink-400">
                  No audit entries yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
