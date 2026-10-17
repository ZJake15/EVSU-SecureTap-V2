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
  if (setting.kind === "time" && !/^([01]\d|2[0-3]):[0-5]\d$/.test(String(raw))) {
    return { error: "Enter a time, like 06:30." };
  }
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

// "mon,tue,..." <-> the buttons that are pressed, always in Mon..Sun order.
function DayPicker({ setting, raw, onChange }) {
  const picked = new Set(String(raw || "").split(",").filter(Boolean));
  const toggle = (day) => {
    const next = new Set(picked);
    if (next.has(day)) next.delete(day);
    else next.add(day);
    onChange(setting.choices.map((choice) => choice.value).filter((value) => next.has(value)).join(","));
  };
  return (
    <span className="flex flex-wrap gap-s1" role="group" aria-label={setting.label}>
      {setting.choices.map((choice) => {
        const on = picked.has(choice.value);
        return (
          <button
            key={choice.value}
            type="button"
            aria-pressed={on}
            onClick={() => toggle(choice.value)}
            className={`h-10 w-12 rounded-sm border text-sm font-bold transition-colors ${
              on ? "border-maroon bg-maroon text-white" : "border-line bg-surface text-ink-600 hover:border-ink-400"
            }`}
          >
            {choice.label}
          </button>
        );
      })}
    </span>
  );
}

