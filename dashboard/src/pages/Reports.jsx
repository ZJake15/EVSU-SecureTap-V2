import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import apiClient from "../api/client";

export default function Reports() {
  const [summary, setSummary] = useState(null);

  useEffect(() => {
    apiClient.get("/reports/summary").then(({ data }) => setSummary(data));
  }, []);

  if (!summary) {
    return <p className="text-sm text-gray-500">Loading report...</p>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-gray-900">Reports</h1>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Entries today" value={summary.entries_today} />
        <StatCard label="Failed verifications today" value={summary.failed_today} />
        <StatCard label="Busiest hour" value={summary.peak_hour ?? "N/A"} />
      </div>

      <div className="rounded border border-gray-200 bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-gray-900">Entries per hour (today)</h2>
        <div className="h-72 w-full">
          <ResponsiveContainer>
            <BarChart data={summary.entries_by_hour}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="hour" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#7B1113" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="rounded border border-gray-200 bg-white p-4 shadow-sm">
        <h2 className="mb-3 text-sm font-semibold text-gray-900">Entries per day (last 7 days)</h2>
        <div className="h-72 w-full">
          <ResponsiveContainer>
            <BarChart data={summary.entries_by_day}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="date" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#7B1113" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value }) {
  return (
    <div className="rounded border border-gray-200 bg-white p-4 shadow-sm">
      <p className="text-xs uppercase tracking-wide text-gray-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-gray-900">{value}</p>
    </div>
  );
}
