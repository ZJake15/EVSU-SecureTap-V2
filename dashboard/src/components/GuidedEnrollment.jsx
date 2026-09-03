import { useEffect, useState } from "react";
import apiClient from "../api/client";
import { PHOTO_ACCEPT, photoFormatError, previewUrlFor, readPhotoInput } from "../lib/photoUpload";
import WebcamCapture from "./WebcamCapture";

// Near-frontal variation only - NOT wide angles or true side-profile shots.
// ArcFace (like the dlib model before it) matches best on near-frontal
// faces; a true 90-degree profile often won't even detect.
const SLOTS = [
  { key: "front", label: "Front", hint: "Straight on" },
  { key: "left", label: "Slight left", hint: "Turn left a little" },
  { key: "right", label: "Slight right", hint: "Turn right a little" },
  { key: "neutral", label: "Neutral", hint: "No expression" },
  { key: "smile", label: "Smile", hint: "Natural smile" },
];

const emptySlotState = () =>
  Object.fromEntries(SLOTS.map((slot) => [slot.key, { status: "empty", file: null, previewUrl: null, reason: null }]));

function CameraIcon(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
      <path
        d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2.13a1 1 0 0 0 .87-.5l.6-1A1.5 1.5 0 0 1 10.4 5h3.2a1.5 1.5 0 0 1 1.3.75l.6 1a1 1 0 0 0 .87.5H18.5A1.5 1.5 0 0 1 20 8.5v8A1.5 1.5 0 0 1 18.5 18h-13A1.5 1.5 0 0 1 4 16.5v-8Z"
        stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"
      />
      <circle cx="12" cy="12.5" r="3" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function CheckIcon(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
      <path d="M5 12.5l4.5 4.5L19 7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function WarningIcon(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
      <path d="M12 4.5 21 19H3L12 4.5Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M12 10v3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="12" cy="16.2" r="0.9" fill="currentColor" />
    </svg>
  );
}

// Centralizes every visual decision per tile-state so the tile markup below
// just reads a config object, rather than a wall of conditional classNames.
function slotVisuals(status, isActive) {
  switch (status) {
    case "ok":
      return {
        tile: "border-gold-500 bg-white",
        badge: "border-gold-500 bg-gold-500 text-white",
        label: "text-ink-900",
        caption: { text: "Passed", className: "text-emerald-700" },
      };
    case "error":
      return {
        tile: "border-red-400 bg-red-50/60",
        badge: "border-red-400 bg-red-400 text-white",
        label: "text-ink-900",
        caption: null, // reason is shown separately, wrapped, below the tile
      };
    case "checking":
      return {
        tile: "border-gold-400 bg-gold-50",
        badge: "border-gold-400 bg-gold-400 text-white",
        label: "text-ink-900",
        caption: { text: "Checking...", className: "text-gold-700" },
      };
    default:
      return isActive
        ? {
            tile: "border-gold-400 border-solid bg-gold-50/60",
            badge: "border-gold-400 text-gold-700",
            label: "text-ink-900",
            caption: { text: "Up next", className: "text-gold-700" },
          }
        : {
            tile: "border-ink-200 border-dashed bg-parchment-100",
            badge: "border-ink-300 text-ink-400",
            label: "text-ink-400",
            caption: null,
          };
  }
}

function SlotBadge({ index, status, className }) {
  return (
    <span
      className={`absolute -left-2 -top-2 flex h-6 w-6 items-center justify-center rounded-full border-2 text-[11px] font-bold ${className}`}
    >
      {status === "ok" ? <CheckIcon className="h-3.5 w-3.5" /> : status === "error" ? <WarningIcon className="h-3.5 w-3.5" /> : index}
    </span>
  );
}

