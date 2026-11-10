import { useCallback, useEffect, useState } from "react";
import apiClient from "../api/client";
import { Field, Icon, Notice, Segmented } from "./ui";

// Settings > Semester Enrollment: this semester's enrolled-students list (the
// registrar's file). Admin and SASO. A registered student who isn't on it is
// flagged NOT ENROLLED, or refused - as the settings above it say. Staff are
// never flagged. See backend users/semesters.py.

function errorText(error, fallback) {
  return error?.response?.data?.detail || fallback;
}

function dateTime(value) {
  return value
    ? new Date(value).toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })
    : "";
}

function Stat({ label, value, tone = "", hint }) {
  return (
    <div className="flex min-w-[150px] flex-col gap-s2 border-l border-line px-s5">
      <span className="t-eyebrow">{label}</span>
      <span className={`t-stat ${tone}`}>{value}</span>
      {hint && <span className="text-xs text-ink-600">{hint}</span>}
    </div>
  );
}

// Choose the file -> see its columns and first rows -> pick the student ID
// column -> confirm. Used to start a semester and to update its list.
function ListUpload({ purpose, onDone, onCancel }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [column, setColumn] = useState("");
  const [name, setName] = useState("");
  const [mode, setMode] = useState("add");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const starting = purpose === "start";

  const choose = async (event) => {
    const chosen = event.target.files?.[0] || null;
    setFile(chosen);
    setPreview(null);
    setError("");
    if (!chosen) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", chosen);
      const { data } = await apiClient.post("/semester/preview", form);
      setPreview(data);
      setColumn(data.suggested_column);
    } catch (err) {
      setError(errorText(err, "Couldn't read that file."));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (starting && !name.trim()) {
      setError('Name the semester, e.g. "1st Semester 2026-2027".');
      return;
    }
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("column", column);
      if (starting) form.append("name", name.trim());
      else form.append("mode", mode);
      const { data } = await apiClient.post(starting ? "/semester/start" : "/semester/upload", form);
      onDone(data);
    } catch (err) {
      setError(errorText(err, "Couldn't save the list."));
      setBusy(false);
    }
  };

  const matched = preview ? preview.matches[column] || 0 : 0;

  return (
    <div className="flex flex-col gap-s5 rounded-md border border-line bg-canvas p-s5">
      <div className="flex items-start justify-between gap-s4">
        <div className="flex flex-col gap-s1">
          <span className="text-base font-bold">{starting ? "Start a new semester" : "Upload an updated list"}</span>
          <span className="text-sm text-ink-600">
            {starting
              ? "The registrar's list of students enrolled this semester (CSV or Excel). The last semester's list stops counting."
              : "A newer list from the registrar - for students who enrolled late."}
          </span>
        </div>
        {onCancel && (
          <button type="button" className="btn-ghost btn-sm" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>

      {starting && (
        <Field label="Semester name">
          <input
            className="input max-w-[420px]"
            placeholder="e.g. 1st Semester 2026-2027"
            value={name}
            maxLength={100}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
      )}

      <Field label="Enrolled students list" hint="CSV (any columns) or Excel. Nothing is saved until you confirm.">
        <input type="file" accept=".csv,.xlsx,.xls,text/csv" className="file-input" onChange={choose} disabled={busy} />
      </Field>

      {preview && (
        <>
          <Field
            label="Which column is the student ID number?"
            hint={`${preview.total_rows.toLocaleString()} rows in the file. The number after each column is how many of its IDs belong to the ${preview.registered_students.toLocaleString()} students registered in SecureTap.`}
          >
            <select className="input max-w-[420px]" value={column} onChange={(event) => setColumn(event.target.value)}>
              {preview.columns.map((name) => (
                <option key={name} value={name}>
                  {name} — {(preview.matches[name] || 0).toLocaleString()} match
                </option>
              ))}
            </select>
          </Field>

          <div className="overflow-x-auto rounded-sm border border-line bg-surface">
            <table className="min-w-full text-left text-sm">
              <thead className="table-head bg-canvas">
                <tr>
                  {preview.columns.map((name) => (
                    <th key={name} className={`whitespace-nowrap px-s3 py-s2 ${name === column ? "text-maroon" : ""}`}>
                      {name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row, index) => (
                  <tr key={index} className="border-t border-line">
                    {preview.columns.map((name) => (
                      <td
                        key={name}
                        className={`whitespace-nowrap px-s3 py-s2 ${name === column ? "bg-prompt-tint font-mono font-medium" : "text-ink-600"}`}
                      >
                        {row[name]}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {matched === 0 && preview.registered_students > 0 && (
            <Notice tone="caution">
              None of the IDs in <b>{column}</b> match a registered student. Check it&apos;s the student ID column, and
              that the IDs are written the same way as in SecureTap (e.g. 2023-11014).
            </Notice>
          )}

          {!starting && (
            <div className="flex flex-col gap-s2">
              <span className="field-label">What to do with it</span>
              <Segmented
                className="max-w-[520px]"
                value={mode}
                onChange={setMode}
                options={[
                  { value: "add", label: "Add to the list", icon: "plus" },
                  { value: "replace", label: "Replace the list", icon: "arrows-clockwise" },
                ]}
              />
              <span className="text-xs text-ink-600">
                {mode === "add"
                  ? "Students in the file are added; nobody is taken off."
                  : "The list becomes exactly this file. Students you added by hand are kept."}
              </span>
            </div>
          )}
        </>
      )}

      {error && <Notice tone="danger">{error}</Notice>}

      <div className="flex gap-s3">
        <button type="button" className="btn-primary" onClick={confirm} disabled={busy || !preview || !column}>
          <Icon name={starting ? "graduation-cap" : "upload-simple"} bold />
          {busy ? "Working…" : starting ? "Start the semester" : "Save the list"}
        </button>
      </div>
    </div>
  );
}

// Registered students not on the list - tick the late enrollees.
function NotOnList({ refreshKey, onChanged, refusing }) {
  const [search, setSearch] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState("");

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const { data } = await apiClient.get("/semester/not-on-list", { params: { search } });
        if (!cancelled) {
          setData(data);
          setError("");
        }
      } catch (err) {
        if (!cancelled) setError(errorText(err, "Couldn't load the students."));
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [search, refreshKey]);

  const markEnrolled = async (student) => {
    setWorking(student.student_or_employee_id);
    try {
      await apiClient.post("/semester/by-hand", { student_ids: [student.student_or_employee_id] });
      onChanged();
    } catch (err) {
      setError(errorText(err, "Couldn't add the student."));
    } finally {
      setWorking("");
    }
  };

  return (
    <div className="flex flex-col gap-s3">
      <div className="flex flex-wrap items-end justify-between gap-s3">
        <div className="flex flex-col gap-s1">
          <span className="text-base font-bold">Registered, not on the list</span>
          <span className="text-sm text-ink-600">
            These students are {refusing ? "refused" : "flagged NOT ENROLLED"} at the gate. Enrolled late? Mark them
            enrolled.
          </span>
        </div>
        <span className="relative block w-full max-w-[300px]">
          <Icon name="magnifying-glass" size={16} className="pointer-events-none absolute left-[10px] top-3 text-ink-600" />
          <input
            className="input pl-9"
            placeholder="Search by name or ID"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </span>
      </div>
      {error && <Notice tone="danger">{error}</Notice>}
      <div className="overflow-hidden rounded-md border border-line">
        {data?.results.length ? (
          data.results.map((student) => (
            <div key={student.id} className="flex min-h-11 items-center gap-s4 border-b border-line px-s4 py-s2 text-sm last:border-b-0">
              <span className="min-w-0 flex-1 truncate font-bold">{student.full_name}</span>
              <span className="w-[130px] truncate font-mono font-medium">{student.student_or_employee_id}</span>
              <span className="hidden min-w-0 flex-1 truncate text-ink-600 md:block">{student.department_or_course}</span>
              <button
                type="button"
                className="btn-secondary btn-sm"
                onClick={() => markEnrolled(student)}
                disabled={working === student.student_or_employee_id}
              >
                <Icon name="check" bold />
                Mark enrolled
              </button>
            </div>
          ))
        ) : (
          <p className="px-s4 py-s5 text-sm text-ink-600">
            {data ? (search ? "No match." : "Every registered student is on the list.") : "Loading…"}
          </p>
        )}
      </div>
      {data && data.total > data.results.length && (
        <span className="text-xs text-ink-600">
          Showing {data.results.length} of {data.total.toLocaleString()} - search to find someone.
        </span>
      )}
    </div>
  );
}

// What the settings above make of the list right now, in one line.
function EffectNotice({ current }) {
  if (!current.check_enabled) {
    return (
      <Notice tone="neutral">
        <b>Check semester enrollment</b> is switched off, so nobody is flagged. The list is kept.
      </Notice>
    );
  }
  if (current.grace_until && new Date(current.grace_until) > new Date()) {
    return (
      <Notice tone="prompt">
        Grace period: nobody is flagged until <b>{dateTime(current.grace_until)}</b>.
      </Notice>
    );
  }
  return null;
}

export default function SemesterPanel() {
  const [state, setState] = useState(null);
  const [error, setError] = useState("");
  const [panel, setPanel] = useState(null); // "start" | "update" | null
  const [message, setMessage] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const load = useCallback(async () => {
    try {
      const { data } = await apiClient.get("/semester");
      setState(data);
      setError("");
    } catch (err) {
      setError(errorText(err, "Couldn't load the semester."));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const refresh = () => setRefreshKey((key) => key + 1);
  const current = state?.current;

  const done = (purpose) => (data) => {
    setPanel(null);
    setMessage(
      purpose === "start"
        ? `${data.name} started - ${data.on_list.toLocaleString()} students on its list.`
        : `List saved - ${data.added.toLocaleString()} added${data.removed ? `, ${data.removed.toLocaleString()} taken off` : ""}.`
    );
    refresh();
  };

  return (
    <div className="flex flex-col gap-s5 border-t border-line pb-s5 pt-s5">
      <div className="flex flex-wrap items-end justify-between gap-s3">
        <h3 className="t-eyebrow text-ink">This semester&apos;s enrolled list</h3>
        {current && !panel && (
          <div className="flex flex-wrap gap-s3">
            <button type="button" className="btn-secondary" onClick={() => setPanel("update")}>
              <Icon name="upload-simple" bold />
              Upload updated list
            </button>
            <button type="button" className="btn-secondary" onClick={() => setPanel("start")}>
              <Icon name="graduation-cap" bold />
              Start new semester
            </button>
          </div>
        )}
      </div>

      {error && <Notice tone="danger">{error}</Notice>}
      {message && <Notice tone="verified">{message}</Notice>}

      {state && !current && (
        <>
          <Notice tone="neutral">
            No semester has been started yet, so nobody is flagged. Start one with the registrar&apos;s list of enrolled
            students.
          </Notice>
          <ListUpload purpose="start" onDone={done("start")} />
        </>
      )}

      {panel && current && <ListUpload purpose={panel} onDone={done(panel)} onCancel={() => setPanel(null)} />}

      {current && (
        <>
          <div className="flex flex-col gap-s4">
            <div className="flex flex-col gap-s1">
              <span className="font-display stretch-semi text-[24px] font-extrabold leading-tight">{current.name}</span>
              <span className="text-sm text-ink-600">
                Started {dateTime(current.started_at)}
                {current.started_by ? ` by ${current.started_by}` : ""} · list last changed {dateTime(current.updated_at)}
              </span>
            </div>
            <EffectNotice current={current} />
            <div className="-ml-s5 flex flex-wrap gap-y-s4">
              <Stat
                label="On the list"
                value={current.on_list.toLocaleString()}
                hint={current.added_by_hand ? `${current.added_by_hand} added by hand` : null}
              />
              <Stat
                label="Registered students on it"
                value={`${current.registered_on_list.toLocaleString()} of ${current.students_registered.toLocaleString()}`}
                tone="text-verified"
              />
              <Stat
                label={current.action === "refuse" ? "Refused at the gate" : "Flagged not enrolled"}
                value={current.registered_not_on_list.toLocaleString()}
                tone={current.registered_not_on_list ? (current.action === "refuse" ? "text-danger" : "text-caution") : ""}
                hint="registered, not on the list"
              />
              <Stat
                label="On the list, not registered"
                value={current.list_not_registered.toLocaleString()}
                hint="haven't enrolled their face yet"
              />
            </div>
            {current.on_list > 0 && current.registered_on_list === 0 && current.students_registered > 0 && (
              <Notice tone="caution">
                None of the registered students are on this list - the wrong column may have been picked. Upload it
                again with &quot;Replace the list&quot; and choose the student ID column.
              </Notice>
            )}
          </div>

          <NotOnList refreshKey={refreshKey} onChanged={refresh} refusing={current.action === "refuse"} />

          {state.past.length > 0 && (
            <div className="flex flex-col gap-s3">
              <span className="text-base font-bold">Earlier semesters</span>
              <div className="overflow-hidden rounded-md border border-line">
                {state.past.map((item) => (
                  <div key={item.id} className="flex items-center gap-s4 border-b border-line px-s4 py-s3 text-sm last:border-b-0">
                    <span className="flex-1 font-bold">{item.name}</span>
                    <span className="font-mono text-ink-600">{item.on_list.toLocaleString()} on the list</span>
                    <span className="text-ink-600">started {dateTime(item.started_at)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
