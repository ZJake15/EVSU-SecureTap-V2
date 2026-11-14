import { useState } from "react";
import apiClient from "../api/client";
import { useOrgLists } from "../lib/orgUnits";
import { Icon, Notice, Segmented } from "./ui";

// Settings > Schools, Offices & Departments (Admin): the lists the
// registration form uses, filled from www.evsu.edu.ph. Add, rename, hide or
// show - nothing is deleted, so people already in a hidden entry keep it.
// Every change is in the Audit Log (backend users/org_views.py).

function errorText(error, fallback) {
  return error?.response?.data?.detail || fallback;
}

function EditableName({ name, onSave, muted }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name);
  if (!editing) {
    return (
      <span className={`flex min-w-0 flex-1 items-center gap-s2 ${muted ? "text-ink-400 line-through" : ""}`}>
        <span className="truncate">{name}</span>
        <button type="button" className="link-action text-xs no-underline" onClick={() => { setValue(name); setEditing(true); }}>
          Rename
        </button>
      </span>
    );
  }
  return (
    <form
      className="flex min-w-0 flex-1 items-center gap-s2"
      onSubmit={async (event) => {
        event.preventDefault();
        if (await onSave(value)) setEditing(false);
      }}
    >
      <input className="input h-8" value={value} maxLength={150} autoFocus onChange={(e) => setValue(e.target.value)} />
      <button type="submit" className="btn-secondary btn-sm">Save</button>
      <button type="button" className="btn-ghost btn-sm" onClick={() => setEditing(false)}>Cancel</button>
    </form>
  );
}

function HideButton({ active, onToggle }) {
  return (
    <button type="button" className={active ? "link-danger text-xs" : "link-action text-xs"} onClick={onToggle}>
      {active ? "Hide" : "Show again"}
    </button>
  );
}

export default function OrgUnitsPanel() {
  const { org, setOrg, error: loadError } = useOrgLists();
  const [error, setError] = useState("");
  const [open, setOpen] = useState(() => new Set());
  const [kind, setKind] = useState("school");
  const [newName, setNewName] = useState("");
  const [newGroup, setNewGroup] = useState("");
  const [newDept, setNewDept] = useState({});

  const call = async (request) => {
    setError("");
    try {
      const { data } = await request();
      setOrg(data);
      return true;
    } catch (err) {
      setError(errorText(err, "Couldn't save the change."));
      return false;
    }
  };

  const groups = [...new Set(org.units.filter((u) => u.kind === "office").map((u) => u.group).filter(Boolean))];
  const sections = [
    ["Schools", org.units.filter((u) => u.kind === "school")],
    ...groups.map((group) => [`Offices · ${group}`, org.units.filter((u) => u.kind === "office" && u.group === group)]),
    ["Offices · Other", org.units.filter((u) => u.kind === "office" && !u.group)],
  ].filter(([, units]) => units.length > 0);

  const toggleOpen = (id) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex flex-col gap-s5 border-t border-line pb-s5 pt-s5">
      {(loadError || error) && <Notice tone="danger">{loadError || error}</Notice>}

      <form
        className="flex flex-wrap items-end gap-s3 rounded-md border border-line bg-canvas p-s4"
        onSubmit={async (event) => {
          event.preventDefault();
          const done = await call(() => apiClient.post("/org-units", { name: newName, kind, group: kind === "office" ? newGroup : "" }));
          if (done) setNewName("");
        }}
      >
        <div className="flex flex-col gap-s2">
          <span className="field-label">Add a</span>
          <Segmented
            value={kind}
            onChange={setKind}
            options={[
              { value: "school", label: "School" },
              { value: "office", label: "Office" },
            ]}
          />
        </div>
        <label className="flex min-w-[240px] flex-1 flex-col gap-s2">
          <span className="field-label">Name</span>
          <input className="input" required maxLength={150} value={newName} onChange={(e) => setNewName(e.target.value)}
                 placeholder={kind === "school" ? "e.g. School of Nursing" : "e.g. Campus Ministry Office"} />
        </label>
        {kind === "office" && (
          <label className="flex min-w-[220px] flex-col gap-s2">
            <span className="field-label">Listed under</span>
            <input className="input" list="org-groups" maxLength={150} value={newGroup}
                   onChange={(e) => setNewGroup(e.target.value)} placeholder="e.g. Academic Affairs" />
            <datalist id="org-groups">
              {groups.map((group) => <option key={group} value={group} />)}
            </datalist>
          </label>
        )}
        <button type="submit" className="btn-secondary">
          <Icon name="plus" bold /> Add
        </button>
      </form>

      {sections.map(([title, units]) => (
        <div key={title} className="flex flex-col gap-s2">
          <span className="t-eyebrow text-ink">{title}</span>
          <div className="overflow-hidden rounded-md border border-line">
            {units.map((unit) => {
              const isOpen = open.has(unit.id);
              return (
                <div key={unit.id} className="border-b border-line last:border-b-0">
                  <div className="flex min-h-11 items-center gap-s3 px-s4 py-s2 text-sm">
                    {unit.kind === "school" ? (
                      <button type="button" onClick={() => toggleOpen(unit.id)} aria-expanded={isOpen}
                              className="flex h-6 w-6 items-center justify-center text-ink-600" title="Departments">
                        <Icon name={isOpen ? "caret-down" : "caret-right"} bold size={14} />
                      </button>
                    ) : (
                      <span className="w-6" />
                    )}
                    <EditableName name={unit.name} muted={!unit.is_active}
                                  onSave={(name) => call(() => apiClient.patch(`/org-units/${unit.id}`, { name }))} />
                    {unit.kind === "school" && (
                      <span className="text-xs text-ink-600">
                        {unit.departments.filter((d) => d.is_active).length} departments
                      </span>
                    )}
                    <span className="w-[86px] text-right font-mono text-xs text-ink-600">{unit.people} people</span>
                    <HideButton active={unit.is_active}
                                onToggle={() => call(() => apiClient.patch(`/org-units/${unit.id}`, { is_active: !unit.is_active }))} />
                  </div>
                  {isOpen && (
                    <div className="flex flex-col gap-s1 border-t border-line bg-canvas px-s4 py-s3 pl-[52px] text-sm">
                      {unit.departments.map((dept) => (
                        <div key={dept.id} className="flex min-h-8 items-center gap-s3">
                          <EditableName name={dept.name} muted={!dept.is_active}
                                        onSave={(name) => call(() => apiClient.patch(`/departments/${dept.id}`, { name }))} />
                          <HideButton active={dept.is_active}
                                      onToggle={() => call(() => apiClient.patch(`/departments/${dept.id}`, { is_active: !dept.is_active }))} />
                        </div>
                      ))}
                      <form
                        className="mt-s2 flex items-center gap-s2"
                        onSubmit={async (event) => {
                          event.preventDefault();
                          const done = await call(() => apiClient.post(`/org-units/${unit.id}/departments`, { name: newDept[unit.id] || "" }));
                          if (done) setNewDept((prev) => ({ ...prev, [unit.id]: "" }));
                        }}
                      >
                        <input className="input h-8 max-w-[360px]" maxLength={150} placeholder="New department"
                               value={newDept[unit.id] || ""}
                               onChange={(e) => setNewDept((prev) => ({ ...prev, [unit.id]: e.target.value }))} />
                        <button type="submit" className="btn-secondary btn-sm">
                          <Icon name="plus" bold /> Add department
                        </button>
                      </form>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
