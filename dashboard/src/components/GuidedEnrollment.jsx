import { useEffect, useState } from "react";
import apiClient from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { PHOTO_ACCEPT, photoFormatError, previewUrlFor, readPhotoInput } from "../lib/photoUpload";
import PoseGuide from "./PoseGuide";
import { Icon, Notice } from "./ui";
import WebcamCapture from "./WebcamCapture";

// Near-frontal variation only - NOT wide angles or true side-profile shots.
// ArcFace (like the dlib model before it) matches best on near-frontal
// faces; a true 90-degree profile often won't even detect. Each key is also
// the PoseGuide drawing shown for that shot.
const SLOTS = [
  { key: "front", label: "Front", hint: "Straight on" },
  { key: "left", label: "Slight left", hint: "Turn left a little" },
  { key: "right", label: "Slight right", hint: "Turn right a little" },
  { key: "neutral", label: "Neutral", hint: "No expression" },
  { key: "smile", label: "Smile", hint: "Natural smile" },
];

const emptySlotState = () =>
  Object.fromEntries(SLOTS.map((slot) => [slot.key, { status: "empty", file: null, previewUrl: null, reason: null }]));

// Centralizes every visual decision per tile-state so the tile markup below
// just reads a config object, rather than a wall of conditional classNames.
function slotVisuals(status, isActive) {
  switch (status) {
    case "ok":
      return { tile: "border border-line bg-canvas", caption: "Passed", icon: "check-circle", tone: "text-verified" };
    case "error":
      return { tile: "border-2 border-danger bg-canvas", caption: "Failed", icon: "x-circle", tone: "text-danger" };
    case "checking":
      return { tile: "border-2 border-prompt bg-ink", caption: "Checking…", icon: "circle-notch", tone: "text-prompt" };
    default:
      return isActive
        ? { tile: "border-2 border-prompt bg-surface", caption: "Up next", icon: "camera", tone: "text-prompt" }
        : { tile: "border border-dashed border-ink-400 bg-surface", caption: "Waiting", icon: "circle", tone: "text-ink-600" };
  }
}

