import { useEffect, useRef, useState } from "react";
import apiClient from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Avatar, Icon, PageHeader } from "../components/ui";

const POLL_INTERVAL_MS = 1000;
const MAX_EVENTS_SHOWN = 500;

const TABLE_COLS = "36px minmax(0,2.4fr) 110px 110px minmax(0,1.5fr) 56px minmax(0,84px)";

function startOfToday() {
  const date = new Date();
  date.setHours(0, 0, 0, 0);
  return date.toISOString();
}

function clockTime(value) {
  return new Date(value).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function todayLabel() {
  return new Date()
    .toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })
    .toUpperCase();
}

function pct(value) {
  return value != null ? `${Math.round(value * 100)}%` : "—";
}

// How a successful pass was verified - icon + word, verified green (a manual
// override is ink: a person vouched for it, not the scanner).
function passMethod(event) {
  switch (event.verification_method) {
    case "face_and_card_tiebreak":
      return { label: "Face + card", icon: "identification-card", className: "text-verified" };
    case "confusable_pair_tiebreak":
      return { label: "Lookalike + card", icon: "users-three", className: "text-verified" };
    case "manual_override":
      return { label: "Manual", icon: "hand-pointing", className: "text-ink" };
    case "face_only":
      return { label: "Face", icon: "user-focus", className: "text-verified" };
    default:
      // Historical rows from before the face-primary pivot (nfc_only,
      // nfc_and_face) - no longer created, but may still turn up in today's
      // feed right after a restart.
      return { label: "Card", icon: "identification-card", className: "text-verified" };
  }
}

// Everything that isn't a clean pass gets pinned to "Needs attention", with
// its own word, icon and status color.
function attentionKind(event) {
  if (event.status === "spoof_suspected") {
    return { word: "Spoof suspected", icon: "warning-octagon", color: "#C62828", className: "text-danger" };
  }
  if (event.status === "occlusion_detected") {
    return { word: "Face covered", icon: "hand-palm", color: "#1D5FA8", className: "text-prompt" };
  }
  if (event.verification_method === "confusable_pair_tiebreak") {
    // Unresolved - no card tap came in time, and (per EntryLog.person being
    // null on this row) nobody was auto-accepted either. Needs a specific
    // manual review, not just "didn't match".
    return { word: "Needs review", icon: "users-three", color: "#9A5B00", className: "text-caution" };
  }
  return { word: "Flagged · Unknown", icon: "user-circle-dashed", color: "#9A5B00", className: "text-caution" };
}

function SubStat({ label, icon, value, tone }) {
  // Only colored once there's something to see - a zero stays ink.
  const active = tone && Number(value) > 0;
  return (
    <div className="flex flex-col gap-s2 border-l border-line px-s5 pb-0.5">
      <span className={`t-eyebrow flex items-center gap-s1 ${active ? tone : ""}`}>
        <Icon name={icon} bold size={14} />
        {label}
      </span>
      <span className={`t-stat ${active ? tone : "text-ink"}`}>{value}</span>
    </div>
  );
}

function LatestPass({ event }) {
  const method = passMethod(event);
  return (
    <div className="card flex flex-none items-stretch gap-s5 p-s4">
      <Avatar src={event.person_photo} name={event.person_name} size={120} />
      <div className="flex min-w-0 flex-1 flex-col justify-center gap-s2">
        <span className="t-eyebrow">
          Latest &middot; {event.gate_location || "Gate"} &middot; {event.direction}
        </span>
        <span className="truncate text-[28px] font-bold leading-tight">{event.person_name}</span>
        <div className="flex flex-wrap items-center gap-s4 text-sm">
          <span className="font-mono font-medium">{event.student_or_employee_id}</span>
          <span className="font-mono font-medium text-ink-600">{clockTime(event.timestamp)}</span>
          <span className={`flex items-center gap-s1 font-bold ${method.className}`}>
            <Icon name={method.icon} bold size={16} />
            {method.label}
          </span>
        </div>
      </div>
      {event.match_confidence != null && (
        <div className="hidden flex-none flex-col items-end justify-center gap-s1 border-l border-line pl-s5 pr-s3 sm:flex">
          <span className="t-eyebrow">Confidence</span>
          <span className="font-mono text-[44px] font-semibold leading-none">{pct(event.match_confidence)}</span>
        </div>
      )}
    </div>
  );
}

