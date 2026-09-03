import { useEffect, useState } from "react";
import apiClient from "../api/client";

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

function extractErrorMessage(err) {
  const data = err.response?.data;
  if (!data) return "Could not reach the server. Check your connection and try again.";
  if (typeof data.detail === "string") return data.detail;
  const [field, messages] = Object.entries(data)[0] || [];
  const text = Array.isArray(messages) ? messages[0] : messages;
  if (!text) return "Could not save this account. Check the fields and try again.";
  return field ? `${field}: ${text}` : text;
}

const inputClass =
  "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition-colors placeholder:text-ink-400 focus:border-maroon focus:outline-none focus:ring-1 focus:ring-maroon";

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-500">{label}</span>
      {children}
    </label>
  );
}

export default function AccountManagement() {
  const [accounts, setAccounts] = useState([]);
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState(null);
  const [formError, setFormError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const loadAccounts = () => {
    apiClient
      .get("/accounts/", { params: { page_size: 100 } })
      .then(({ data }) => setAccounts(data.results || data))
      .catch(() => setLoadError("Could not load accounts. Try refreshing the page."));
  };

  useEffect(loadAccounts, []);

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

  const handleEdit = (account) => {
    setEditingId(account.id);
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
    <div className="space-y-8">
      <div>
        <h1 className="font-display text-2xl font-semibold text-ink-900">Account Management</h1>
        <p className="mt-1 text-sm text-ink-500">
          Create and manage SASO and Security Officer dashboard accounts, and assign their roles.
        </p>
      </div>

      {loadError && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{loadError}</p>}

      <form onSubmit={handleSubmit} className="space-y-5 rounded-xl border border-ink-900/10 bg-white p-6 shadow-sm">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Username">
            <input
              required
              disabled={Boolean(editingId)}
              value={form.username}
              onChange={handleChange("username")}
              className={`${inputClass} disabled:bg-parchment-100 disabled:text-ink-400`}
            />
          </Field>
          <Field label="First name">
            <input value={form.first_name} onChange={handleChange("first_name")} className={inputClass} />
          </Field>
          <Field label="Last name">
            <input value={form.last_name} onChange={handleChange("last_name")} className={inputClass} />
          </Field>
          <Field label="Role">
            <select value={form.role} onChange={handleChange("role")} className={inputClass}>
              <option value="admin">Admin</option>
              <option value="saso">Security Manager (SASO)</option>
              <option value="security_officer">Security Officer</option>
            </select>
          </Field>
          {form.role === "security_officer" && (
            <Field label="Assigned gate">
              <input
                required
                placeholder="Main Gate"
                value={form.assigned_gate_location}
                onChange={handleChange("assigned_gate_location")}
                className={inputClass}
              />
            </Field>
          )}
          <Field label={editingId ? "New password (optional)" : "Password"}>
            <input
              type="password"
              required={!editingId}
              placeholder={editingId ? "Leave blank to keep current password" : ""}
              value={form.password}
              onChange={handleChange("password")}
              className={inputClass}
            />
          </Field>
        </div>

        {formError && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{formError}</p>}

        <div className="flex gap-2 border-t border-ink-900/10 pt-4">
          <button
            type="submit"
            disabled={isSubmitting}
            className="rounded-lg bg-maroon px-5 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-maroon-600 disabled:opacity-50"
          >
            {editingId ? "Save changes" : "Create account"}
          </button>
          {editingId && (
            <button
              type="button"
              onClick={resetForm}
              className="rounded-lg border border-ink-200 px-5 py-2 text-sm font-medium text-ink-700 transition-colors hover:bg-parchment-100"
            >
              Cancel edit
            </button>
          )}
        </div>
      </form>

      <div className="overflow-x-auto rounded-xl border border-ink-900/10 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-ink-900/10 text-sm">
          <thead className="bg-parchment-100">
            <tr>
              {["Username", "Name", "Role", "Gate", "Status", ""].map((h) => (
                <th
                  key={h}
                  className="border-b-2 border-maroon/20 px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-ink-500"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-900/5">
            {accounts.map((account) => (
              <tr key={account.id} className="transition-colors hover:bg-parchment-50">
                <td className="px-4 py-2.5 font-medium text-ink-900">{account.username}</td>
                <td className="px-4 py-2.5 text-ink-700">{account.full_name || "—"}</td>
                <td className="px-4 py-2.5 text-ink-700">{ROLE_LABELS[account.role] || account.role}</td>
                <td className="px-4 py-2.5 text-ink-700">{account.assigned_gate_location || "—"}</td>
                <td className="px-4 py-2.5">
                  <span
                    className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${
                      account.is_active
                        ? "border border-emerald-200 bg-emerald-50 text-emerald-800"
                        : "border border-ink-200 bg-ink-50 text-ink-500"
                    }`}
                  >
                    {account.is_active ? "Active" : "Inactive"}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right">
                  <button onClick={() => handleEdit(account)} className="mr-3 text-sm font-medium text-maroon hover:underline">
                    Edit
                  </button>
                  {account.is_active ? (
                    <button onClick={() => handleDeactivate(account)} className="text-sm font-medium text-red-600 hover:underline">
                      Deactivate
                    </button>
                  ) : (
                    <button onClick={() => handleReactivate(account)} className="text-sm font-medium text-emerald-700 hover:underline">
                      Reactivate
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {accounts.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-ink-400">
                  No accounts yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