function ModeTab({ selected, disabled, onClick, children }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`border-b-[3px] pb-s2 text-sm transition-colors disabled:cursor-not-allowed ${
        selected ? "border-brass font-bold text-ink" : "border-transparent text-ink-600 hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

export default function GuidedEnrollment({ onChange, disabled }) {
  const [mode, setMode] = useState("guided"); // "guided" | "fallback"
  // Settings page: "Allow single-photo registration" (the server enforces
  // it too - this just doesn't offer an option that would be refused).
  const singlePhotoAllowed = useAuth().policy?.allow_single_photo !== false;
  useEffect(() => {
    if (!singlePhotoAllowed && mode === "fallback") setMode("guided");
  }, [singlePhotoAllowed, mode]);
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
  const failedSlot = SLOTS.find((slot) => slots[slot.key].status === "error");

  return (
    <div className="flex flex-col gap-s3 border-t border-line pt-s4">
      <div className="flex items-baseline justify-between">
        <span className="t-section">Face enrollment</span>
        {mode === "guided" && (
          <span className="font-mono text-sm font-semibold">
            {passedCount} / {SLOTS.length} captured
          </span>
        )}
      </div>
      <div className="flex gap-s4">
        <ModeTab selected={mode === "guided"} disabled={disabled} onClick={() => setMode("guided")}>
          Guided 5-shot capture
        </ModeTab>
        <ModeTab
          selected={mode === "fallback"}
          disabled={disabled || !singlePhotoAllowed}
          onClick={() => setMode("fallback")}
        >
          Single-photo fallback
        </ModeTab>
      </div>
      {!singlePhotoAllowed && (
        <span className="text-xs text-ink-600">Single-photo registration is turned off in Settings.</span>
      )}

      {mode === "guided" ? (
        <>
          <p className="text-xs leading-snug text-ink-600">
            Have the person face the camera for each shot - small variations only (a slight turn, neutral vs.
            smile), not side profiles. All 5 must pass the quality check before &ldquo;Save person&rdquo; unlocks.
            Click a passed photo to retake it.
          </p>
          <div className="grid grid-cols-5 gap-s2">
            {SLOTS.map((slot) => {
              const state = slots[slot.key];
              const isActive = slot.key === activeSlotKey;
              const visuals = slotVisuals(state.status, isActive);
              return (
                <div key={slot.key} className="flex min-w-0 flex-col gap-s1">
                  {state.status === "ok" ? (
                    <button
                      type="button"
                      onClick={() => retakeSlot(slot.key)}
                      title="Click to retake"
                      className={`relative flex aspect-[3/4] w-full items-center justify-center overflow-hidden rounded-sm ${visuals.tile}`}
                    >
                      {state.previewUrl ? (
                        <img src={state.previewUrl} alt={slot.label} className="h-full w-full object-cover" />
                      ) : (
                        // Captured and passed, just not renderable here (HEIC).
                        <span className="flex flex-col items-center text-[11px] text-ink-600">
                          <Icon name="image" size={20} />
                          HEIC
                        </span>
                      )}
                    </button>
                  ) : (
                    <div
                      className={`relative flex aspect-[3/4] w-full items-center justify-center rounded-sm ${visuals.tile}`}
                    >
                      {state.status === "checking" ? (
                        <Icon name="circle-notch" size={24} className="animate-spin text-ink-400" />
                      ) : (
                        <PoseGuide
                          pose={slot.key}
                          className={`h-full w-full p-s1 ${isActive ? "text-ink" : "text-ink-400"}`}
                        />
                      )}
                    </div>
                  )}
                  <span className="truncate text-xs font-bold leading-tight">{slot.label}</span>
                  <span className="truncate text-[11px] leading-tight text-ink-600">{slot.hint}</span>
                  <span className={`flex items-center gap-[3px] text-[11px] font-bold leading-tight ${visuals.tone}`}>
                    <Icon name={visuals.icon} bold size={12} className={state.status === "checking" ? "animate-spin" : ""} />
                    {visuals.caption}
                  </span>
                </div>
              );
            })}
          </div>

          {failedSlot && (
            <Notice tone="danger" icon="x-circle" className="py-s2 text-xs">
              <b>{failedSlot.label} failed:</b> {slots[failedSlot.key].reason} Retake before continuing.
            </Notice>
          )}

          {activeSlot ? (
            <div className="flex flex-col gap-s2 rounded-sm border border-line p-s3">
              <div className="flex items-center gap-s3">
                <PoseGuide
                  pose={activeSlot.key}
                  className="h-28 w-24 shrink-0 rounded-sm border border-line bg-surface p-s1 text-ink"
                />
                <div className="flex min-w-0 flex-col gap-s1">
                  <span className="text-sm font-bold">
                    Now capturing: <span className="text-prompt">{activeSlot.label}</span>
                    <span className="font-normal text-ink-600">
                      {isCheckingActive ? " · checking photo quality…" : ` · ${activeSlot.hint}`}
                    </span>
                  </span>
                  <span className="text-xs leading-snug text-ink-600">
                    Have the person copy this pose, facing the camera.
                  </span>
                </div>
              </div>
              <WebcamCapture onCapture={(file) => captureIntoSlot(activeSlot.key, file)} />
              <span className="text-xs text-ink-600">or upload a file instead (JPEG, PNG or HEIC only):</span>
              <input
                type="file"
                accept={PHOTO_ACCEPT}
                disabled={isCheckingActive}
                onChange={(e) => {
                  const file = readPhotoInput(e);
                  if (file) captureIntoSlot(activeSlot.key, file);
                }}
                className="file-input"
              />
            </div>
          ) : (
            <Notice tone="verified">
              <b>All 5 photos captured.</b>
            </Notice>
          )}
        </>
      ) : (
        <>
          <p className="text-xs leading-snug text-ink-600">
            One photo only - mainly meant for bulk import staging, not for a person standing right here. This
            enrollment will be flagged as lower-confidence.
          </p>
          {/* Keyed off `file`, not `previewUrl` - a HEIC upload has no preview
              but is very much selected, and falling back to the upload controls
              here would make it look like nothing had been picked. */}
          {fallback.file ? (
            <div className="flex items-center gap-s3 rounded-sm border border-line p-s3">
              {fallback.previewUrl ? (
                <img src={fallback.previewUrl} alt="Captured preview" className="h-20 w-20 rounded-sm object-cover" />
              ) : (
                <div className="flex h-20 w-20 flex-col items-center justify-center rounded-sm bg-canvas text-[11px] text-ink-600">
                  <span className="font-bold">HEIC</span>
                  <span>no preview</span>
                </div>
              )}
              <button type="button" onClick={() => captureFallback(null)} className="link-action">
                Retake / remove
              </button>
            </div>
          ) : (
            <div className="flex flex-col gap-s2 rounded-sm border border-line p-s3">
              <WebcamCapture onCapture={captureFallback} />
              <span className="text-xs text-ink-600">or upload a file instead (JPEG, PNG or HEIC only):</span>
              <input
                type="file"
                accept={PHOTO_ACCEPT}
                onChange={(e) => captureFallback(readPhotoInput(e))}
                className="file-input"
              />
              {fallbackError && <p className="text-sm font-bold text-danger">{fallbackError}</p>}
            </div>
          )}
        </>
      )}
    </div>
  );
}
