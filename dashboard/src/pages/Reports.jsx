import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  Pie,
  PieChart,
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
  maroonDeep: "#4A0A0C",
  brass: "#C89B3C",
  caution: "#9A5B00",
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

// Every outcome the gates log - entries by how they were confirmed, then each
// kind of refusal - so the bar adds up to everything (reports/views.py).
// Eight clearly different hues, in this order: every two neighbouring
// slices of the donut stay apart for color-blind readers (OKLab ΔE ≥ 9.2)
// and everyone else (≥ 20.8) - checked with the dataviz palette validator.
// Each one is also named with its count and share in the list beside it.
const METHODS = [
  { key: "face", label: "Face", color: "#2a78d6" }, // blue
  { key: "face_nfc", label: "Face + card", color: "#1baf7a" }, // aqua
  { key: "card", label: "Card", color: "#eb6834" }, // orange
  { key: "manual", label: "Manual override", color: "#6250d6" }, // violet
  { key: "failed", label: "Unknown", color: "#e34948" }, // red
  { key: "spoof", label: "Spoof suspected", color: "#eda100" }, // yellow
  { key: "occluded", label: "Face covered", color: "#008300" }, // green
  { key: "not_enrolled", label: "Not enrolled (refused)", color: "#e87ba4" }, // magenta
];

function percent(n, sum) {
  if (!sum || !n) return "0%";
  const value = (n / sum) * 100;
  return value < 1 ? "<1%" : `${Math.round(value)}%`;
}

