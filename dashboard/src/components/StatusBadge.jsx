const STATUS_STYLES = {
  success: { label: "Success", className: "border border-emerald-200 bg-emerald-50 text-emerald-800" },
  failed: { label: "Failed", className: "border border-red-200 bg-red-50 text-red-800" },
  // Distinct from a plain "Failed" (unrecognized face) - this is a security
  // event (liveness/anti-spoofing check failed), not a recognition miss.
  spoof_suspected: { label: "Spoof suspected", className: "border border-purple-200 bg-purple-50 text-purple-800" },
  // Also distinct from "Failed" - the mouth/nose read as covered before the
  // face was ever compared against anyone, so this was never a genuine
  // non-match. Teal: not yet claimed by success (emerald) or spoof (purple).
  occlusion_detected: { label: "Occlusion detected", className: "border border-teal-200 bg-teal-50 text-teal-800" },
};

export default function StatusBadge({ status }) {
  const { label, className } = STATUS_STYLES[status] || STATUS_STYLES.failed;
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${className}`}>
      {label}
    </span>
  );
}
