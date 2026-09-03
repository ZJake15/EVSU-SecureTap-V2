import { useEffect, useRef, useState } from "react";
import apiClient from "../api/client";

const POLL_INTERVAL_MS = 1000;
const MAX_EVENTS_SHOWN = 500;

// A small fixed set of avatar background colors, picked deterministically
// from the person's name (same name -> same color every time) so cards
// stay visually stable across polls instead of flickering.
const AVATAR_COLORS = [
  "bg-red-500", "bg-orange-500", "bg-amber-500", "bg-emerald-500",
  "bg-teal-500", "bg-sky-500", "bg-indigo-500", "bg-purple-500", "bg-pink-500",
];

function startOfToday() {
  const date = new Date();
  date.setHours(0, 0, 0, 0);
  return date.toISOString();
}

function initials(name) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] || "";
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase();
}

function avatarColor(name) {
  const key = name || "";
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function UnknownFaceIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-8 w-8 text-red-600" aria-hidden="true">
      <circle cx="12" cy="8" r="3.2" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M4.5 20c1.2-3.6 4-5.4 7.5-5.4s6.3 1.8 7.5 5.4"
        stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
      />
      <text x="12" y="11" textAnchor="middle" fontSize="6.5" fill="currentColor" stroke="none">?</text>
    </svg>
  );
}

function EventPhoto({ event }) {
  if (event.status === "success") {
    if (event.person_photo) {
      return <img src={event.person_photo} alt="" className="aspect-square w-full object-cover" />;
    }
    return (
      <div className={`flex aspect-square w-full items-center justify-center text-lg font-bold text-white ${avatarColor(event.person_name)}`}>
        {initials(event.person_name)}
      </div>
    );
  }
  if (event.captured_photo) {
    return <img src={event.captured_photo} alt="" className="aspect-square w-full object-cover" />;
  }
  return (
    <div
      className={`flex aspect-square w-full items-center justify-center ${
        event.status === "occlusion_detected" ? "bg-teal-50" : "bg-red-50"
      }`}
    >
      <UnknownFaceIcon />
    </div>
  );
}

function MethodBadge({ event }) {
  if (event.status === "spoof_suspected") {
    return (
      <span className="inline-flex items-center rounded-full border border-purple-200 bg-purple-50 px-2 py-0.5 text-[10px] font-bold text-purple-800">
        Spoof suspected
      </span>
    );
  }
  if (event.status === "occlusion_detected") {
    return (
      <span className="inline-flex items-center rounded-full border border-teal-200 bg-teal-50 px-2 py-0.5 text-[10px] font-bold text-teal-800">
        Face covered
      </span>
    );
  }
  if (event.verification_method === "confusable_pair_tiebreak" && event.status !== "success") {
    // Unresolved - no card tap came in time, and (per EntryLog.person being
    // null on this row) nobody was auto-accepted either. Distinct from the
    // generic "Flagged" case below: this needs a specific manual review,
    // not just "didn't match".
    return (
      <span className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-800">
        Needs review - confusable pair
      </span>
    );
  }
  if (event.status !== "success") {
    return (
      <span className="inline-flex items-center rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-[10px] font-bold text-red-800">
        Flagged
      </span>
    );
  }
  if (event.verification_method === "confusable_pair_tiebreak") {
    return (
      <span className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-800">
        Confusable pair + NFC
      </span>
    );
  }
  if (event.verification_method === "face_and_card_tiebreak") {
    return (
      <span className="inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-800">
        Face + NFC
      </span>
    );
  }
  if (event.verification_method === "face_only") {
    return (
      <span className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-800">
        Face
      </span>
    );
  }
  // Historical rows from before the face-primary pivot (nfc_only,
  // nfc_and_face) - no longer created, but may still be visible if today's
  // feed happens to include one from right after a restart.
  return (
    <span className="inline-flex items-center rounded-full border border-ink-200 bg-ink-50 px-2 py-0.5 text-[10px] font-bold text-ink-600">
      NFC
    </span>
  );
}

