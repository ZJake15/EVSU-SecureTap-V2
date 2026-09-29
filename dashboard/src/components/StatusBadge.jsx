import { Icon } from "./ui";

// Status is always an icon + a word in its status color - never color alone.
const STATUS_STYLES = {
  success: { label: "Success", icon: "check-circle", className: "text-verified" },
  failed: { label: "Failed", icon: "x-circle", className: "text-danger" },
  // Distinct from a plain "Failed" (unrecognized face) - this is a security
  // event (liveness/anti-spoofing check failed), not a recognition miss.
  spoof_suspected: { label: "Spoof suspected", icon: "warning-octagon", className: "text-danger" },
  // Also distinct from "Failed" - the mouth/nose read as covered before the
  // face was ever compared against anyone, so this was never a genuine
  // non-match. Prompt blue: it's an instruction to the person, not an alarm.
  occlusion_detected: { label: "Face covered", icon: "hand-palm", className: "text-prompt" },
};

// Left-edge bar color on a log row - only non-routine rows get one.
export const STATUS_BAR = {
  success: "#FFFFFF",
  failed: "#C62828",
  spoof_suspected: "#C62828",
  occlusion_detected: "#1D5FA8",
};

export default function StatusBadge({ status }) {
  const { label, icon, className } = STATUS_STYLES[status] || STATUS_STYLES.failed;
  return (
    <span className={`inline-flex items-center gap-s1 whitespace-nowrap text-sm font-bold ${className}`}>
      <Icon name={icon} bold size={16} />
      {label}
    </span>
  );
}
