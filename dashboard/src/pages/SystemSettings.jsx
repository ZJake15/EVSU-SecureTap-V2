import { useCallback, useEffect, useMemo, useState } from "react";
import { useBlocker } from "react-router-dom";
import apiClient from "../api/client";
import { Icon, Notice, PageHeader, Segmented } from "../components/ui";

// The dashboard's Settings page. Every setting - its label, plain-English
// description, limits and recommended range - comes from the backend
// (configuration/registry.py), so this page only draws what it's given and
// a new setting there shows up here without changes. The server re-checks
// every value on save; the limits here are just a convenience.

const NUMBER_KINDS = new Set(["int", "float"]);

function fmt(value) {
  if (typeof value !== "number") return String(value);
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)));
}

function toDraft(setting, value = setting.value) {
  return NUMBER_KINDS.has(setting.kind) ? fmt(value) : value;
}

// { value } when the typed text is allowed, { error } when it isn't.
export function parseDraft(setting, raw) {
  if (!NUMBER_KINDS.has(setting.kind)) return { value: raw };
  if (String(raw).trim() === "") return { error: "Enter a number." };
  const number = Number(raw);
  if (!Number.isFinite(number)) return { error: "Enter a number." };
  if (setting.kind === "int" && !Number.isInteger(number)) return { error: "Use a whole number." };
  if (number < setting.minimum || number > setting.maximum) {
    return { error: `Must be between ${fmt(setting.minimum)} and ${fmt(setting.maximum)}.` };
  }
  return { value: number };
}

// Why saving this value needs confirming (risky settings only), or null.
export function riskReason(setting, value) {
  if (!setting.risky || value === undefined) return null;
  if (setting.kind === "bool") {
    return setting.recommended !== null && value !== setting.recommended ? setting.off_warning : null;
  }
  if (Array.isArray(setting.recommended)) {
    const [low, high] = setting.recommended;
    if (value < low || value > high) {
      return `${fmt(value)} is outside the recommended range of ${fmt(low)} to ${fmt(high)}. This can let the wrong people in or keep real students out.`;
    }
  }
  return null;
}

function lastChanged(setting) {
  if (!setting.changed_at) return null;
  const when = new Date(setting.changed_at).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return `Last changed by ${setting.changed_by} on ${when}`;
}

