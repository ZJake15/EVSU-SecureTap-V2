import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import apiClient from "../api/client";
import logo from "../assets/logo.png";
import { Icon, Notice } from "../components/ui";

// Reports > "Print daily summary": one day on paper for the security
// office's file - the totals, each gate, entries and exits by hour, every
// security event (with who let someone in by hand, and why), and the guards
// on duty. Backend: reports/views.py DailySummaryView. The date picker and
// buttons aren't printed; the rest is laid out for A4.

function localToday() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

const time = (value) =>
  value ? new Date(value).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" }) : "—";

const TOTALS = [
  ["entries", "Entries"],
  ["exits", "Exits"],
  ["unknown", "Unknown"],
  ["spoof", "Fake face"],
  ["manual", "By hand"],
  ["refused_not_enrolled", "Refused"],
];

const MORE = [
  ["not_enrolled_let_in", "Not enrolled, let in"],
  ["covered", "Covered faces"],
  ["odd_pattern", "Odd entry/exit pattern"],
  ["after_hours", "After hours"],
  ["unattended", "Nobody on duty"],
];

const EVENT_TONE = {
  "Fake face suspected": "text-danger",
  "Refused - not enrolled": "text-danger",
  "Unknown person (seen again)": "text-caution",
  "Unknown person": "text-caution",
};

function Section({ title, children }) {
  return (
    <section className="flex flex-col gap-s2 break-inside-avoid">
      <h2 className="t-eyebrow border-b border-line pb-s1 text-maroon">{title}</h2>
      {children}
    </section>
  );
}

