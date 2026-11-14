import { Icon } from "./ui";

// Status is always an icon + a word in its status color - never color alone.
const STATUS_STYLES = {
  success: { label: "Success", icon: "check-circle", className: "text-verified" },
  // Status "failed": not recognized - called Unknown everywhere (gate monitor,
  // Live Monitoring, Logs, Reports), in Unknown's amber.
  failed: { label: "Unknown", icon: "user-circle-dashed", className: "text-caution" },
  // Distinct from a plain Unknown (unrecognized face) - this is a security
  // event (liveness/anti-spoofing check failed), not a recognition miss.
  spoof_suspected: { label: "Spoof suspected", icon: "warning-octagon", className: "text-danger" },
  // Also distinct from Unknown - the mouth/nose read as covered before the
  // face was ever compared against anyone, so this was never a genuine
  // non-match. Prompt blue: it's an instruction to the person, not an alarm.
  occlusion_detected: { label: "Face covered", icon: "hand-palm", className: "text-prompt" },
  // A recognized student refused - not on this semester's enrolled list
  // (Settings > Semester Enrollment: "Refuse entry").
  not_enrolled: { label: "Not enrolled · refused", icon: "prohibit", className: "text-danger" },
};

// Left-edge bar color on a log row - only non-routine rows get one.
export const STATUS_BAR = {
  success: "#FFFFFF",
  failed: "#9A5B00", // Unknown - caution amber, as on the gate monitor
  spoof_suspected: "#C62828",
  occlusion_detected: "#1D5FA8",
  not_enrolled: "#C62828",
};

// An entry written outside the gate's opening hours (Settings > Scheduling).
export function AfterHoursTag() {
  return (
    <span
      className="inline-flex items-center gap-s1 whitespace-nowrap rounded-sm bg-caution-tint px-s1 text-xs font-bold text-caution"
      title="Outside the gate's opening hours (Settings > Scheduling)"
    >
      <Icon name="moon" bold size={12} />
      After hours
    </span>
  );
}

// A student let in who isn't on the current semester's enrolled list (Semester page).
export function NotEnrolledTag() {
  return (
    <span
      className="inline-flex items-center gap-s1 whitespace-nowrap rounded-sm bg-caution-tint px-s1 text-xs font-bold uppercase text-caution"
      title="Not on this semester's enrolled list (Semester page) - let in, flagged for checking"
    >
      <Icon name="warning" bold size={12} />
      Not enrolled
    </span>
  );
}

// Which way through the gate: IN (entrance camera / reader) or OUT (the exit's).
// Shown on every record once a gate records exits; an entry-only record still
// says IN, so the two never look alike.
export function DirectionTag({ direction }) {
  const out = direction === "exit";
  return (
    <span
      className={`inline-flex items-center gap-s1 whitespace-nowrap rounded-sm px-s1 text-xs font-bold uppercase ${
        out ? "bg-canvas text-ink-600" : "bg-verified-tint text-verified"
      }`}
      title={out ? "Leaving - the gate's exit camera or card reader" : "Coming in - the gate's entrance camera or card reader"}
    >
      <Icon name={out ? "sign-out" : "sign-in"} bold size={12} />
      {out ? "Out" : "In"}
    </span>
  );
}

// An odd entry/exit pattern (backend EntryLog.PatternFlag) - usually a missed
// scan, sometimes tailgating. Let in either way.
export const PATTERN_LABELS = {
  no_entry: { label: "No entry today", title: "Left with no entry recorded for them today" },
  no_exit: { label: "No exit since entry", title: "Came in again with no exit since their last entry" },
};

export function PatternTag({ flag }) {
  const pattern = PATTERN_LABELS[flag];
  if (!pattern) return null;
  return (
    <span
      className="inline-flex items-center gap-s1 whitespace-nowrap rounded-sm bg-caution-tint px-s1 text-xs font-bold uppercase text-caution"
      title={`${pattern.title} - usually a missed scan; worth a question`}
    >
      <Icon name="warning" bold size={12} />
      {pattern.label}
    </span>
  );
}

export default function StatusBadge({ status }) {
  const { label, icon, className } = STATUS_STYLES[status] || STATUS_STYLES.failed;
  return (
    <span className={`inline-flex items-center gap-s1 whitespace-nowrap text-sm font-bold ${className}`}>
      <Icon name={icon} bold size={16} />
      {label}
    </span>
  );
}