function Toggle({ checked, onChange, label }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className="flex items-center gap-s3 text-sm font-bold"
    >
      <span
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
          checked ? "bg-maroon" : "bg-ink-400"
        }`}
      >
        <span
          className={`inline-block h-5 w-5 rounded-full bg-white shadow transition-transform ${
            checked ? "translate-x-[22px]" : "translate-x-[2px]"
          }`}
        />
      </span>
      <span className={checked ? "text-ink" : "text-ink-600"}>{checked ? "On" : "Off"}</span>
    </button>
  );
}

function Control({ setting, raw, onChange, invalid }) {
  if (setting.kind === "bool") return <Toggle checked={raw} onChange={onChange} label={setting.label} />;
  if (setting.kind === "choice") {
    return <Segmented options={setting.choices} value={raw} onChange={onChange} className="min-w-[260px]" />;
  }
  if (setting.kind === "fixed") {
    return (
      <span className="flex h-10 items-center gap-s2 rounded-sm border border-line bg-canvas px-s3 font-mono text-sm font-semibold">
        <Icon name="lock-simple" size={14} className="text-ink-600" />
        {fmt(setting.value)} {setting.unit}
      </span>
    );
  }
  // The unit sits inside the box, so every control lines up on the right.
  return (
    <span className="relative inline-flex">
      <input
        type="number"
        inputMode="decimal"
        min={setting.minimum}
        max={setting.maximum}
        step={setting.step}
        value={raw}
        onChange={(event) => onChange(event.target.value)}
        aria-label={`${setting.label}${setting.unit ? ` (${setting.unit})` : ""}`}
        aria-invalid={invalid}
        className={`input w-[168px] text-right font-mono ${setting.unit ? "pr-[78px]" : ""} ${
          invalid ? "border-danger focus:border-danger focus:ring-danger" : ""
        }`}
      />
      {setting.unit && (
        <span className="pointer-events-none absolute right-s3 top-1/2 w-[62px] -translate-y-1/2 text-sm text-ink-600">
          {setting.unit}
        </span>
      )}
    </span>
  );
}

function SettingRow({ setting, raw, parsed, dirty, dependencyOff, dependencyLabel, serverError, onChange, onReset }) {
  const value = parsed.value;
  const risk = parsed.error ? null : riskReason(setting, value);
  const error = parsed.error || serverError;
  const changed = lastChanged(setting);
  const atRecommended = setting.recommended_value === null || value === setting.recommended_value;

  return (
    <div className={`grid gap-s3 border-t border-line py-s4 md:grid-cols-[minmax(0,1fr)_auto] md:gap-s5 ${dependencyOff ? "opacity-60" : ""}`}>
      <div className="flex min-w-0 flex-col gap-s1">
        <div className="flex flex-wrap items-center gap-s2">
          <span className="text-[15px] font-bold text-ink">{setting.label}</span>
          {dirty && (
            <span className="rounded-sm bg-prompt-tint px-s2 py-[1px] text-xs font-bold text-prompt">Edited</span>
          )}
          {setting.requires_restart && (
            <span className="rounded-sm bg-caution-tint px-s2 py-[1px] text-xs font-bold text-caution">
              Takes effect after the gate monitor restarts
            </span>
          )}
        </div>
        <p className="max-w-[640px] text-sm leading-snug text-ink-600">{setting.description}</p>
        {Array.isArray(setting.recommended) && (
          <span className="text-xs font-bold text-ink-600">
            Recommended: {fmt(setting.recommended[0])} – {fmt(setting.recommended[1])}
          </span>
        )}
        {setting.note && <span className="max-w-[640px] text-xs leading-snug text-ink-600">{setting.note}</span>}
        {setting.status && <span className="text-xs font-bold text-ink">{setting.status}</span>}
        {dependencyOff && <span className="text-xs text-ink-600">Turn on “{dependencyLabel}” to use this.</span>}
        {error && (
          <Notice tone="danger" className="mt-s1 max-w-[640px] py-s2 text-xs">
            {error}
          </Notice>
        )}
        {risk && (
          <Notice tone="caution" className="mt-s1 max-w-[640px] py-s2 text-xs">
            <b>Outside the recommended setting.</b> {risk}
          </Notice>
        )}
        {changed && <span className="mt-s1 text-xs text-ink-400">{changed}</span>}
      </div>
      <div className="flex flex-col items-start gap-s2 md:items-end">
        <Control setting={setting} raw={raw} onChange={onChange} invalid={Boolean(error)} />
        {setting.risky && !atRecommended && (
          <button type="button" onClick={onReset} className="link-action text-xs">
            Reset to recommended ({setting.kind === "bool" ? (setting.recommended_value ? "On" : "Off") : fmt(setting.recommended_value)})
          </button>
        )}
      </div>
    </div>
  );
}

// The page body - pure, so it can be drawn from any data (the page itself,
// and the screenshot used in documentation.md).
export function SettingsContent({
  data,
  draft,
  serverErrors = {},
  onChange = () => {},
  onReset = () => {},
  onResetAll = () => {},
  onSave = () => {},
  onDiscard = () => {},
  saving = false,
  notice = null,
}) {
  const byKey = useMemo(() => Object.fromEntries(data.settings.map((setting) => [setting.key, setting])), [data]);
  const rows = data.settings.map((setting) => {
    const raw = draft[setting.key];
    const parsed = parseDraft(setting, raw);
    const dirty = parsed.error ? raw !== toDraft(setting) : parsed.value !== setting.value;
    return { setting, raw, parsed, dirty };
  });
  const dirtyCount = rows.filter((row) => row.dirty).length;
  const hasErrors = rows.some((row) => row.dirty && row.parsed.error);
  const allAtDefault = rows.every(({ setting, raw }) => raw === toDraft(setting, setting.default));

  return (
    <div className="flex flex-col">
      <PageHeader eyebrow="System configuration" title="Settings">
        <button
          type="button"
          onClick={onResetAll}
          disabled={saving || allAtDefault}
          title={allAtDefault ? "Every setting is already at its default." : undefined}
          className="btn-secondary"
        >
          <Icon name="arrow-counter-clockwise" bold size={16} />
          Reset all to defaults
        </button>
      </PageHeader>
      <p className="mt-s3 max-w-[760px] text-sm leading-snug text-ink-600">
        Changes apply to every gate within a few seconds - nobody has to restart anything. Every change is
        recorded in the Audit Log. Only Admins can see this page.
      </p>
      {notice && (
        <Notice tone={notice.tone} className="mt-s4 max-w-[760px]">
          {notice.text}
        </Notice>
      )}

      <div className="mt-s5 grid grid-cols-1 items-start gap-s5 xl:grid-cols-[220px_minmax(0,1fr)]">
        <nav className="sticky top-s5 hidden flex-col gap-s1 xl:flex" aria-label="Settings sections">
          {data.sections.map((section) => (
            <a
              key={section.key}
              href={`#settings-${section.key}`}
              className="rounded-sm px-s3 py-s2 text-sm font-bold text-ink-600 hover:bg-surface hover:text-ink"
            >
              {section.title}
            </a>
          ))}
        </nav>

        <div className="flex min-w-0 flex-col gap-s5">
          {data.sections.map((section, index) => (
            <section key={section.key} id={`settings-${section.key}`} className="card scroll-mt-s5 px-s5 pb-s2 pt-s5">
              <div className="flex flex-col gap-s1 pb-s4">
                <span className="t-eyebrow">{section.key === "advanced" ? "Optional" : `Section ${index + 1}`}</span>
                <h2 className="t-section">{section.title}</h2>
                <p className="text-sm text-ink-600">{section.intro}</p>
              </div>
              {rows
                .filter((row) => row.setting.section === section.key)
                .map(({ setting, raw, parsed, dirty }) => {
                  const dependency = setting.depends_on ? byKey[setting.depends_on] : null;
                  return (
                    <SettingRow
                      key={setting.key}
                      setting={setting}
                      raw={raw}
                      parsed={parsed}
                      dirty={dirty}
                      dependencyOff={dependency ? draft[dependency.key] === false : false}
                      dependencyLabel={dependency?.label}
                      serverError={serverErrors[setting.key]}
                      onChange={(value) => onChange(setting.key, value)}
                      onReset={() => onReset(setting.key)}
                    />
                  );
                })}
            </section>
          ))}
        </div>
      </div>

      {dirtyCount > 0 && (
        <div className="sticky bottom-s4 z-20 mt-s5 flex flex-wrap items-center gap-s3 rounded-md border border-line bg-surface px-s5 py-s3 shadow-lg">
          <Icon name="pencil-simple" bold size={18} className="text-prompt" />
          <span className="text-sm font-bold">
            {dirtyCount} unsaved change{dirtyCount === 1 ? "" : "s"}
          </span>
          {hasErrors && <span className="text-sm text-danger">Fix the highlighted values first.</span>}
          <span className="flex-1" />
          <button type="button" onClick={onDiscard} disabled={saving} className="btn-secondary">
            Discard
          </button>
          <button type="button" onClick={onSave} disabled={saving || hasErrors} className="btn-primary">
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      )}
    </div>
  );
}