function MetricCard({ label, value, tone = "default" }) {
  const toneClasses =
    tone === "danger"
      ? "border-red-200 bg-red-50"
      : tone === "accent"
        ? "border-gold-300 bg-gold-50"
        : tone === "teal"
          ? "border-teal-200 bg-teal-50"
          : "border-ink-900/10 bg-white";
  const valueClasses = tone === "danger" ? "text-red-700" : tone === "teal" ? "text-teal-700" : "text-ink-900";
  return (
    <div className={`rounded-xl border p-4 shadow-sm ${toneClasses}`}>
      <p className="text-xs font-semibold uppercase tracking-wide text-ink-500">{label}</p>
      <p className={`mt-1 font-display text-2xl font-semibold ${valueClasses}`}>{value}</p>
    </div>
  );
}

export default function LiveMonitoring() {
  const [events, setEvents] = useState([]);
  const [error, setError] = useState("");
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
      } catch {
        if (!isCancelled) setError("Live feed unavailable - retrying...");
      }
    };

    poll();
    const intervalId = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      isCancelled = true;
      clearInterval(intervalId);
    };
  }, []);

  const enrolledCount = events.filter((event) => event.status === "success").length;
  const spoofCount = events.filter((event) => event.status === "spoof_suspected").length;
  const unknownCount = events.filter((event) => event.status === "failed").length;
  const occludedCount = events.filter((event) => event.status === "occlusion_detected").length;
  const confidences = events
    .map((event) => event.match_confidence)
    .filter((value) => value !== null && value !== undefined);
  const avgConfidence = confidences.length
    ? Math.round((confidences.reduce((sum, value) => sum + value, 0) / confidences.length) * 100)
    : null;

  return (
    <div>
      <div className="mb-6">
        <h1 className="font-display text-2xl font-semibold text-ink-900">Live Monitoring</h1>
        <p className="mt-1 text-sm text-ink-500">Today&rsquo;s gate activity, updated in real time.</p>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <MetricCard label="Passes today" value={events.length} />
        <MetricCard label="Enrolled matches" value={enrolledCount} />
        <MetricCard label="Unknown attempts" value={unknownCount} tone="danger" />
        <MetricCard label="Spoof suspected" value={spoofCount} tone="danger" />
        <MetricCard label="Occlusion detected" value={occludedCount} tone="teal" />
        <MetricCard label="Avg confidence" value={avgConfidence !== null ? `${avgConfidence}%` : "—"} tone="accent" />
      </div>

      {error && <p className="mb-3 text-sm font-medium text-amber-700">{error}</p>}
      {events.length === 0 && (
        <p className="text-sm text-ink-500">Waiting for the next scan...</p>
      )}
      <div className="grid grid-cols-3 gap-4 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8">
        {events.map((event) => (
          <div
            key={event.id}
            className="flex flex-col overflow-hidden rounded-xl border border-ink-900/10 bg-white shadow-sm"
          >
            <EventPhoto event={event} />
            <div className="flex flex-1 flex-col gap-0.5 border-t-2 border-gold-400/70 p-2">
              <p className="truncate text-xs font-semibold text-ink-900">{event.person_name || "Unknown"}</p>
              <p className="truncate text-[10px] text-ink-500">
                {event.student_or_employee_id || "No ID on file"}
              </p>
              <p className="truncate text-[10px] text-ink-400">
                {new Date(event.timestamp).toLocaleTimeString()}
              </p>
              <div className="mt-1 flex items-center gap-1">
                <MethodBadge event={event} />
                {event.status === "success" && event.match_confidence != null && (
                  <span className="text-[10px] font-medium text-ink-500">
                    {Math.round(event.match_confidence * 100)}%
                  </span>
                )}
              </div>
              {/* A moment of occlusion seen earlier in this same encounter,
                  even though it resolved into this row's own outcome - the
                  point of keeping the note rather than dropping it once
                  resolved (see EntryLog.occlusion_detected). Not shown on an
                  occlusion_detected row itself - its own badge already says so. */}
              {event.occlusion_detected && event.status !== "occlusion_detected" && (
                <p className="truncate text-[10px] text-teal-700">Face briefly covered earlier</p>
              )}
              {/* A manual backstop for a confusable pair (see users.models.
                  ConfusablePair) - shown specifically on a confusable-pair
                  tiebreak, the one moment a guard actually needs it to
                  double-check by eye. Never used by matching itself. */}
              {event.verification_method === "confusable_pair_tiebreak" && event.distinguishing_note && (
                <p className="truncate text-[10px] font-medium text-amber-700" title={event.distinguishing_note}>
                  Note: {event.distinguishing_note}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
