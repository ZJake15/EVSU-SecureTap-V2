// Small shared building blocks for the redesigned dashboard - kept here so
// every page draws its header, icons and notices the same way.

export function Icon({ name, bold = false, size = 16, className = "", style }) {
  return (
    <i
      aria-hidden="true"
      className={`${bold ? "ph-bold" : "ph"} ph-${name} shrink-0 ${className}`}
      style={{ fontSize: size, ...style }}
    />
  );
}

// Eyebrow + 44px title on the left, the page's actions on the right,
// bottom-aligned so a button sits on the title's baseline.
export function PageHeader({ eyebrow, title, children }) {
  return (
    <div className="flex flex-none flex-wrap items-end justify-between gap-s5">
      <div className="flex min-w-0 flex-col gap-s2">
        {eyebrow && <span className="t-eyebrow">{eyebrow}</span>}
        <h1 className="t-title">{title}</h1>
      </div>
      {children && <div className="flex flex-wrap items-center gap-s3">{children}</div>}
    </div>
  );
}

const NOTICE_TONES = {
  danger: { bg: "bg-danger-tint", fg: "text-danger", icon: "warning-circle" },
  caution: { bg: "bg-caution-tint", fg: "text-caution", icon: "warning" },
  verified: { bg: "bg-verified-tint", fg: "text-verified", icon: "check-circle" },
  prompt: { bg: "bg-prompt-tint", fg: "text-prompt", icon: "info" },
  neutral: { bg: "bg-surface border border-line", fg: "text-ink", icon: "info" },
};

export function Notice({ tone = "danger", icon, className = "", children }) {
  const t = NOTICE_TONES[tone] || NOTICE_TONES.danger;
  return (
    <div className={`notice ${t.bg} ${className}`}>
      <Icon name={icon || t.icon} bold size={20} className={t.fg} />
      <div className="min-w-0 flex-1 leading-snug">{children}</div>
    </div>
  );
}

function initials(name) {
  if (!name) return "";
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] || "";
  const last = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (first + last).toUpperCase();
}

// Square (3px radius) photo tile - the person's photo when there is one,
// initials on canvas otherwise, or a dashed-face icon on ink for an unknown.
export function Avatar({ src, name, size = 32, unknown = false, className = "" }) {
  const box = { width: size, height: size };
  if (src) {
    return <img src={src} alt="" style={box} className={`shrink-0 rounded-sm object-cover ${className}`} />;
  }
  if (unknown || !name) {
    return (
      <span style={box} className={`flex shrink-0 items-center justify-center rounded-sm bg-ink text-ink-400 ${className}`}>
        <Icon name="user-circle-dashed" size={Math.round(size * 0.5)} />
      </span>
    );
  }
  return (
    <span
      style={{ ...box, fontSize: Math.max(11, Math.round(size * 0.36)) }}
      className={`flex shrink-0 items-center justify-center rounded-sm bg-canvas font-display font-bold text-ink-600 stretch-semi ${className}`}
    >
      {initials(name)}
    </span>
  );
}

// Two-or-more option toggle (Entry/Exit, Student/Staff, report ranges) - the
// selected segment is solid ink, per the mockups.
export function Segmented({ options, value, onChange, className = "" }) {
  return (
    <div
      role="radiogroup"
      className={`grid h-10 overflow-hidden rounded-sm border border-line bg-surface text-sm font-bold ${className}`}
      style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
    >
      {options.map((option, index) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(option.value)}
            className={`flex items-center justify-center gap-s2 whitespace-nowrap px-s4 transition-colors ${
              index > 0 ? "border-l border-line" : ""
            } ${selected ? "bg-ink text-white" : "text-ink-600 hover:bg-canvas"}`}
          >
            {option.icon && <Icon name={option.icon} bold={selected} size={16} />}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

// Label above a control, 6px apart. `eyebrow` switches the label to the
// condensed-caps style used by filter bars.
export function Field({ label, hint, eyebrow = false, className = "", children }) {
  return (
    <label className={`flex flex-col gap-s2 ${className}`}>
      <span className={eyebrow ? "t-eyebrow" : "field-label"}>{label}</span>
      {hint && <span className="-mt-s1 text-xs text-ink-600">{hint}</span>}
      {children}
    </label>
  );
}

export function Checkbox({ checked, onChange, label }) {
  return (
    <label className="flex h-10 cursor-pointer items-center gap-s3 text-sm font-bold text-ink">
      <input type="checkbox" checked={checked} onChange={onChange} className="h-[18px] w-[18px] accent-maroon" />
      {label}
    </label>
  );
}