function PassRow({ event }) {
  const method = passMethod(event);
  // A manual backstop for a confusable pair (see users.models.ConfusablePair)
  // - shown on a confusable-pair tiebreak, the one moment a guard actually
  // needs it to double-check by eye. Never used by matching itself.
  const note =
    event.verification_method === "confusable_pair_tiebreak" && event.distinguishing_note
      ? event.distinguishing_note
      : null;
  return (
    <div
      className="grid min-h-10 items-center gap-s3 border-b border-line px-s4 py-s1 text-sm"
      style={{ gridTemplateColumns: TABLE_COLS }}
    >
      <Avatar src={event.person_photo} name={event.person_name} size={28} />
      <span className="flex min-w-0 flex-col">
        <span className="truncate font-bold">{event.person_name}</span>
        {note && (
          <span className="flex items-center gap-s1 truncate text-xs text-ink-600" title={note}>
            <Icon name="note" size={12} />
            {note}
          </span>
        )}
        {/* A moment of occlusion seen earlier in this same encounter, even
            though it resolved into this pass (see EntryLog.occlusion_detected). */}
        {event.occlusion_detected && (
          <span className="flex items-center gap-s1 text-xs text-prompt">
            <Icon name="hand-palm" size={12} />
            Face briefly covered earlier
          </span>
        )}
      </span>
      <span className="truncate font-mono text-[13px] font-medium">{event.student_or_employee_id}</span>
      <span className="font-mono text-[13px] font-medium text-ink-600">{clockTime(event.timestamp)}</span>
      <span className={`flex items-center gap-s1 whitespace-nowrap font-bold ${method.className}`}>
        <Icon name={method.icon} bold size={16} />
        {method.label}
      </span>
      <span className="text-right font-mono font-medium">{pct(event.match_confidence)}</span>
      <span className="truncate text-ink-600">{event.gate_location}</span>
    </div>
  );
}

function AttentionCard({ event }) {
  const kind = attentionKind(event);
  const isLookalike = event.status !== "spoof_suspected" && event.verification_method === "confusable_pair_tiebreak";
  const meta = [
    event.student_or_employee_id || "No ID on file",
    isLookalike ? "Lookalike unresolved" : event.failure_reason,
    event.gate_location,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div
      className="flex flex-none gap-s4 rounded-l-sm rounded-r-md border border-l-[3px] border-line bg-surface p-s4"
      style={{ borderLeftColor: kind.color }}
    >
      {event.captured_photo ? (
        <img src={event.captured_photo} alt="" className="h-14 w-14 flex-none rounded-sm object-cover" />
      ) : (
        <span className="flex h-14 w-14 flex-none items-center justify-center rounded-sm bg-ink text-ink-400">
          <Icon name={kind.icon} size={28} />
        </span>
      )}
      <div className="flex min-w-0 flex-1 flex-col gap-s1">
        <div className="flex items-center justify-between gap-s3">
          <span
            className={`flex items-center gap-s1 font-display stretch-condensed text-sm font-extrabold uppercase tracking-[0.06em] ${kind.className}`}
          >
            <Icon name={kind.icon} bold size={16} />
            {kind.word}
          </span>
          <span className="font-mono text-xs font-medium text-ink-600">{clockTime(event.timestamp)}</span>
        </div>
        <span className="text-base font-bold leading-tight">{event.person_name || "Unknown"}</span>
        <span className="text-xs text-ink-600">{meta}</span>
        {isLookalike && event.distinguishing_note && (
          <span className="mt-s1 rounded-sm bg-canvas px-s2 py-s1 text-xs text-ink">Note: {event.distinguishing_note}</span>
        )}
      </div>
    </div>
  );
}

