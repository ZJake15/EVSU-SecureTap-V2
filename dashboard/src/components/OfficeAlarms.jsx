import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import apiClient from "../api/client";
import { Avatar, Icon } from "./ui";

// The office alarm (Admin and SASO, on every dashboard page): a suspected
// fake face, or the same unknown person seen again at a gate, pops up in the
// bottom-right corner with a short alarm sound until someone dismisses it.
// Settings > Alerts decides which (each event's own alert switch, and "Office
// alarm on the dashboard" over both). Backend: logs/office_views.py AlarmsView
// - asked every few seconds from the server's own clock, so nothing is
// missed or shown twice, and nothing from before the page opened pops up.

const POLL_MS = 5000;
const POLL_OFF_MS = 30000; // while the office alarm is switched off
const SHOWN = 4; // the newest few; "Dismiss all" clears the rest

const KINDS = {
  spoof: { title: "Fake face suspected", tone: "danger", icon: "warning-octagon" },
  repeated_unknown: { title: "Same unknown person again", tone: "caution", icon: "user-circle-dashed" },
};

// A short alarm in the browser (no sound file): a rising siren for a fake
// face, two bell notes for a repeated unknown - like the gate monitor's.
let audio = null;
function sound(kind) {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
    const start = audio.currentTime + 0.02;
    const notes =
      kind === "spoof"
        ? [0, 0.32, 0.64].map((at) => ({ at, from: 650, to: 1050, length: 0.28 }))
        : [
            { at: 0, from: 1200, to: 1200, length: 0.16 },
            { at: 0.2, from: 900, to: 900, length: 0.2 },
          ];
    notes.forEach(({ at, from, to, length }) => {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      osc.type = kind === "spoof" ? "sawtooth" : "sine";
      osc.frequency.setValueAtTime(from, start + at);
      osc.frequency.linearRampToValueAtTime(to, start + at + length);
      gain.gain.setValueAtTime(0.0001, start + at);
      gain.gain.exponentialRampToValueAtTime(0.18, start + at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + at + length);
      osc.connect(gain).connect(audio.destination);
      osc.start(start + at);
      osc.stop(start + at + length + 0.02);
    });
  } catch {
    // no sound in this browser - the pop-up still shows
  }
}

// Browsers only allow sound after the page has been clicked once.
function unlockSoundOnFirstClick() {
  const unlock = () => {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      audio.resume();
    } catch {
      /* no sound in this browser */
    }
    window.removeEventListener("pointerdown", unlock);
  };
  window.addEventListener("pointerdown", unlock);
  return () => window.removeEventListener("pointerdown", unlock);
}

function timeText(value) {
  return new Date(value).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

export default function OfficeAlarms() {
  const [alarms, setAlarms] = useState([]);
  const since = useRef(null);
  const timer = useRef(null);
  const stopped = useRef(false);

  const ask = useCallback(async () => {
    let wait = POLL_MS;
    try {
      const { data } = await apiClient.get("/alarms", { params: since.current ? { since: since.current } : {} });
      since.current = data.server_time;
      if (!data.enabled) wait = POLL_OFF_MS;
      if (data.alarms.length && !stopped.current) {
        sound(data.alarms.some((alarm) => alarm.alarm === "spoof") ? "spoof" : "repeated_unknown");
        setAlarms((current) => [...data.alarms.slice().reverse(), ...current].slice(0, 30));
      }
    } catch {
      // the server is unreachable or restarting - ask again later
    }
    if (!stopped.current) timer.current = setTimeout(ask, wait);
  }, []);

  useEffect(() => {
    stopped.current = false;
    ask();
    const forget = unlockSoundOnFirstClick();
    return () => {
      stopped.current = true;
      clearTimeout(timer.current);
      forget();
    };
  }, [ask]);

  if (!alarms.length) return null;
  const dismiss = (id) => setAlarms((current) => current.filter((alarm) => alarm.id !== id));

  return (
    <div
      role="alert"
      className="fixed bottom-s5 right-s5 z-50 flex w-[380px] max-w-[calc(100vw-2rem)] flex-col gap-s2 print:hidden"
    >
      {alarms.slice(0, SHOWN).map((alarm) => {
        const kind = KINDS[alarm.alarm] || KINDS.spoof;
        const danger = kind.tone === "danger";
        return (
          <div
            key={alarm.id}
            className={`card flex gap-s3 border-l-4 p-s3 shadow-lg ${danger ? "border-l-danger" : "border-l-caution"}`}
          >
            <Avatar src={alarm.captured_photo} unknown size={56} />
            <div className="flex min-w-0 flex-1 flex-col gap-[2px]">
              <span className={`flex items-center gap-s1 text-sm font-bold ${danger ? "text-danger" : "text-caution"}`}>
                <Icon name={kind.icon} bold size={16} />
                {kind.title}
              </span>
              <span className="text-sm text-ink">
                {alarm.gate_location}
                {alarm.direction === "exit" ? " · exit" : ""} · {timeText(alarm.timestamp)}
              </span>
              <Link to="/logs" className="link-action" onClick={() => dismiss(alarm.id)}>
                See it in Logs
              </Link>
            </div>
            <button
              type="button"
              title="Dismiss"
              onClick={() => dismiss(alarm.id)}
              className="h-8 w-8 flex-none rounded-sm text-ink-600 hover:bg-canvas"
            >
              <Icon name="x" size={16} />
            </button>
          </div>
        );
      })}
      {alarms.length > 1 && (
        <button type="button" className="btn-secondary btn-sm self-end shadow" onClick={() => setAlarms([])}>
          Dismiss all{alarms.length > SHOWN ? ` (${alarms.length})` : ""}
        </button>
      )}
    </div>
  );
}