function Control({ setting, raw, onChange, invalid }) {
  if (setting.kind === "bool") return <Toggle checked={raw} onChange={onChange} label={setting.label} />;
  if (setting.kind === "days") return <DayPicker setting={setting} raw={raw} onChange={onChange} />;
  if (setting.kind === "time") {
    return (
      <input
        type="time"
        value={raw}
        onChange={(event) => onChange(event.target.value)}
        aria-label={setting.label}
        aria-invalid={invalid}
        className={`input w-[168px] font-mono ${invalid ? "border-danger focus:border-danger focus:ring-danger" : ""}`}
      />
    );
  }
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

// Each group's icon in the side menu and its header.
const SECTION_ICONS = {
  face: "user-focus",
  spoof: "shield-warning",
  quality: "image",
  nfc: "identification-card",
  enrollment: "user-plus",
  privacy: "lock-key",
  scheduling: "calendar-blank",
  security: "shield-check",
  alerts: "bell-ringing",
};

// Small headings inside a long group, shown above the setting they start with.
const SUBHEADINGS = {
  auto_delete_enabled: "Automatic deletion",
  reenroll_months: "Face re-enrollment",
  gate_hours_enabled: "Gate opening hours",
  idle_logout_enabled: "Dashboard sign-in",
  gate_sign_in_enabled: "Gate monitor sign-in",
};

function matchesQuery(setting, query) {
  return [setting.label, setting.description, setting.note].some((text) => text && text.toLowerCase().includes(query));
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
  onResetGroup = () => {},
  onSave = () => {},
  onDiscard = () => {},
  saving = false,
  notice = null,
}) {
  const [activeKey, setActiveKey] = useState(data.sections[0]?.key);
  const [search, setSearch] = useState("");
  const [moreOpen, setMoreOpen] = useState({});
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
  const dirtyIn = (sectionKey) => rows.filter((row) => row.dirty && row.setting.section === sectionKey).length;
  const dirtyGroups = data.sections.filter((section) => dirtyIn(section.key) > 0).length;

  const query = search.trim().toLowerCase();
  const matches = query ? rows.filter(({ setting }) => matchesQuery(setting, query)) : [];
  const active = data.sections.find((section) => section.key === activeKey) || data.sections[0];
  const activeRows = rows.filter((row) => row.setting.section === active.key);
  const mainRows = activeRows.filter((row) => !row.setting.more);
  const moreRows = activeRows.filter((row) => row.setting.more);
  // Open by itself when something in it is edited or wrong - never hide a change.
  const moreShown =
    moreOpen[active.key] || moreRows.some((row) => row.dirty || serverErrors[row.setting.key]);
  const groupAtDefault = activeRows.every(({ setting, raw }) => raw === toDraft(setting, setting.default));

  const renderRow = ({ setting, raw, parsed, dirty }, withSubheading = true) => {
    const dependency = setting.depends_on ? byKey[setting.depends_on] : null;
    return (
      <div key={setting.key}>
        {withSubheading && SUBHEADINGS[setting.key] && (
          <h3 className="t-eyebrow border-t border-line pb-s1 pt-s5 text-ink">{SUBHEADINGS[setting.key]}</h3>
        )}
        <SettingRow
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
      </div>
    );
  };

  const openGroup = (key) => {
    setActiveKey(key);
    setSearch("");
  };

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

      <div className="mt-s5 grid grid-cols-1 items-start gap-s5 lg:grid-cols-[248px_minmax(0,1fr)]">
        <aside className="flex min-w-0 flex-col gap-s3 lg:sticky lg:top-s5">
          <span className="relative block">
            <Icon name="magnifying-glass" size={16} className="pointer-events-none absolute left-[10px] top-3 text-ink-600" />
            <input
              type="search"
              placeholder="Search settings"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              aria-label="Search settings"
              className="input pl-9"
            />
          </span>

          {/* A drop-down on narrow screens, the side menu on wide ones. */}
          <select
            value={active.key}
            onChange={(event) => openGroup(event.target.value)}
            aria-label="Settings group"
            className="input lg:hidden"
          >
            {data.sections.map((section) => {
              const edited = dirtyIn(section.key);
              return (
                <option key={section.key} value={section.key}>
                  {section.title}
                  {edited ? ` (${edited} unsaved)` : ""}
                </option>
              );
            })}
          </select>
          <nav className="hidden flex-col gap-[2px] lg:flex" aria-label="Settings groups">
            {data.sections.map((section) => {
              const isActive = !query && section.key === active.key;
              const edited = dirtyIn(section.key);
              const found = query ? matches.filter((row) => row.setting.section === section.key).length : 0;
              return (
                <button
                  key={section.key}
                  type="button"
                  onClick={() => openGroup(section.key)}
                  aria-current={isActive ? "page" : undefined}
                  className={`flex items-center gap-s3 rounded-sm px-s3 py-s2 text-left text-sm font-bold transition-colors ${
                    isActive ? "bg-maroon text-white" : "text-ink-600 hover:bg-surface hover:text-ink"
                  } ${query && !found ? "opacity-40" : ""}`}
                >
                  <Icon name={SECTION_ICONS[section.key] || "gear"} bold size={18} className="flex-none" />
                  <span className="min-w-0 flex-1 leading-snug">{section.title}</span>
                  {query && found > 0 && (
                    <span className="rounded-full bg-canvas px-[7px] font-mono text-xs text-ink">{found}</span>
                  )}
                  {!query && edited > 0 && (
                    <span
                      className={`flex h-5 min-w-5 items-center justify-center rounded-full px-[6px] font-mono text-xs ${
                        isActive ? "bg-white text-maroon" : "bg-prompt text-white"
                      }`}
                      title={`${edited} unsaved change${edited === 1 ? "" : "s"}`}
                    >
                      {edited}
                    </span>
                  )}
                </button>
              );
            })}
          </nav>
        </aside>

        {query ? (
          <section className="card min-w-0 px-s5 pb-s2 pt-s5" aria-label="Search results">
            <div className="flex flex-col gap-s1 pb-s4">
              <h2 className="t-section">
                {matches.length} setting{matches.length === 1 ? "" : "s"} match “{search.trim()}”
              </h2>
              <p className="text-sm text-ink-600">Change them here, or open a group from the menu.</p>
            </div>
            {data.sections.map((section) => {
              const found = matches.filter((row) => row.setting.section === section.key);
              if (!found.length) return null;
              return (
                <div key={section.key}>
                  <button
                    type="button"
                    onClick={() => openGroup(section.key)}
                    className="flex w-full items-center gap-s2 border-t border-line pb-s1 pt-s5 text-left text-sm font-bold text-maroon hover:underline"
                  >
                    <Icon name={SECTION_ICONS[section.key] || "gear"} bold size={16} />
                    {section.title}
                  </button>
                  {found.map((row) => renderRow(row, false))}
                </div>
              );
            })}
            {matches.length === 0 && (
              <p className="border-t border-line py-s5 text-sm text-ink-600">
                No setting mentions that. Try a shorter word, like “delete”, “card” or “camera”.
              </p>
            )}
          </section>
        ) : (
          <section className="card min-w-0 px-s5 pb-s2 pt-s5" aria-labelledby={`settings-${active.key}`}>
            <div className="flex flex-wrap items-start gap-s4 pb-s4">
              <span className="flex h-11 w-11 flex-none items-center justify-center rounded-md bg-maroon text-white">
                <Icon name={SECTION_ICONS[active.key] || "gear"} bold size={22} />
              </span>
              <div className="flex min-w-0 flex-1 flex-col gap-s1">
                <h2 id={`settings-${active.key}`} className="t-section">
                  {active.title}
                </h2>
                <p className="text-sm text-ink-600">{active.intro}</p>
              </div>
              <button
                type="button"
                onClick={() => onResetGroup(active.key)}
                disabled={saving || groupAtDefault}
                title={groupAtDefault ? "Everything here is already at its default." : undefined}
                className="btn-secondary"
              >
                <Icon name="arrow-counter-clockwise" bold size={16} />
                Reset this group
              </button>
            </div>
            {mainRows.map((row) => renderRow(row))}
            {moreRows.length > 0 && (
              <div className="border-t border-line">
                <button
                  type="button"
                  onClick={() => setMoreOpen((prev) => ({ ...prev, [active.key]: !moreShown }))}
                  aria-expanded={Boolean(moreShown)}
                  className="flex w-full items-center gap-s2 py-s4 text-left text-sm font-bold text-ink-600 hover:text-ink"
                >
                  <Icon name={moreShown ? "caret-down" : "caret-right"} bold size={14} />
                  More options
                  <span className="font-normal">
                    · {moreRows.length} fine-tuning setting{moreRows.length === 1 ? "" : "s"} most schools never change
                  </span>
                </button>
                {moreShown && <div className="-mt-s4">{moreRows.map((row) => renderRow(row, false))}</div>}
              </div>
            )}
          </section>
        )}
      </div>

      {dirtyCount > 0 && (
        <div className="sticky bottom-s4 z-20 mt-s5 flex flex-wrap items-center gap-s3 rounded-md border border-line bg-surface px-s5 py-s3 shadow-lg">
          <Icon name="pencil-simple" bold size={18} className="text-prompt" />
          <span className="text-sm font-bold">
            {dirtyCount} unsaved change{dirtyCount === 1 ? "" : "s"}
            {dirtyGroups > 1 ? ` in ${dirtyGroups} groups` : ""}
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
        onResetGroup={(sectionKey) => {
          // Like Reset all, only fills in the defaults - nothing is saved
          // until the admin reviews them and clicks "Save changes".
          const section = data.sections.find((s) => s.key === sectionKey);
          setDraft((prev) => ({
            ...prev,
            ...Object.fromEntries(
              data.settings.filter((s) => s.section === sectionKey).map((s) => [s.key, toDraft(s, s.default)])
            ),
          }));
          setNotice({
            tone: "prompt",
            text: `${section?.title || "This group"} is back to its defaults. Review them, then click Save changes - or Discard to keep the current values.`,
          });
        }}
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