export default function GuidedEnrollment({ onChange, disabled }) {
  const [mode, setMode] = useState("guided"); // "guided" | "fallback"
  const [slots, setSlots] = useState(emptySlotState);
  const [fallback, setFallback] = useState({ file: null, previewUrl: null });
  const [fallbackError, setFallbackError] = useState("");

  const activeSlotKey = SLOTS.find((slot) => slots[slot.key].status !== "ok")?.key || null;
  const allSlotsOk = SLOTS.every((slot) => slots[slot.key].status === "ok");
  const passedCount = SLOTS.filter((slot) => slots[slot.key].status === "ok").length;

  useEffect(() => {
    if (mode === "guided") {
      onChange({
        mode,
        isReady: allSlotsOk,
        primaryPhoto: slots.front.status === "ok" ? slots.front.file : null,
        extraPhotos: SLOTS.filter((s) => s.key !== "front").map((s) => slots[s.key].file).filter(Boolean),
      });
    } else {
      onChange({ mode, isReady: !!fallback.file, primaryPhoto: fallback.file, extraPhotos: [] });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, slots, fallback]);

  const checkQuality = async (file) => {
    const payload = new FormData();
    payload.append("photo", file);
    try {
      const { data } = await apiClient.post("/users/check-photo-quality", payload, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return data;
    } catch {
      return { ok: false, reason: "Could not check this photo. Check your connection and try again." };
    }
  };

  const captureIntoSlot = async (key, file) => {
    // Rejected locally before the round-trip, and surfaced on the slot itself
    // so an unsupported file reads the same as a failed quality check.
    const formatError = photoFormatError(file);
    if (formatError) {
      setSlots((prev) => {
        const existing = prev[key];
        if (existing.previewUrl) URL.revokeObjectURL(existing.previewUrl);
        return { ...prev, [key]: { status: "error", file: null, previewUrl: null, reason: formatError } };
      });
      return;
    }
    setSlots((prev) => ({ ...prev, [key]: { ...prev[key], status: "checking", reason: null } }));
    const result = await checkQuality(file);
    setSlots((prev) => {
      const existing = prev[key];
      if (existing.previewUrl) URL.revokeObjectURL(existing.previewUrl);
      if (result.ok) {
        // previewUrlFor returns null for HEIC - accepted, but unrenderable in
        // any non-Apple browser, so the slot shows a placeholder rather than a
        // broken image. `file` is what actually gets uploaded either way.
        return { ...prev, [key]: { status: "ok", file, previewUrl: previewUrlFor(file), reason: null } };
      }
      return { ...prev, [key]: { status: "error", file: null, previewUrl: null, reason: result.reason } };
    });
  };

  const retakeSlot = (key) => {
    setSlots((prev) => {
      const existing = prev[key];
      if (existing.previewUrl) URL.revokeObjectURL(existing.previewUrl);
      return { ...prev, [key]: { status: "empty", file: null, previewUrl: null, reason: null } };
    });
  };

  const captureFallback = (file) => {
    // photoFormatError(null) is null, so "Retake / remove" still clears cleanly.
    const formatError = photoFormatError(file);
    setFallbackError(formatError || "");
    if (formatError) return;
    setFallback((prev) => {
      if (prev.previewUrl) URL.revokeObjectURL(prev.previewUrl);
      return { file, previewUrl: previewUrlFor(file) };
    });
  };

  const activeSlot = SLOTS.find((slot) => slot.key === activeSlotKey);
  const isCheckingActive = activeSlot && slots[activeSlot.key].status === "checking";

  return (
    <div className="col-span-full space-y-4 rounded-xl border border-ink-900/10 bg-parchment-50 p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-display text-base font-semibold text-ink-900">
            {mode === "guided" ? "Guided photo capture" : "Single-photo fallback"}
          </p>
          {mode === "guided" && (
            <p className="text-xs text-ink-500">
              {passedCount} of {SLOTS.length} shots passed
            </p>
          )}
        </div>
        <button
          type="button"
          disabled={disabled}
          onClick={() => setMode((m) => (m === "guided" ? "fallback" : "guided"))}
          className="rounded-full border border-maroon/30 px-3 py-1 text-xs font-semibold text-maroon transition-colors hover:bg-maroon hover:text-white disabled:opacity-50"
        >
          {mode === "guided" ? "Use single-photo fallback instead" : "Back to guided capture"}
        </button>
      </div>

      {mode === "guided" ? (
        <>
          <p className="text-xs text-ink-500">
            Have the student face the camera for each shot below - small variations only (a slight turn,
            neutral vs. smile), not side profiles. All 5 must pass quality checks before &ldquo;Add
            user&rdquo; unlocks.
          </p>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            {SLOTS.map((slot, index) => {
              const state = slots[slot.key];
              const isActive = slot.key === activeSlotKey;
              const visuals = slotVisuals(state.status, isActive);
              return (
                <div key={slot.key} className="pt-2">
                  <div className={`relative rounded-lg border-2 p-1.5 text-center transition-colors ${visuals.tile}`}>
                    <SlotBadge index={index + 1} status={state.status} className={visuals.badge} />
                    {state.previewUrl || state.status === "ok" ? (
                      <button
                        type="button"
                        onClick={() => retakeSlot(slot.key)}
                        title="Click to retake"
                        className="block w-full"
                      >
                        {state.previewUrl ? (
                          <img
                            src={state.previewUrl}
                            alt={slot.label}
                            className="aspect-square w-full rounded object-cover"
                          />
                        ) : (
                          // Captured and passed, just not renderable here (HEIC).
                          <div className="flex aspect-square w-full flex-col items-center justify-center rounded bg-parchment-100 text-[10px] text-ink-500">
                            <span className="text-base">🖼</span>
                            <span>HEIC captured</span>
                            <span className="text-ink-400">no preview</span>
                          </div>
                        )}
                      </button>
                    ) : (
                      <div className="flex aspect-square w-full flex-col items-center justify-center gap-1 rounded">
                        {state.status === "checking" ? (
                          <span className="h-6 w-6 animate-spin rounded-full border-2 border-gold-300 border-t-gold-600" />
                        ) : (
                          <CameraIcon className={`h-7 w-7 ${isActive ? "text-gold-500" : "text-ink-300"}`} />
                        )}
                      </div>
                    )}
                  </div>
                  <p className={`mt-1.5 text-xs font-semibold ${visuals.label}`}>{slot.label}</p>
                  <p className="text-[10px] text-ink-400">{slot.hint}</p>
                  {visuals.caption && (
                    <p className={`text-[10px] font-medium ${visuals.caption.className}`}>{visuals.caption.text}</p>
                  )}
                  {state.status === "error" && (
                    <p className="mt-0.5 text-[10px] leading-tight text-red-600">{state.reason}</p>
                  )}
                </div>
              );
            })}
          </div>

          {activeSlot ? (
            <div className="space-y-2 rounded-lg border border-ink-900/10 bg-white p-3">
              <p className="text-xs font-semibold text-ink-700">
                Now capturing: <span className="text-maroon">{activeSlot.label}</span>
                {isCheckingActive && " - checking photo quality..."}
              </p>
              <WebcamCapture onCapture={(file) => captureIntoSlot(activeSlot.key, file)} />
              <p className="text-xs text-ink-500">or upload a file instead:</p>
              <input
                type="file"
                accept={PHOTO_ACCEPT}
                disabled={isCheckingActive}
                onChange={(e) => {
                  const file = readPhotoInput(e);
                  if (file) captureIntoSlot(activeSlot.key, file);
                }}
                className="w-full rounded border border-ink-200 px-2 py-1.5 text-sm file:mr-3 file:rounded file:border-0 file:bg-maroon/10 file:px-2 file:py-1 file:text-xs file:font-medium file:text-maroon"
              />
              <p className="text-[10px] text-ink-400">JPEG, PNG or HEIC only.</p>
            </div>
          ) : (
            <div className="flex items-center gap-2 rounded-lg border border-gold-300 bg-gold-50 p-3">
              <CheckIcon className="h-5 w-5 text-gold-700" />
              <p className="text-sm font-semibold text-gold-800">All 5 photos captured.</p>
            </div>
          )}
        </>
      ) : (
        <>
          <p className="text-xs text-ink-500">
            One photo only - mainly meant for bulk import staging, not for a student standing right here.
            This enrollment will be flagged as lower-confidence.
          </p>
          {/* Keyed off `file`, not `previewUrl` - a HEIC upload has no preview
              but is very much selected, and falling back to the upload controls
              here would make it look like nothing had been picked. */}
          {fallback.file ? (
            <div className="flex items-center gap-3 rounded-lg border border-ink-900/10 bg-white p-3">
              {fallback.previewUrl ? (
                <img src={fallback.previewUrl} alt="Captured preview" className="h-20 w-20 rounded object-cover" />
              ) : (
                <div className="flex h-20 w-20 flex-col items-center justify-center rounded bg-parchment-100 text-[10px] text-ink-500">
                  <span>HEIC</span>
                  <span className="text-ink-400">no preview</span>
                </div>
              )}
              <button type="button" onClick={() => captureFallback(null)} className="text-sm font-medium text-maroon hover:underline">
                Retake / remove
              </button>
            </div>
          ) : (
            <div className="space-y-2 rounded-lg border border-ink-900/10 bg-white p-3">
              <WebcamCapture onCapture={captureFallback} />
              <p className="text-xs text-ink-500">or upload a file instead:</p>
              <input
                type="file"
                accept={PHOTO_ACCEPT}
                onChange={(e) => captureFallback(readPhotoInput(e))}
                className="w-full rounded border border-ink-200 px-2 py-1.5 text-sm file:mr-3 file:rounded file:border-0 file:bg-maroon/10 file:px-2 file:py-1 file:text-xs file:font-medium file:text-maroon"
              />
              <p className="text-[10px] text-ink-400">JPEG, PNG or HEIC only.</p>
              {fallbackError && <p className="text-sm text-red-600">{fallbackError}</p>}
            </div>
          )}
        </>
      )}
    </div>
  );
}
