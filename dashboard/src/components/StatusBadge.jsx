const STATUS_STYLES = {
  success: { label: "Success", className: "bg-green-100 text-green-800" },
  failed: { label: "Failed", className: "bg-red-100 text-red-800" },
  // Distinct from a plain "Failed" (unrecognized face) - this is a security
  // event (liveness/anti-spoofing check failed), not a recognition miss.
  spoof_suspected: { label: "Spoof suspected", className: "bg-purple-100 text-purple-800" },
};

export default function StatusBadge({ status }) {
  const { label, className } = STATUS_STYLES[status] || STATUS_STYLES.failed;
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${className}`}>
      {label}
    </span>
  );
}
