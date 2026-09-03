import { useEffect, useState } from "react";
import apiClient from "../api/client";

// Every key SystemSettingsView returns, with a short human label - grouped
// to match how they're actually used (face matching, liveness/anti-spoof,
// the voting/cooldown grace-period system, gate-scan quality gates). Purely
// a display mapping; unknown keys from the API still render (see the
// fallback loop below) so this never silently hides a setting someone adds
// later and forgets to list here.
const GROUPS = [
  {
    title: "Face matching",
    keys: {
      face_match_similarity_threshold: "Match similarity threshold",
      tiebreak_margin: "Tiebreak margin",
      tiebreak_timeout_seconds: "Tiebreak timeout (seconds)",
    },
  },
  {
    title: "Liveness (anti-spoofing)",
    keys: {
      liveness_score_threshold: "Liveness score threshold",
    },
  },
  {
    title: "Voting / confirmation window",
    keys: {
      vote_window_size: "Vote window size (attempts)",
      vote_required_agreement: "Required agreement",
      vote_window_seconds: "Vote window (seconds)",
    },
  },
  {
    title: "Gate-scan quality gates",
    keys: {
      gate_scan_det_size: "Detector input size",
      gate_scan_min_blur_variance: "Minimum blur variance",
      face_edge_margin_ratio: "Edge margin ratio",
      face_max_yaw_ratio: "Max yaw (turn-away) ratio",
      face_min_mouth_visibility_ratio: "Min mouth visibility ratio",
      face_max_mouth_texture_ratio: "Max mouth texture ratio",
      face_min_det_score_unoccluded: "Min detection score (unoccluded)",
    },
  },
  {
    title: "Cooldowns",
    keys: {
      recognition_cooldown_seconds: "Recognition cooldown (seconds)",
      unenrolled_capture_cooldown_seconds: "Unenrolled capture cooldown (seconds)",
      spoof_capture_cooldown_seconds: "Spoof capture cooldown (seconds)",
      occlusion_capture_cooldown_seconds: "Occlusion capture cooldown (seconds)",
    },
  },
];

export default function SystemSettings() {
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiClient
      .get("/settings")
      .then(({ data }) => setSettings(data))
      .catch(() => setError("Could not load system settings. Try refreshing the page."));
  }, []);

  const knownKeys = new Set(GROUPS.flatMap((group) => Object.keys(group.keys)));
  const otherEntries = settings
    ? Object.entries(settings).filter(([key]) => !knownKeys.has(key) && key !== "editable" && key !== "note")
    : [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-2xl font-semibold text-ink-900">System Settings</h1>
        <p className="mt-1 text-sm text-ink-500">
          The thresholds and configuration values the gate scan currently runs with.
        </p>
      </div>

      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}

      {settings && (
        <>
          {settings.editable === false && (
            <div className="rounded-xl border border-gold-500/30 bg-gold-500/5 p-4 text-sm text-ink-700">
              <p className="font-semibold text-ink-900">Read-only, for now</p>
              <p className="mt-1">{settings.note}</p>
            </div>
          )}

          {GROUPS.map((group) => (
            <div key={group.title} className="rounded-xl border border-ink-900/10 bg-white p-6 shadow-sm">
              <h2 className="font-display text-lg font-semibold text-ink-900">{group.title}</h2>
              <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
                {Object.entries(group.keys).map(([key, label]) => (
                  <div key={key} className="flex items-center justify-between border-b border-ink-900/5 pb-2">
                    <dt className="text-sm text-ink-600">{label}</dt>
                    <dd className="font-mono text-sm font-semibold text-ink-900">
                      {settings[key] !== undefined ? String(settings[key]) : "—"}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          ))}

          {otherEntries.length > 0 && (
            <div className="rounded-xl border border-ink-900/10 bg-white p-6 shadow-sm">
              <h2 className="font-display text-lg font-semibold text-ink-900">Other</h2>
              <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
                {otherEntries.map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between border-b border-ink-900/5 pb-2">
                    <dt className="text-sm text-ink-600">{key}</dt>
                    <dd className="font-mono text-sm font-semibold text-ink-900">{String(value)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </>
      )}
    </div>
  );
}
