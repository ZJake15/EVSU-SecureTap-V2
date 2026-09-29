import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import apiClient from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Icon, Notice, PageHeader, Segmented } from "../components/ui";

const RANGE_OPTIONS = [
  { label: "Today", days: 1 },
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
];

const C = {
  ink: "#121416",
  ink6: "#4B5157",
  ink4: "#8A9097",
  line: "#D9DCDF",
  maroon: "#7B1113",
  danger: "#C62828",
  prompt: "#1D5FA8",
};

const TICK = { fontFamily: "IBM Plex Mono", fontSize: 11, fill: C.ink6 };
const TOOLTIP = {
  contentStyle: {
    border: `1px solid ${C.line}`,
    borderRadius: 3,
    fontFamily: "Atkinson Hyperlegible Next",
    fontSize: 13,
  },
  cursor: { fill: "#EEF0F2" },
};

const METHODS = [
  { key: "face", label: "Face", color: C.maroon },
  { key: "face_nfc", label: "Face + card", color: C.ink6 },
  { key: "failed", label: "Failed", color: C.danger },
  { key: "occluded", label: "Face covered", color: C.prompt },
];

function exportMethodCsv(entriesByMethod) {
  const header = ["Date", "Face", "Face + NFC", "Failed", "Occluded"];
  const rows = entriesByMethod.map((row) => [row.date, row.face, row.face_nfc, row.failed, row.occluded]);
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

function localDateKey(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

// entries_by_day is sparse (only days that had a pass) - fill in the whole
// last week so a quiet day shows as a zero bar instead of vanishing.
function lastSevenDays(entriesByDay) {
  const counts = Object.fromEntries(entriesByDay.map((row) => [row.date, row.count]));
  return Array.from({ length: 7 }, (_, i) => {
    const date = new Date();
    date.setDate(date.getDate() - (6 - i));
    const key = localDateKey(date);
    const weekday = date.toLocaleDateString("en-US", { weekday: "short" }).slice(0, 2);
    return { key, label: `${weekday} ${date.getDate()}`, count: counts[key] || 0, isToday: i === 6 };
  });
}

function hourLabel(hour) {
  if (!hour) return "N/A";
  const h = Number(hour.slice(0, 2));
  const suffix = h < 12 ? "AM" : "PM";
  return `${h % 12 === 0 ? 12 : h % 12}:00 ${suffix}`;
}

function ChartCard({ title, className = "", children }) {
  return (
    <div className={`card flex min-w-0 flex-col gap-s3 px-s4 pb-s3 pt-s4 ${className}`}>
      {typeof title === "string" ? (
        <span className="font-display stretch-semi text-base font-bold">{title}</span>
      ) : (
        title
      )}
      {children}
    </div>
  );
}

export default function Reports() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [summary, setSummary] = useState(null);
  const [farFrr, setFarFrr] = useState(null);
  const [threshold, setThreshold] = useState(null);
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

  // The live match threshold, to mark on the FAR/FRR chart. Settings is
  // Admin-only, so a SASO simply gets the chart without the marker.
  useEffect(() => {
    if (!isAdmin) return;
    apiClient
      .get("/settings")
      .then(({ data }) => setThreshold(data.face_match_similarity_threshold ?? null))
      .catch(() => {});
  }, [isAdmin]);

  const rangeLabel = RANGE_OPTIONS.find((o) => o.days === days)?.label || "";
  const todayText = new Date()
    .toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
    .toUpperCase();

  const header = (
    <PageHeader eyebrow={days === 1 ? `Today · ${todayText}` : rangeLabel} title="Reports">
      <Segmented
        value={days}
        onChange={setDays}
        options={RANGE_OPTIONS.map((o) => ({ value: o.days, label: o.label }))}
      />
    </PageHeader>
  );

  if (error) {
    return (
      <div className="flex flex-col">
        {header}
        <Notice tone="danger" className="mt-s6 max-w-[640px] items-center">
          <span className="mr-s3">{error}</span>
          <button type="button" onClick={() => setRetryTick((t) => t + 1)} className="btn-secondary btn-sm">
            <Icon name="arrows-clockwise" size={14} />
            Retry
          </button>
        </Notice>
      </div>
    );
  }

  if (!summary || !farFrr) {
    return (
      <div className="flex flex-col">
        {header}
        <p className="mt-s6 text-sm text-ink-600">Loading report…</p>
      </div>
    );
  }

  const methodTotals = METHODS.map((m) => ({
    ...m,
    n: summary.entries_by_method.reduce((sum, row) => sum + (row[m.key] || 0), 0),
  }));
  const methodSum = methodTotals.reduce((sum, m) => sum + m.n, 0);

  const hours = summary.busiest_hours.map((row) => ({ ...row, h: Number(row.hour.slice(0, 2)) }));
  const peakCount = Math.max(0, ...hours.map((row) => row.count));
  const week = lastSevenDays(summary.entries_by_day);
  const confidence = summary.confidence_histogram.map((row) => ({ ...row, label: row.bucket.split("-")[0] }));

  const farFrrChartData = farFrr.table.map((row) => ({
    threshold: row.threshold,
    FAR: row.far != null ? Number((row.far * 100).toFixed(2)) : null,
    FRR: row.frr != null ? Number((row.frr * 100).toFixed(2)) : null,
  }));
  // Snap the live threshold to the nearest tabulated one so the marker lands
  // on the category axis.
  const thresholdTick =
    threshold != null && farFrrChartData.length
      ? farFrrChartData.reduce((best, row) =>
          Math.abs(row.threshold - threshold) < Math.abs(best.threshold - threshold) ? row : best
        ).threshold
      : null;

  return (
    <div className="flex flex-col">
      {header}

      <div className="mt-s5 grid grid-cols-1 items-end gap-s6 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="flex flex-wrap items-end gap-y-s4">
          <div className="flex flex-col gap-s1 pr-s6">
            <span className="t-eyebrow">Entries today</span>
            <span className="t-hero">{summary.entries_today}</span>
          </div>
          <div className="flex flex-col gap-s2 border-l border-line px-s5 pb-0.5">
            <span className={`t-eyebrow ${summary.failed_today > 0 ? "text-danger" : ""}`}>Failed verifications</span>
            <span className={`t-stat ${summary.failed_today > 0 ? "text-danger" : ""}`}>{summary.failed_today}</span>
          </div>
          <div className="flex flex-col gap-s2 border-l border-line px-s5 pb-0.5">
            <span className="t-eyebrow">Busiest hour</span>
            <span className="t-stat">{hourLabel(summary.peak_hour)}</span>
          </div>
        </div>

        <div className="flex flex-col gap-s3">
          <div className="flex items-center justify-between gap-s4">
            <span className="t-eyebrow">Entries by method &middot; {rangeLabel.toLowerCase()}</span>
            <button
              type="button"
              onClick={() => exportMethodCsv(summary.entries_by_method)}
              className="btn-secondary btn-sm"
            >
              <Icon name="download-simple" size={14} />
              Export CSV
            </button>
          </div>
          <div className="flex h-4 gap-0.5 bg-canvas">
            {methodSum > 0 &&
              methodTotals
                .filter((m) => m.n > 0)
                .map((m) => <span key={m.key} style={{ flex: m.n, background: m.color }} title={`${m.label}: ${m.n}`} />)}
          </div>
          <div className="flex flex-wrap gap-x-s4 gap-y-s2 text-xs">
            {methodTotals.map((m) => (
              <span key={m.key} className="flex items-center gap-s2 text-ink-600">
                <span className="h-[10px] w-[10px]" style={{ background: m.color }} />
                {m.label} <span className="font-mono font-semibold text-ink">{m.n}</span>
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-s6 grid grid-cols-1 gap-x-s6 gap-y-s5 xl:grid-cols-[minmax(0,8fr)_minmax(0,4fr)]">
        <ChartCard title={`Busiest hours · ${rangeLabel.toLowerCase()}`}>
          <div className="h-[230px]">
            <ResponsiveContainer>
              <BarChart data={hours} margin={{ top: 4, right: 0, bottom: 0, left: -24 }} barCategoryGap={2}>
                <CartesianGrid vertical={false} stroke={C.line} />
                <XAxis
                  dataKey="h"
                  tick={TICK}
                  tickLine={false}
                  axisLine={{ stroke: C.line }}
                  interval={0}
                  tickFormatter={(h) => (h % 3 === 0 ? String(h) : "")}
                />
                <YAxis allowDecimals={false} tick={TICK} tickLine={false} axisLine={false} />
                <Tooltip {...TOOLTIP} labelFormatter={(h) => `${String(h).padStart(2, "0")}:00`} />
                <Bar dataKey="count" name="Passes" isAnimationActive={false}>
                  {hours.map((row) => (
                    <Cell key={row.h} fill={row.count === peakCount && peakCount > 0 ? C.maroon : C.ink6} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>

        <ChartCard title="Entries per day">
          <div className="h-[230px]">
            <ResponsiveContainer>
              <BarChart data={week} margin={{ top: 18, right: 0, bottom: 0, left: 0 }} barCategoryGap={6}>
                <XAxis dataKey="label" tick={TICK} tickLine={false} axisLine={{ stroke: C.line }} interval={0} />
                <Tooltip {...TOOLTIP} />
                <Bar dataKey="count" name="Passes" isAnimationActive={false}>
                  {week.map((row) => (
                    <Cell key={row.key} fill={row.isToday ? C.maroon : C.ink6} />
                  ))}
                  <LabelList dataKey="count" position="top" style={{ ...TICK, fontSize: 10 }} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>

        <div className="grid grid-cols-1 gap-s5 xl:col-span-2 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <ChartCard title="Confidence score distribution">
            <div className="h-[230px]">
              <ResponsiveContainer>
                <BarChart data={confidence} margin={{ top: 4, right: 0, bottom: 0, left: -24 }} barCategoryGap={2}>
                  <CartesianGrid vertical={false} stroke={C.line} />
                  <XAxis dataKey="label" tick={TICK} tickLine={false} axisLine={{ stroke: C.line }} interval={0} />
                  <YAxis allowDecimals={false} tick={TICK} tickLine={false} axisLine={false} />
                  <Tooltip {...TOOLTIP} labelFormatter={(_, payload) => payload?.[0]?.payload?.bucket} />
                  <Bar dataKey="count" name="Matches" fill={C.maroon} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>

          <ChartCard
            title={
              <div className="flex flex-wrap items-center justify-between gap-s4">
                <span className="font-display stretch-semi text-base font-bold">False-accept / false-reject vs. threshold</span>
                <div className="flex gap-s4 text-xs text-ink-600">
                  <span className="flex items-center gap-s2">
                    <span className="h-[3px] w-4 bg-maroon" />
                    False accept
                  </span>
                  <span className="flex items-center gap-s2">
                    <span className="h-[3px] w-4 bg-ink-600" />
                    False reject
                  </span>
                </div>
              </div>
            }
          >
            <Notice tone="caution" className="px-s3 py-s2 text-xs">
              <b>Preliminary estimate.</b> {farFrr.note} Based on {farFrr.same_person_count} same-person and{" "}
              {farFrr.cross_person_count} cross-person comparisons.
            </Notice>
            <div className="h-[190px]">
              <ResponsiveContainer>
                <LineChart data={farFrrChartData} margin={{ top: 14, right: 8, bottom: 0, left: -18 }}>
                  <CartesianGrid vertical={false} stroke={C.line} />
                  <XAxis dataKey="threshold" tick={TICK} tickLine={false} axisLine={{ stroke: C.line }} />
                  <YAxis unit="%" tick={TICK} tickLine={false} axisLine={false} />
                  <Tooltip {...TOOLTIP} cursor={{ stroke: C.line }} />
                  {thresholdTick != null && (
                    <ReferenceLine
                      x={thresholdTick}
                      stroke={C.prompt}
                      strokeDasharray="3 3"
                      label={{
                        value: `current ${threshold}`,
                        position: "insideTopRight",
                        fill: C.prompt,
                        fontFamily: "IBM Plex Mono",
                        fontSize: 11,
                        fontWeight: 600,
                      }}
                    />
                  )}
                  <Line type="monotone" dataKey="FAR" stroke={C.maroon} strokeWidth={2.5} dot={false} isAnimationActive={false} />
                  <Line type="monotone" dataKey="FRR" stroke={C.ink6} strokeWidth={2.5} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>
        </div>
      </div>
    </div>
  );
}
