import { useEffect, useRef, useState } from "react";
import apiClient from "../api/client";
import { Field, Icon, Notice, PageHeader } from "../components/ui";

const emptyForm = {
  username: "",
  first_name: "",
  last_name: "",
  role: "security_officer",
  assigned_gate_location: "",
  password: "",
};

const ROLE_LABELS = {
  admin: "Admin",
  saso: "Security Manager (SASO)",
  security_officer: "Security Officer",
};

const COLS = "minmax(0,1.2fr) minmax(0,1.4fr) minmax(0,1.3fr) minmax(0,96px) 96px";

// How long the form keeps its red outline after "New account" is clicked.
const FORM_HIGHLIGHT_MS = 3000;

function extractErrorMessage(err) {
  const data = err.response?.data;
  if (!data) return "Could not reach the server. Check your connection and try again.";
  if (typeof data.detail === "string") return data.detail;
  const [field, messages] = Object.entries(data)[0] || [];
  const text = Array.isArray(messages) ? messages[0] : messages;
  if (!text) return "Could not save this account. Check the fields and try again.";
  return field ? `${field}: ${text}` : text;
}

export default function AccountManagement() {
  const [accounts, setAccounts] = useState([]);
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState(null);
  const [formError, setFormError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  // Bumped on every "New account" click, so a second click re-runs the
  // highlight even while the first one is still showing. 0 = no highlight.
  const [highlightKey, setHighlightKey] = useState(0);
  const formRef = useRef(null);
  const usernameRef = useRef(null);

  const editingAccount = editingId ? accounts.find((account) => account.id === editingId) : null;

  const loadAccounts = () => {
    apiClient
      .get("/accounts/", { params: { page_size: 100 } })
      .then(({ data }) => setAccounts(data.results || data))
      .catch(() => setLoadError("Could not load accounts. Try refreshing the page."));
  };

  useEffect(loadAccounts, []);

  // Runs after the reset form has rendered, so the Username field is
  // enabled again by the time it's focused (it's disabled while editing).
  useEffect(() => {
    if (!highlightKey) return undefined;
    usernameRef.current?.focus({ preventScroll: true });
    const timer = setTimeout(() => setHighlightKey(0), FORM_HIGHLIGHT_MS);
    return () => clearTimeout(timer);
  }, [highlightKey]);

  const handleChange = (field) => (event) => {
    const value = event.target.value;
    setForm((prev) => ({
      ...prev,
      [field]: value,
      // A gate only means anything for a Security Officer - clear it the
      // moment a different role is picked so a stale gate can't linger on
      // an account that's no longer scoped by one.
      ...(field === "role" && value !== "security_officer" ? { assigned_gate_location: "" } : {}),
    }));
  };

  const resetForm = () => {
    setForm(emptyForm);
    setEditingId(null);
    setFormError("");
  };

  // "New account": clear the form, bring it into view (on a narrow screen
  // it sits below the whole account list) and outline it in red for a
  // moment so it's obvious where to type.
  const startNewAccount = () => {
    resetForm();
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    formRef.current?.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
    setHighlightKey((key) => key + 1);
  };

  const handleEdit = (account) => {
    setHighlightKey(0);
    setEditingId(account.id);
    setFormError("");
    setForm({
      username: account.username,
      first_name: account.first_name,
      last_name: account.last_name,
      role: account.role,
      assigned_gate_location: account.assigned_gate_location || "",
      password: "",
    });
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setFormError("");
    setIsSubmitting(true);
    try {
      const payload = { ...form };
      if (!payload.password) delete payload.password; // optional on edit
      if (editingId) {
        await apiClient.patch(`/accounts/${editingId}/`, payload);
      } else {
        await apiClient.post("/accounts/", payload);
      }
      resetForm();
      loadAccounts();
    } catch (err) {
      setFormError(extractErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeactivate = async (account) => {
    const confirmed = window.confirm(`Deactivate the account "${account.username}"? They will no longer be able to log in.`);
    if (!confirmed) return;
    await apiClient.delete(`/accounts/${account.id}/`);
    loadAccounts();
  };

  const handleReactivate = async (account) => {
    await apiClient.patch(`/accounts/${account.id}/`, { is_active: true });
    loadAccounts();
  };

  return (
    <div className="flex flex-col">
      <PageHeader eyebrow="Dashboard sign-ins" title="Accounts">
        <button type="button" onClick={startNewAccount} className="btn-primary">
          <Icon name="plus" bold size={16} />
          New account
        </button>
      </PageHeader>

      {loadError && (
        <Notice tone="danger" className="mt-s4">
          {loadError}
        </Notice>
      )}

      <div className="mt-s6 grid grid-cols-1 items-start gap-s6 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="card overflow-hidden">
          <div className="overflow-x-auto">
            <div className="min-w-[620px]">
              <div className="table-head grid h-9 items-center gap-s3 border-b border-line px-s4" style={{ gridTemplateColumns: COLS }}>
                <span>Username</span>
                <span>Name</span>
                <span>Role</span>
                <span>Gate</span>
                <span>Status</span>
              </div>
              {accounts.map((account) => {
                const selected = account.id === editingId;
                return (
                  <button
                    key={account.id}
                    type="button"
                    onClick={() => handleEdit(account)}
                    title={`Edit ${account.username}`}
                    className={`grid h-[52px] w-full items-center gap-s3 border-b border-l-[3px] border-b-line pl-[13px] pr-s4 text-left text-sm transition-colors ${
                      selected ? "border-l-maroon bg-canvas" : "border-l-transparent hover:bg-canvas"
                    }`}
                    style={{ gridTemplateColumns: COLS }}
                  >
                    <span className="truncate font-mono font-semibold">{account.username}</span>
                    <span className="truncate font-bold">{account.full_name || "—"}</span>
                    <span className="truncate">{ROLE_LABELS[account.role] || account.role}</span>
                    <span className="truncate text-ink-600">{account.assigned_gate_location || "—"}</span>
                    {account.is_active ? (
                      <span className="flex items-center gap-s1 font-bold text-verified">
                        <Icon name="check-circle" bold size={14} />
                        Active
                      </span>
                    ) : (
                      <span className="flex items-center gap-s1 font-bold text-ink-600">
                        <Icon name="minus-circle" bold size={14} />
                        Inactive
                      </span>
                    )}
                  </button>
                );
              })}
              {accounts.length === 0 && <p className="px-s4 py-s5 text-sm text-ink-600">No accounts yet.</p>}
            </div>
          </div>
        </div>

        <form
          ref={formRef}
          onSubmit={handleSubmit}
          className={`card flex scroll-mt-s6 flex-col gap-s4 p-s5 outline outline-2 outline-offset-4 transition-[outline-color] duration-500 ${
            highlightKey ? "outline-danger" : "outline-transparent"
          }`}
        >
          <div className="flex flex-col gap-s1">
            <span className="t-eyebrow">{editingId ? "Edit account" : "New account"}</span>
            <span className="t-section">{editingId ? form.username : "Create a dashboard sign-in"}</span>
          </div>

          <Field label="Username">
            <input
              ref={usernameRef}
              required
              disabled={Boolean(editingId)}
              value={form.username}
              onChange={handleChange("username")}
              className="input font-mono"
            />
          </Field>
          <div className="grid grid-cols-2 gap-s4">
            <Field label="First name">
              <input value={form.first_name} onChange={handleChange("first_name")} className="input" />
            </Field>
            <Field label="Last name">
              <input value={form.last_name} onChange={handleChange("last_name")} className="input" />
            </Field>
          </div>
          <Field label={editingId ? "New password" : "Password"}>
            <input
              type="password"
              required={!editingId}
              placeholder={editingId ? "Leave blank to keep current password" : "At least 10 characters"}
              value={form.password}
              onChange={handleChange("password")}
              className="input"
            />
          </Field>

          <div className="flex flex-col gap-s2">
            <span className="field-label">Role</span>
            <div role="radiogroup" className="flex flex-col rounded-sm border border-line text-sm">
              {Object.entries(ROLE_LABELS).map(([value, label], index) => (
                <label
                  key={value}
                  className={`flex h-10 cursor-pointer items-center gap-s3 px-s3 ${index > 0 ? "border-t border-line" : ""} ${
                    form.role === value ? "font-bold" : ""
                  }`}
                >
                  <input
                    type="radio"
                    name="role"
                    value={value}
                    checked={form.role === value}
                    onChange={handleChange("role")}
                    className="h-4 w-4 accent-maroon"
                  />
                  {label}
                </label>
              ))}
            </div>
          </div>

          {form.role === "security_officer" && (
            <Field label="Assigned gate" hint="Security Officers only - they see this gate's activity, today only.">
              <input
                required
                placeholder="Main Gate"
                value={form.assigned_gate_location}
                onChange={handleChange("assigned_gate_location")}
                className="input"
              />
            </Field>
          )}

          {formError && <Notice tone="danger">{formError}</Notice>}

          <div className="flex flex-wrap items-center gap-s3 pt-s3">
            <button type="submit" disabled={isSubmitting} className="btn-primary">
              {editingId ? "Save changes" : "Create account"}
            </button>
            {editingId && (
              <button type="button" onClick={resetForm} className="btn-ghost">
                Cancel
              </button>
            )}
            <span className="flex-1" />
            {editingAccount &&
              (editingAccount.is_active ? (
                <button type="button" onClick={() => handleDeactivate(editingAccount)} className="link-danger">
                  Deactivate account
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => handleReactivate(editingAccount)}
                  className="text-sm font-bold text-verified hover:underline"
                >
                  Reactivate account
                </button>
              ))}
          </div>
        </form>
      </div>
    </div>
  );
}