export default function DailySummary() {
  const [params, setParams] = useSearchParams();
  const date = params.get("date") || localToday();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setData(null);
    setError("");
    apiClient
      .get("/reports/daily", { params: { date } })
      .then(({ data: summary }) => alive && setData(summary))
      .catch(() => alive && setError("Couldn't load the summary - try again."));
    return () => {
      alive = false;
    };
  }, [date]);

  const day = new Date(`${date}T00:00:00`).toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
  const hours = (data?.by_hour || []).filter((row) => row.entries || row.exits);

  return (
    <div className="flex flex-col gap-s5">
      <div className="flex flex-wrap items-end justify-between gap-s4 print:hidden">
        <div className="flex flex-col gap-s2">
          <Link to="/reports" className="link-action inline-flex items-center gap-s1">
            <Icon name="arrow-left" size={14} /> Reports
          </Link>
          <h1 className="t-title">Daily summary</h1>
        </div>
        <div className="flex items-end gap-s3">
          <label className="flex flex-col gap-s1">
            <span className="t-eyebrow">Day</span>
            <input
              type="date"
              className="input w-44"
              value={date}
              max={localToday()}
              onChange={(event) => event.target.value && setParams({ date: event.target.value })}
            />
          </label>
          <button type="button" className="btn-primary" onClick={() => window.print()} disabled={!data}>
            <Icon name="printer" size={16} /> Print
          </button>
        </div>
      </div>

      {error && <Notice className="print:hidden">{error}</Notice>}
      {!data && !error && <p className="text-sm text-ink-600 print:hidden">Loading the summary…</p>}

      {data && (
        <article className="card mx-auto flex w-full max-w-[210mm] flex-col gap-s5 p-s6 print:max-w-none print:border-0 print:p-0">
          <header className="flex items-center gap-s4 border-b-2 border-maroon pb-s3">
            <img src={logo} alt="" className="h-12 w-12" />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="t-eyebrow">EVSU SecureTap · Daily gate summary</span>
              <span className="font-display stretch-semi text-2xl font-bold text-ink">{day}</span>
            </div>
            <div className="text-right text-xs text-ink-600">
              Printed by {data.generated_by}
              <br />
              {new Date(data.generated_at).toLocaleString("en-US", {
                month: "short",
                day: "numeric",
                hour: "numeric",
                minute: "2-digit",
              })}
            </div>
          </header>

          <div className="grid grid-cols-3 gap-s3 sm:grid-cols-6">
            {TOTALS.map(([key, label]) => (
              <div key={key} className="flex flex-col gap-s1 rounded-sm border border-line px-s3 py-s2">
                <span className="t-eyebrow">{label}</span>
                <span className="font-display stretch-semi text-2xl font-bold text-ink">{data.totals[key]}</span>
              </div>
            ))}
          </div>
          <p className="-mt-s3 text-xs text-ink-600">
            {MORE.map(([key, label]) => `${label}: ${data.totals[key]}`).join(" · ")}
          </p>

          <Section title="Each gate">
            {data.gates.length === 0 ? (
              <p className="text-sm text-ink-600">No gate recorded anything this day.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="table-head text-left">
                    <th className="py-s1">Gate</th>
                    <th className="text-right">Entries</th>
                    <th className="text-right">Exits</th>
                    <th className="text-right">Unknown</th>
                    <th className="text-right">Fake face</th>
                    <th className="text-right">By hand</th>
                    <th className="text-right">Nobody on duty</th>
                  </tr>
                </thead>
                <tbody>
                  {data.gates.map((gate) => (
                    <tr key={gate.gate} className="border-t border-line">
                      <td className="py-s1 font-bold">{gate.gate}</td>
                      <td className="text-right font-mono">{gate.entries}</td>
                      <td className="text-right font-mono">{gate.exits}</td>
                      <td className="text-right font-mono">{gate.unknown}</td>
                      <td className="text-right font-mono">{gate.spoof}</td>
                      <td className="text-right font-mono">{gate.manual}</td>
                      <td className="text-right font-mono">{gate.unattended}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>

          <Section title="By hour">
            {hours.length === 0 ? (
              <p className="text-sm text-ink-600">Nobody was let through this day.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="table-head text-left">
                    <th className="py-s1">Hour</th>
                    <th className="text-right">Entries</th>
                    <th className="text-right">Exits</th>
                  </tr>
                </thead>
                <tbody>
                  {hours.map((row) => (
                    <tr key={row.hour} className="border-t border-line">
                      <td className="py-[3px] font-mono">{row.hour}</td>
                      <td className="text-right font-mono">{row.entries}</td>
                      <td className="text-right font-mono">{row.exits}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>

          <Section title={`Security events (${data.events_total})`}>
            {data.events.length === 0 ? (
              <p className="text-sm text-ink-600">None - no fake faces, unknown people, refusals or manual entries.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="table-head text-left">
                    <th className="py-s1">Time</th>
                    <th>Gate</th>
                    <th>What</th>
                    <th>Who</th>
                    <th>Details</th>
                  </tr>
                </thead>
                <tbody>
                  {data.events.map((event, index) => (
                    <tr key={index} className="border-t border-line align-top">
                      <td className="whitespace-nowrap py-[3px] pr-s2 font-mono">{time(event.time)}</td>
                      <td className="pr-s2">
                        {event.gate}
                        {event.direction === "exit" ? " · exit" : ""}
                      </td>
                      <td className={`pr-s2 font-bold ${EVENT_TONE[event.kind] || "text-ink"}`}>{event.kind}</td>
                      <td className="pr-s2">
                        {event.who || "—"}
                        {event.id_number && <span className="text-ink-600"> ({event.id_number})</span>}
                      </td>
                      <td className="text-ink-600">{event.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {data.events_total > data.events.length && (
              <p className="text-xs text-ink-600">
                The first {data.events.length} are listed - see Logs for all {data.events_total}.
              </p>
            )}
          </Section>

          <Section title="Guards on duty">
            {data.shifts.length === 0 ? (
              <p className="text-sm text-ink-600">Nobody signed in at a gate monitor this day.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="table-head text-left">
                    <th className="py-s1">Gate</th>
                    <th>Guard</th>
                    <th>From</th>
                    <th>To</th>
                    <th>Signed in with</th>
                  </tr>
                </thead>
                <tbody>
                  {data.shifts.map((shift, index) => (
                    <tr key={index} className="border-t border-line">
                      <td className="py-[3px] pr-s2">{shift.gate}</td>
                      <td className="pr-s2 font-bold">{shift.name}</td>
                      <td className="pr-s2 font-mono">{time(shift.from)}</td>
                      <td className="pr-s2 font-mono">
                        {shift.to ? time(shift.to) : "still on duty"}
                        {shift.ended && <span className="font-sans text-ink-600"> ({shift.ended.toLowerCase()})</span>}
                      </td>
                      <td>{shift.method}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Section>

          <footer className="mt-s4 grid grid-cols-2 gap-s6 pt-s6 text-sm">
            <div className="border-t border-ink pt-s1 text-ink-600">Checked by (name and signature)</div>
            <div className="border-t border-ink pt-s1 text-ink-600">Date</div>
          </footer>
        </article>
      )}
    </div>
  );
}