export default function LiveMonitoring() {
  const { user } = useAuth();
  const isSecurityOfficer = user?.role === "security_officer";
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
  const [lastUpdate, setLastUpdate] = useState(null);
  const sinceRef = useRef(startOfToday());

  useEffect(() => {
    let isCancelled = false;

    const poll = async () => {
      try {
        const { data } = await apiClient.get("/logs/live", {
          params: { since: sinceRef.current },
        });
        if (isCancelled) return;
        if (data.results?.length) {
          setEvents((prev) => [...data.results, ...prev].slice(0, MAX_EVENTS_SHOWN));
          sinceRef.current = data.results[0].timestamp;
        }
        setError("");
        setLastUpdate(new Date());
      } catch {
        if (!isCancelled) setError("Live feed unavailable — retrying…");
      }
    };

    poll();
    const intervalId = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      isCancelled = true;
      clearInterval(intervalId);
    };
  }, []);

  const passes = events.filter((event) => event.status === "success");
  const attention = events.filter((event) => event.status !== "success");
  const unknownCount = events.filter((event) => event.status === "failed").length;
  const spoofCount = events.filter((event) => event.status === "spoof_suspected").length;
  const occludedCount = events.filter((event) => event.status === "occlusion_detected").length;
  const confidences = events
    .map((event) => event.match_confidence)
    .filter((value) => value !== null && value !== undefined);
  const avgConfidence = confidences.length
    ? `${Math.round((confidences.reduce((sum, value) => sum + value, 0) / confidences.length) * 100)}%`
    : "—";

  const gate = user?.gateLocation || "no gate assigned";
  const latest = passes[0];

  const attentionEmpty = (
    <span className="border-t border-line pt-s4 text-sm leading-normal text-ink-600">
      Nothing to review. Spoof attempts, unknown people, covered faces and unresolved lookalikes will be pinned
      here.
    </span>
  );

  return (
    <div className="flex flex-col">
      <PageHeader
        eyebrow={isSecurityOfficer ? "Your gate · today only" : `Today · ${todayLabel()} · All gates`}
        title={isSecurityOfficer ? `Live Monitoring · ${gate}` : "Live Monitoring"}
      >
        <div className="flex items-center gap-s3 pb-s1 text-sm text-ink-600">
          <span className={`h-2 w-2 ${error ? "bg-caution" : "bg-verified"}`} />
          <span>{error ? "Reconnecting · last update" : "Live · updates every second"}</span>
          {lastUpdate && <span className="font-mono font-medium text-ink">{clockTime(lastUpdate)}</span>}
        </div>
      </PageHeader>

      <div className="mt-s5 flex flex-none flex-wrap items-end gap-y-s4">
        <div className="flex flex-col gap-s1 pr-s6">
          <span className="t-eyebrow">Passes today</span>
          <span className="t-hero">{events.length}</span>
        </div>
        <SubStat label="Enrolled matches" icon="user-focus" value={passes.length} />
        <SubStat label="Unknown attempts" icon="user-circle-dashed" value={unknownCount} tone="text-caution" />
        <SubStat label="Spoof suspected" icon="warning-octagon" value={spoofCount} tone="text-danger" />
        <SubStat label="Face covered" icon="hand-palm" value={occludedCount} tone="text-prompt" />
        <SubStat label="Avg. confidence" icon="gauge" value={avgConfidence} />
      </div>

      {error && (
        <div className="notice mt-s5 flex-none items-center bg-caution-tint">
          <Icon name="arrows-clockwise" bold size={20} className="text-caution" />
          <span className="font-bold">{error}</span>
          {lastUpdate && (
            <span className="text-ink-600">
              Events shown are from {clockTime(lastUpdate)}. New scans will appear once the connection returns.
            </span>
          )}
        </div>
      )}

      {events.length === 0 ? (
        <div className="mt-s6 grid grid-cols-1 gap-s6 xl:grid-cols-[minmax(0,8fr)_minmax(0,4fr)]">
          <div className="card flex flex-col justify-center gap-s4 p-s7">
            <Icon name="hourglass-medium" size={44} className="text-ink-600" />
            <span className="font-display stretch-semi text-[28px] font-extrabold leading-tight">
              Waiting for the first scan of the day
            </span>
            <span className="max-w-[460px] text-base leading-normal text-ink-600">
              Entries appear here the moment a gate camera or card reader logs someone. This page updates every
              second.
            </span>
          </div>
          <div className="flex flex-col gap-s3">
            <span className="t-section">Needs attention</span>
            {attentionEmpty}
          </div>
        </div>
      ) : (
        <div className="mt-s6 grid grid-cols-1 gap-s6 xl:grid-cols-[minmax(0,8fr)_minmax(0,4fr)]">
          <div className="flex min-w-0 flex-col gap-s5">
            {latest && <LatestPass event={latest} />}
            <div className="card flex flex-col overflow-hidden">
              <div className="overflow-x-auto">
                <div className="min-w-[720px]">
                  <div
                    className="table-head grid h-9 items-center gap-s3 border-b border-line px-s4"
                    style={{ gridTemplateColumns: TABLE_COLS }}
                  >
                    <span />
                    <span>Name</span>
                    <span>ID</span>
                    <span>Time</span>
                    <span>Method</span>
                    <span className="text-right">Conf.</span>
                    <span>Gate</span>
                  </div>
                  <div className="max-h-[calc(100vh-420px)] min-h-[200px] overflow-y-auto">
                    {passes.map((event) => (
                      <PassRow key={event.id} event={event} />
                    ))}
                    {passes.length === 0 && (
                      <p className="px-s4 py-s5 text-sm text-ink-600">No successful passes yet today.</p>
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="flex min-w-0 flex-col gap-s3">
            <div className="flex flex-none items-baseline justify-between">
              <span className="t-section">Needs attention</span>
              <span className="font-mono text-sm font-medium text-ink-600">{attention.length} today</span>
            </div>
            {attention.length === 0 ? (
              attentionEmpty
            ) : (
              <div className="flex max-h-[calc(100vh-260px)] flex-col gap-s3 overflow-y-auto">
                {attention.map((event) => (
                  <AttentionCard key={event.id} event={event} />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
