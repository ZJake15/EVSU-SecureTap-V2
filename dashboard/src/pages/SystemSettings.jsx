import { useEffect, useState } from "react";
import apiClient from "../api/client";
import { Icon, Notice, PageHeader } from "../components/ui";

// Every key SystemSettingsView returns, with a short human label - grouped
// to match how they're actually used. Purely a display mapping; unknown keys
// from the API still render (see "Other" below) so this never silently hides
// a setting someone adds later and forgets to list here. `unit: "s"` marks a
// value in seconds.
const LEFT_GROUPS = [
  {
    title: "Face matching",
    keys: {
      face_match_similarity_threshold: { label: "Match threshold" },
      confusable_similarity_threshold: { label: "Lookalike similarity threshold" },
      tiebreak_margin: { label: "Tiebreak margin" },
      tiebreak_timeout_seconds: { label: "Tiebreak timeout", unit: "s" },
    },
  },
  {
    title: "Voting window",
    keys: {
      vote_window_size: { label: "Size (attempts)" },
      vote_required_agreement: { label: "Required agreement" },
      vote_window_seconds: { label: "Window", unit: "s" },
    },
  },
  {
    title: "Gate-scan quality",
    keys: {
      gate_scan_det_size: { label: "Detector size" },
      gate_scan_min_blur_variance: { label: "Minimum blur variance" },
      face_edge_margin_ratio: { label: "Edge margin" },
      face_max_yaw_ratio: { label: "Max yaw (turn-away)" },
    },
  },
];

const RIGHT_GROUPS = [
  {
    title: "Liveness",
    keys: {
      liveness_score_threshold: { label: "Score threshold" },
    },
  },
  {
    title: "Occlusion detection",
    keys: {
      face_min_mouth_visibility_ratio: { label: "Min mouth visibility" },
      face_max_mouth_texture_ratio: { label: "Mouth texture ratio" },
      face_min_det_score_unoccluded: { label: "Min detection score" },
    },
  },
  {
    title: "Cooldowns",
    keys: {
      recognition_cooldown_seconds: { label: "Recognition", unit: "s" },
      unenrolled_capture_cooldown_seconds: { label: "Unenrolled capture", unit: "s" },
      spoof_capture_cooldown_seconds: { label: "Spoof capture", unit: "s" },
      occlusion_capture_cooldown_seconds: { label: "Covered-face capture", unit: "s" },
    },
  },
  {
    title: "Enrollment",
    keys: {
      max_embeddings_per_person: { label: "Max photos" },
      max_embeddings_per_confusable_person: { label: "Max photos · lookalike pair" },
    },
  },
];

function formatValue(value, unit) {
  if (value === undefined || value === null) return "—";
  return unit ? `${value} ${unit}` : String(value);
}

function Group({ title, rows }) {
  return (
    <div className="flex flex-col">
      <span className="border-b-2 border-ink pb-s3 font-display stretch-semi text-base font-bold">{title}</span>
      {rows.map(({ key, label, value }) => (
        <div key={key} className="flex h-9 items-center justify-between gap-s4 border-b border-line text-sm">
          <span className="truncate text-ink-600" title={key}>
            {label}
          </span>
          <span className="font-mono font-semibold">{value}</span>
        </div>
      ))}
    </div>
  );
}

export default function SystemSettings() {
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiClient
      .get("/settings")
      .then(({ data }) => setSettings(data))
      .catch(() => setError("Could not load system settings. Try refreshing the page."));
  }, []);

  const groupRows = (group) =>
    Object.entries(group.keys).map(([key, { label, unit }]) => ({
      key,
      label,
      value: formatValue(settings[key], unit),
    }));

  const knownKeys = new Set([...LEFT_GROUPS, ...RIGHT_GROUPS].flatMap((group) => Object.keys(group.keys)));
  const otherRows = settings
    ? Object.entries(settings)
        .filter(([key]) => !knownKeys.has(key) && key !== "editable" && key !== "note")
        .map(([key, value]) => ({ key, label: key, value: formatValue(value) }))
    : [];

  return (
    <div className="flex flex-col">
      <PageHeader eyebrow="Server configuration" title="Settings" />

      {error && (
        <Notice tone="danger" className="mt-s5">
          {error}
        </Notice>
      )}

      {settings && (
        <>
          {settings.editable === false && (
            <div className="notice mt-s5 max-w-[760px] border border-line bg-surface leading-snug">
              <Icon name="lock-simple" bold size={20} />
              <span>
                <b>Read-only for now.</b> {settings.note}
              </span>
            </div>
          )}

          <div className="mt-s6 grid grid-cols-1 content-start gap-x-s7 gap-y-s6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
            <div className="flex flex-col gap-s6">
              {LEFT_GROUPS.map((group) => (
                <Group key={group.title} title={group.title} rows={groupRows(group)} />
              ))}
            </div>
            <div className="flex flex-col gap-s6">
              {RIGHT_GROUPS.map((group) => (
                <Group key={group.title} title={group.title} rows={groupRows(group)} />
              ))}
              {otherRows.length > 0 && <Group title="Other" rows={otherRows} />}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