function Dialog({ title, children, actions }) {
  return (
    <>
      <div className="fixed inset-0 z-40 bg-ink/30" aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="fixed left-1/2 top-1/2 z-50 flex w-[min(520px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col gap-s4 rounded-md bg-surface p-s5 shadow-xl"
      >
        <h2 className="t-section">{title}</h2>
        {children}
        <div className="flex flex-wrap justify-end gap-s3">{actions}</div>
      </div>
    </>
  );
}

export default function SystemSettings() {
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [loadError, setLoadError] = useState("");
  const [serverErrors, setServerErrors] = useState({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);
  const [confirmItems, setConfirmItems] = useState(null);
  const [confirmResetAll, setConfirmResetAll] = useState(false);

  const applyData = useCallback((next) => {
    setData(next);
    setDraft(Object.fromEntries(next.settings.map((setting) => [setting.key, toDraft(setting)])));
    setServerErrors({});
  }, []);

  useEffect(() => {
    apiClient
      .get("/settings")
      .then(({ data: payload }) => applyData(payload))
      .catch(() => setLoadError("Could not load the settings. Try refreshing the page."));
  }, [applyData]);

  const changes = useMemo(() => {
    if (!data) return [];
    return data.settings
      .map((setting) => ({ setting, parsed: parseDraft(setting, draft[setting.key]) }))
      .filter(({ setting, parsed }) =>
        parsed.error ? draft[setting.key] !== toDraft(setting) : parsed.value !== setting.value
      );
  }, [data, draft]);
  const dirty = changes.length > 0;

  // Leaving with unsaved changes: a warning for links inside the dashboard...
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname
  );
  // ...and the browser's own prompt for closing or reloading the tab.
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = async (confirmed) => {
    if (changes.some(({ parsed }) => parsed.error)) return;
    const risky = changes
      .map(({ setting, parsed }) => ({ key: setting.key, label: setting.label, reason: riskReason(setting, parsed.value) }))
      .filter((item) => item.reason);
    if (risky.length && !confirmed) {
      setConfirmItems(risky);
      return;
    }
    setConfirmItems(null);
    setSaving(true);
    setNotice(null);
    try {
      const values = Object.fromEntries(changes.map(({ setting, parsed }) => [setting.key, parsed.value]));
      const { data: payload } = await apiClient.patch("/settings", { values, confirmed: Boolean(confirmed) });
      applyData(payload);
      setNotice({ tone: "verified", text: "Saved. The gates use the new values within a few seconds." });
    } catch (err) {
      const status = err.response?.status;
      const body = err.response?.data || {};
      if (status === 409 && body.confirmation_required) {
        setConfirmItems(body.confirmation_required);
      } else if (status === 400 && body.errors) {
        setServerErrors(body.errors);
        setNotice({ tone: "danger", text: "Some values weren't saved - see the highlighted settings." });
      } else {
        setNotice({ tone: "danger", text: "Could not save. Check your connection and try again." });
      }
    } finally {
      setSaving(false);
    }
  };

  if (loadError) {
    return (
      <div className="flex flex-col">
        <PageHeader eyebrow="System configuration" title="Settings" />
        <Notice tone="danger" className="mt-s5">
          {loadError}
        </Notice>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="flex flex-col">
        <PageHeader eyebrow="System configuration" title="Settings" />
        <p className="mt-s5 text-sm text-ink-600">Loading settings…</p>
      </div>
    );
  }

  const byKey = Object.fromEntries(data.settings.map((setting) => [setting.key, setting]));

  return (
    <>
      <SettingsContent
        data={data}
        draft={draft}
        serverErrors={serverErrors}
        saving={saving}
        notice={notice}
        onChange={(key, value) => {
          setDraft((prev) => ({ ...prev, [key]: value }));
          setServerErrors((prev) => ({ ...prev, [key]: undefined }));
          setNotice(null);
        }}
        onReset={(key) =>
          setDraft((prev) => ({ ...prev, [key]: toDraft(byKey[key], byKey[key].recommended_value) }))
        }
        onResetAll={() => setConfirmResetAll(true)}
        onDiscard={() => applyData(data)}
        onSave={() => save(false)}
      />

      {confirmResetAll && (
        <Dialog
          title="Reset every setting to its default?"
          actions={
            <>
              <button type="button" onClick={() => setConfirmResetAll(false)} className="btn-secondary">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  // Only fills in the defaults - nothing is saved until the
                  // admin reviews them and clicks "Save changes".
                  setDraft(Object.fromEntries(data.settings.map((s) => [s.key, toDraft(s, s.default)])));
                  setServerErrors({});
                  setConfirmResetAll(false);
                  setNotice({
                    tone: "prompt",
                    text: "Every setting is back to its default below. Review them, then click Save changes - or Discard to keep the current values.",
                  });
                }}
                className="btn-primary"
              >
                Reset all
              </button>
            </>
          }
        >
          <p className="text-sm leading-snug text-ink-600">
            Every setting goes back to the value the system started with - the new features (automatic deletion,
            login lockout, automatic logout, the extra alerts) go back to off. Nothing is saved until you review the
            changes and click <b>Save changes</b>.
          </p>
        </Dialog>
      )}

      {confirmItems && (
        <Dialog
          title="Save a risky change?"
          actions={
            <>
              <button type="button" onClick={() => setConfirmItems(null)} className="btn-secondary">
                Go back
              </button>
              <button type="button" onClick={() => save(true)} className="btn-primary bg-danger hover:bg-danger">
                Save anyway
              </button>
            </>
          }
        >
          <p className="text-sm leading-snug text-ink-600">
            These changes are outside the recommended settings and affect who gets through the gate:
          </p>
          <ul className="flex flex-col gap-s3">
            {confirmItems.map((item) => (
              <li key={item.key} className="flex flex-col gap-s1 rounded-sm bg-caution-tint px-s3 py-s2 text-sm">
                <b>{item.label}</b>
                <span className="leading-snug">{item.reason}</span>
              </li>
            ))}
          </ul>
        </Dialog>
      )}

      {blocker.state === "blocked" && (
        <Dialog
          title="Leave without saving?"
          actions={
            <>
              <button type="button" onClick={() => blocker.reset()} className="btn-secondary">
                Stay on this page
              </button>
              <button type="button" onClick={() => blocker.proceed()} className="btn-primary">
                Leave and discard changes
              </button>
            </>
          }
        >
          <p className="text-sm leading-snug text-ink-600">
            You have {changes.length} unsaved change{changes.length === 1 ? "" : "s"} on the Settings page. If you leave
            now, they will be lost.
          </p>
        </Dialog>
      )}
    </>
  );
}