// Entries by method as a flat donut (every slice true to size - a 3D pie
// would enlarge the slices at the front) with the total in the middle, and
// beside it every method with its count and share, so even a sliver is read
// from the list rather than guessed from the ring.
function MethodDonut({ totals, sum }) {
  const slices = totals.filter((m) => m.n > 0);
  return (
    <div className="flex flex-wrap items-center gap-s5">
      <div className="relative h-[176px] w-[176px] flex-none">
        {sum > 0 ? (
          <ResponsiveContainer>
            <PieChart>
              <Pie
                data={slices}
                dataKey="n"
                nameKey="label"
                innerRadius={56}
                outerRadius={84}
                startAngle={90}
                endAngle={-270}
                stroke="#FFFFFF"
                strokeWidth={2}
                isAnimationActive={false}
              >
                {slices.map((m) => (
                  <Cell key={m.key} fill={m.color} />
                ))}
              </Pie>
              <Tooltip {...TOOLTIP} formatter={(value, name) => [`${value} · ${percent(value, sum)}`, name]} />
            </PieChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full w-full rounded-full border-[28px] border-canvas" />
        )}
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-display stretch-semi text-[28px] font-extrabold leading-none text-ink">{sum}</span>
          <span className="text-xs text-ink-600">logged</span>
        </div>
      </div>
      <ul className="flex min-w-[220px] flex-1 flex-col gap-s1 text-sm">
        {totals.map((m) => (
          <li key={m.key} className={`grid grid-cols-[12px_minmax(0,1fr)_40px_40px] items-center gap-s2 ${m.n ? "" : "text-ink-400"}`}>
            <span className="h-[12px] w-[12px] rounded-sm" style={{ background: m.n ? m.color : C.line }} />
            <span className="truncate">{m.label}</span>
            <span className={`text-right font-mono font-semibold ${m.n ? "text-ink" : ""}`}>{m.n}</span>
            <span className="text-right font-mono text-xs">{percent(m.n, sum)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Successful entries by the school or office of whoever entered (reports/views.py
// _entries_by_unit), students and staff apart, most first.
function UnitBreakdown({ rows, rangeLabel }) {
  const [showAll, setShowAll] = useState(false);
  const max = Math.max(1, ...rows.map((row) => row.total));
  const shown = showAll ? rows : rows.slice(0, 8);
  return (
    <div className="card mt-s6 flex flex-col gap-s4 p-s5">
      <div className="flex flex-wrap items-baseline justify-between gap-s3">
        <span className="t-section">Entries by school or office</span>
        <span className="flex items-center gap-s4 text-xs text-ink-600">
          <span className="flex items-center gap-s2">
            <span className="h-[10px] w-[10px]" style={{ background: C.maroon }} /> Students
          </span>
          <span className="flex items-center gap-s2">
            <span className="h-[10px] w-[10px]" style={{ background: C.brass }} /> Staff
          </span>
          <span>{rangeLabel.toLowerCase()}</span>
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-600">No entries in this period.</p>
      ) : (
        <div className="flex flex-col gap-s2">
          {shown.map((row) => (
            <div key={row.unit} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_48px] items-center gap-s3 text-sm">
              <span className={`truncate ${row.unit === "Not set" ? "italic text-ink-600" : ""}`} title={row.unit}>
                {row.unit}
              </span>
              <span className="flex h-3 bg-canvas" title={`${row.students} students, ${row.staff} staff`}>
                <span style={{ width: `${(row.students / max) * 100}%`, background: C.maroon }} />
                <span style={{ width: `${(row.staff / max) * 100}%`, background: C.brass }} />
              </span>
              <span className="text-right font-mono font-semibold">{row.total}</span>
            </div>
          ))}
          {rows.length > 8 && (
            <button type="button" className="link-action self-start" onClick={() => setShowAll((v) => !v)}>
              {showAll ? "Show fewer" : `Show all ${rows.length}`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function exportMethodCsv(entriesByMethod) {
  const header = ["Date", ...METHODS.map((method) => method.label)];
  const rows = entriesByMethod.map((row) => [row.date, ...METHODS.map((method) => row[method.key] ?? 0)]);
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

// Entries against exits (reports/views.py _in_out): blue in, orange out - a
// pair the dataviz palette validator passes for color-blind readers (ΔE 24.7)
// and everyone else (33.6), and always named in the legend beside them.
const IN_OUT = [
  { key: "entries", label: "Entries", color: "#2a78d6" },
  { key: "exits", label: "Exits", color: "#eb6834" },
];

// "8h 02m" from minutes.
function duration(minutes) {
  if (minutes == null) return "—";
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours}h ${String(minutes % 60).padStart(2, "0")}m` : `${minutes}m`;
}

function InOutStat({ label, value, tone = "" }) {
  return (
    <div className="flex flex-col gap-s1 border-l border-line px-s4">
      <span className={`t-eyebrow ${tone}`}>{label}</span>
      <span className={`font-mono text-[22px] font-semibold leading-tight ${tone}`}>{value}</span>
    </div>
  );
}

// People coming in and going out over the range - only meaningful once a gate
// has an exit camera (chosen in its gate monitor), so it says so when none has.
function InOutSection({ inOut, rangeLabel }) {
  if (!inOut) return null;
  const hours = inOut.by_hour.map((row) => ({ ...row, h: Number(row.hour.slice(0, 2)) }));
  const flags = inOut.pattern_flags || {};
  return (
    <div className="mt-s6 flex flex-col gap-s4">
      <div className="flex flex-wrap items-baseline justify-between gap-s3">
        <span className="t-section">Entries and exits &middot; {rangeLabel.toLowerCase()}</span>
        {inOut.exits === 0 && (
          <span className="text-sm text-ink-600">
            No exits recorded yet - a gate records them once its gate monitor has an exit camera (Cameras &amp; card
            readers).
          </span>
        )}
      </div>
      <div className="flex flex-wrap gap-y-s3">
        <InOutStat label="Entries" value={inOut.entries} />
        <InOutStat label="Exits" value={inOut.exits} />
        <InOutStat label="Average stay" value={duration(inOut.average_stay_minutes)} />
        <InOutStat label="Median stay" value={duration(inOut.median_stay_minutes)} />
        <InOutStat label="Busiest exit hour" value={hourLabel(inOut.busiest_exit_hour)} />
        <InOutStat label="Left, no entry" value={flags.no_entry || 0} tone={flags.no_entry ? "text-caution" : ""} />
        <InOutStat label="In again, no exit" value={flags.no_exit || 0} tone={flags.no_exit ? "text-caution" : ""} />
        <InOutStat label="Unknown at exits" value={inOut.exit_unknown} tone={inOut.exit_unknown ? "text-caution" : ""} />
        <InOutStat label="Spoof at exits" value={inOut.exit_spoof} tone={inOut.exit_spoof ? "text-danger" : ""} />
      </div>
      <ChartCard
        title={
          <div className="flex flex-wrap items-center justify-between gap-s4">
            <span className="font-display stretch-semi text-base font-bold">In and out by hour of the day</span>
            <div className="flex gap-s4 text-xs text-ink-600">
              {IN_OUT.map((series) => (
                <span key={series.key} className="flex items-center gap-s2">
                  <span className="h-3 w-3 rounded-[2px]" style={{ background: series.color }} />
                  {series.label}
                </span>
              ))}
            </div>
          </div>
        }
      >
        <div className="h-[230px]">
          <ResponsiveContainer>
            <BarChart data={hours} margin={{ top: 4, right: 0, bottom: 0, left: -24 }} barCategoryGap={3} barGap={2}>
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
              {IN_OUT.map((series) => (
                <Bar
                  key={series.key}
                  dataKey={series.key}
                  name={series.label}
                  fill={series.color}
                  radius={[2, 2, 0, 0]}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>
    </div>
  );
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
            <span className="t-eyebrow">Exits today</span>
            <span className="t-stat">{summary.exits_today ?? 0}</span>
          </div>
          <div className="flex flex-col gap-s2 border-l border-line px-s5 pb-0.5">
            <span className={`t-eyebrow ${summary.failed_today > 0 ? "text-danger" : ""}`}>Unknown today</span>
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
          <MethodDonut totals={methodTotals} sum={methodSum} />
        </div>
      </div>

      <UnitBreakdown rows={summary.entries_by_unit || []} rangeLabel={rangeLabel} />

      <InOutSection inOut={summary.in_out} rangeLabel={rangeLabel} />

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
