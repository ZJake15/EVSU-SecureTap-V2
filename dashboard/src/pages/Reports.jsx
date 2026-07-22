import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import apiClient from "../api/client";

const RANGE_OPTIONS = [
  { label: "Today", days: 1 },
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
];

function exportMethodCsv(entriesByMethod) {
  const header = ["Date", "Face", "Face + NFC", "Failed"];
  const rows = entriesByMethod.map((row) => [row.date, row.face, row.face_nfc, row.failed]);
  const csv = [header, ...rows]
    .map((row) => row.map((cell) => `"${String(cell ?? "").replace(/"/g, '""')}"`).join(","))
    .join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `entries-by-method-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

export default function Reports() {
  const [summary, setSummary] = useState(null);
  const [farFrr, setFarFrr] = useState(null);
  const [error, setError] = useState("");
  const [days, setDays] = useState(7);
  const [retryTick, setRetryTick] = useState(0);

  useEffect(() => {
    let isCancelled = false;
    setError("");
    Promise.all([
      apiClient.get("/reports/summary", { params: { days } }),
      apiClient.get("/reports/far-frr"),
    ])
      .then(([summaryRes, farFrrRes]) => {
        if (isCancelled) return;
        setSummary(summaryRes.data);
        setFarFrr(farFrrRes.data);
      })
      .catch(() => {
        if (!isCancelled) setError("Could not load the report. Check your connection and try again.");
      });
    return () => {
      isCancelled = true;
    };
  }, [days, retryTick]);

  if (error) {
    return (
      <div>
        <h1 className="mb-3 text-xl font-semibold text-gray-900">Reports</h1>
        <p className="text-sm text-red-600">{error}</p>
        <button
          type="button"
          onClick={() => setRetryTick((t) => t + 1)}
          className="mt-2 rounded bg-maroon px-3 py-1.5 text-sm font-medium text-white hover:bg-maroon-600"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!summary || !farFrr) {
    return <p className="text-sm text-gray-500">Loading report...</p>;
  }

  const farFrrChartData = farFrr.table.map((row) => ({
    threshold: row.threshold,
    FAR: row.far != null ? Number((row.far * 100).toFixed(2)) : null,
    FRR: row.frr != null ? Number((row.frr * 100).toFixed(2)) : null,
  }));

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">Reports</h1>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm"
        >
          {RANGE_OPTIONS.map((option) => (
            <option key={option.days} value={option.days}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard label="Entries today" value={summary.entries_today} />
        <StatCard label="Failed verifications today" value={summary.failed_today} />
        <StatCard label="Busiest hour today" value={summary.peak_hour ?? "N/A"} />
      </div>

      <div className="rounded border border-gray-200 bg-white p-4 shadow-sm">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-900">
            Entries by method ({RANGE_OPTIONS.find((o) => o.days === days)?.label.toLowerCase()})
          </h2>
          <button
            type="button"
            onClick={() => exportMethodCsv(summary.entries_by_method)}
            className="rounded bg-maroon px-3 py-1.5 text-xs font-medium text-white hover:bg-maroon-600"
          >
            Export CSV
          </button>
        </div>
        <div className="h-72 w-full">
          <ResponsiveContainer>
            <BarChart data={summary.entries_by_method}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="date" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Legend />
              <Bar dataKey="face" name="Face" stackId="method" fill="#16a34a" />
              <Bar dataKey="face_nfc" name="Face + NFC" stackId="method" fill="#d97706" />
              <Bar dataKey="failed" name="Failed" stackId="method" fill="#dc2626" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded border border-gray-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-gray-900">Confidence score distribution</h2>
          <div className="h-64 w-full">
            <ResponsiveContainer>
              <BarChart data={summary.confidence_histogram}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="bucket" tick={{ fontSize: 10 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
                <Tooltip />
                <Bar dataKey="count" fill="#7B1113" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div className="rounded border border-gray-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-gray-900">Busiest hours</h2>
          <div className="h-64 w-full">
            <ResponsiveContainer>
              <BarChart data={summary.busiest_hours}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="hour" tick={{ fontSize: 10 }} interval={2} />
                <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
                <Tooltip />
                <Bar dataKey="count" fill="#7B1113" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
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

      <div className="rounded border border-gray-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-semibold text-gray-900">False-accept / false-reject rate</h2>
        <p className="mb-3 text-xs text-gray-500">
          {farFrr.note} Based on {farFrr.same_person_count} same-person and {farFrr.cross_person_count}{" "}
          cross-person comparisons among currently enrolled photos.
        </p>
        <div className="h-72 w-full">
          <ResponsiveContainer>
            <LineChart data={farFrrChartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis dataKey="threshold" tick={{ fontSize: 12 }} />
              <YAxis unit="%" tick={{ fontSize: 12 }} />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="FAR" stroke="#dc2626" strokeWidth={2} dot={{ r: 3 }} />
              <Line type="monotone" dataKey="FRR" stroke="#2563eb" strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
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
