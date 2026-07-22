import { useEffect, useState } from "react";
import apiClient from "../api/client";
import WebcamCapture from "./WebcamCapture";

// Near-frontal variation only - NOT wide angles or true side-profile shots.
// ArcFace (like the dlib model before it) matches best on near-frontal
// faces; a true 90-degree profile often won't even detect.
const SLOTS = [
  { key: "front", label: "Front - straight on" },
  { key: "left", label: "Slight left turn" },
  { key: "right", label: "Slight right turn" },
  { key: "neutral", label: "Neutral (no expression)" },
  { key: "smile", label: "Smile" },
];

const emptySlotState = () =>
  Object.fromEntries(SLOTS.map((slot) => [slot.key, { status: "empty", file: null, previewUrl: null, reason: null }]));

export default function GuidedEnrollment({ onChange, disabled }) {
  const [mode, setMode] = useState("guided"); // "guided" | "fallback"
  const [slots, setSlots] = useState(emptySlotState);
  const [fallback, setFallback] = useState({ file: null, previewUrl: null });

  const activeSlotKey = SLOTS.find((slot) => slots[slot.key].status !== "ok")?.key || null;
  const allSlotsOk = SLOTS.every((slot) => slots[slot.key].status === "ok");

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
    setSlots((prev) => ({ ...prev, [key]: { ...prev[key], status: "checking", reason: null } }));
    const result = await checkQuality(file);
    setSlots((prev) => {
      const existing = prev[key];
      if (existing.previewUrl) URL.revokeObjectURL(existing.previewUrl);
      if (result.ok) {
        return { ...prev, [key]: { status: "ok", file, previewUrl: URL.createObjectURL(file), reason: null } };
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
    setFallback((prev) => {
      if (prev.previewUrl) URL.revokeObjectURL(prev.previewUrl);
      return { file, previewUrl: file ? URL.createObjectURL(file) : null };
    });
  };

  const activeSlot = SLOTS.find((slot) => slot.key === activeSlotKey);
  const isCheckingActive = activeSlot && slots[activeSlot.key].status === "checking";

  return (
    <div className="col-span-full space-y-3 rounded border border-gray-200 p-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-gray-700">
          {mode === "guided" ? "Guided photo capture (5 shots)" : "Single-photo fallback"}
        </p>
        <button
          type="button"
          disabled={disabled}
          onClick={() => setMode((m) => (m === "guided" ? "fallback" : "guided"))}
          className="text-xs text-maroon hover:underline disabled:opacity-50"
        >
          {mode === "guided" ? "Use single-photo fallback instead" : "Back to guided capture"}
        </button>
      </div>

      {mode === "guided" ? (
        <>
          <p className="text-xs text-gray-500">
            Have the student face the camera for each shot below - small variations only (a slight turn,
            neutral vs. smile), not side profiles. All 5 must pass quality checks before "Add user" unlocks.
          </p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {SLOTS.map((slot) => {
              const state = slots[slot.key];
              const isActive = slot.key === activeSlotKey;
              const borderClass =
                state.status === "ok"
                  ? "border-green-500"
                  : state.status === "error"
                    ? "border-red-500"
                    : isActive
                      ? "border-maroon"
                      : "border-gray-200";
              return (
                <div key={slot.key} className={`rounded border-2 p-1.5 text-center ${borderClass}`}>
                  {state.previewUrl ? (
                    <button
                      type="button"
                      onClick={() => retakeSlot(slot.key)}
                      title="Click to retake"
                      className="block w-full"
                    >
                      <img src={state.previewUrl} alt={slot.label} className="aspect-square w-full rounded object-cover" />
                    </button>
                  ) : (
                    <div className="flex aspect-square w-full items-center justify-center rounded bg-gray-100 text-[10px] text-gray-400">
                      {state.status === "checking" ? "Checking..." : "Not captured"}
                    </div>
                  )}
                  <p className="mt-1 text-[10px] font-medium text-gray-700">{slot.label}</p>
                  {state.status === "ok" && <p className="text-[10px] text-green-700">Passed</p>}
                  {state.status === "error" && <p className="text-[10px] text-red-600">{state.reason}</p>}
                </div>
              );
            })}
          </div>

          {activeSlot ? (
            <div className="space-y-2 border-t border-gray-100 pt-2">
              <p className="text-xs font-medium text-gray-600">
                Now capturing: {activeSlot.label}
                {isCheckingActive && " - checking photo quality..."}
              </p>
              <WebcamCapture onCapture={(file) => captureIntoSlot(activeSlot.key, file)} />
              <p className="text-xs text-gray-500">or upload a file instead:</p>
              <input
                type="file"
                accept="image/*"
                disabled={isCheckingActive}
                onChange={(e) => e.target.files?.[0] && captureIntoSlot(activeSlot.key, e.target.files[0])}
                className="rounded border border-gray-300 px-2 py-1.5 text-sm"
              />
            </div>
          ) : (
            <p className="text-sm font-medium text-green-700">All 5 photos captured.</p>
          )}
        </>
      ) : (
        <>
          <p className="text-xs text-gray-500">
            One photo only - mainly meant for bulk import staging, not for a student standing right here.
            This enrollment will be flagged as lower-confidence.
          </p>
          {fallback.previewUrl ? (
            <div className="flex items-center gap-3">
              <img src={fallback.previewUrl} alt="Captured preview" className="h-20 w-20 rounded object-cover" />
              <button type="button" onClick={() => captureFallback(null)} className="text-sm text-maroon hover:underline">
                Retake / remove
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              <WebcamCapture onCapture={captureFallback} />
              <p className="text-xs text-gray-500">or upload a file instead:</p>
              <input
                type="file"
                accept="image/*"
                onChange={(e) => captureFallback(e.target.files?.[0] || null)}
                className="rounded border border-gray-300 px-2 py-1.5 text-sm"
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
