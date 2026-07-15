import { useEffect, useState } from "react";
import apiClient from "../api/client";
import WebcamCapture from "../components/WebcamCapture";

const emptyForm = {
  full_name: "",
  role: "student",
  student_or_employee_id: "",
  nfc_id: "",
  department_or_course: "",
  photo: null,
};

// DRF field validation errors come back as {"photo": ["reason"], ...} - not
// {"detail": "..."} - so pull out the first field's actual message instead
// of falling back to a generic one that hides the real reason.
function extractErrorMessage(err) {
  const data = err.response?.data;
  if (!data) return "Could not reach the server. Check your connection and try again.";
  if (typeof data.detail === "string") return data.detail;

  const [field, messages] = Object.entries(data)[0] || [];
  const text = Array.isArray(messages) ? messages[0] : messages;
  if (!text) return "Could not save this user. Check the fields and try again.";
  return field && field !== "photo" && field !== "non_field_errors" ? `${field}: ${text}` : text;
}

export default function Users() {
  const [people, setPeople] = useState([]);
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState(null);
  const [formError, setFormError] = useState("");
  const [photoPreviewUrl, setPhotoPreviewUrl] = useState(null);
  const [bulkFile, setBulkFile] = useState(null);
  const [bulkReport, setBulkReport] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const loadPeople = () => {
    apiClient.get("/users/", { params: { page_size: 100 } }).then(({ data }) => {
      setPeople(data.results || data);
    });
  };

  useEffect(loadPeople, []);

  const handleChange = (field) => (event) => {
    setForm((prev) => ({ ...prev, [field]: event.target.value }));
  };

  const setPhoto = (file) => {
    setForm((prev) => ({ ...prev, photo: file }));
    setPhotoPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return file ? URL.createObjectURL(file) : null;
    });
  };

  const handlePhotoChange = (event) => {
    setPhoto(event.target.files?.[0] || null);
  };

  const clearPhoto = () => setPhoto(null);

  const resetForm = () => {
    setForm(emptyForm);
    setEditingId(null);
    setPhotoPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setFormError("");
    setIsSubmitting(true);

    const payload = new FormData();
    Object.entries(form).forEach(([key, value]) => {
      if (value !== null && value !== "") payload.append(key, value);
    });

    try {
      if (editingId) {
        await apiClient.put(`/users/${editingId}/`, payload, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      } else {
        await apiClient.post("/users/", payload, {
          headers: { "Content-Type": "multipart/form-data" },
        });
      }
      resetForm();
      loadPeople();
    } catch (err) {
      setFormError(extractErrorMessage(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleEdit = (person) => {
    setEditingId(person.id);
    setForm({
      full_name: person.full_name,
      role: person.role,
      student_or_employee_id: person.student_or_employee_id,
      nfc_id: person.nfc_id,
      department_or_course: person.department_or_course,
      photo: null,
    });
  };

  const handleDeactivate = async (person) => {
    await apiClient.delete(`/users/${person.id}/`);
    loadPeople();
  };

  const handleReactivate = async (person) => {
    await apiClient.patch(`/users/${person.id}/`, { is_active: true });
    loadPeople();
  };

  const handleBulkImport = async (event) => {
    event.preventDefault();
    if (!bulkFile) return;
    const payload = new FormData();
    payload.append("file", bulkFile);
    setBulkReport(null);
    try {
      const { data } = await apiClient.post("/users/bulk-import", payload, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setBulkReport(data);
      loadPeople();
    } catch (err) {
      setBulkReport(err.response?.data || { detail: "Bulk import failed." });
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">User Management</h1>
        <p className="text-sm text-gray-500">Register, edit, and deactivate students and staff.</p>
      </div>

      <form
        onSubmit={handleSubmit}
        className="grid grid-cols-1 gap-3 rounded border border-gray-200 bg-white p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-3"
      >
        <input
          required
          placeholder="Full name"
          value={form.full_name}
          onChange={handleChange("full_name")}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
        <select
          value={form.role}
          onChange={handleChange("role")}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm"
        >
          <option value="student">Student</option>
          <option value="staff">Staff</option>
        </select>
        <input
          required
          placeholder="Student/Employee ID"
          value={form.student_or_employee_id}
          onChange={handleChange("student_or_employee_id")}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
        <input
          required
          placeholder="NFC ID"
          value={form.nfc_id}
          onChange={handleChange("nfc_id")}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
        <input
          placeholder="Department / Course"
          value={form.department_or_course}
          onChange={handleChange("department_or_course")}
          className="rounded border border-gray-300 px-2 py-1.5 text-sm"
        />
        <div className="col-span-full space-y-2 rounded border border-gray-200 p-3">
          <p className="text-sm font-medium text-gray-700">Photo - scan a face first, or upload a file</p>
          {photoPreviewUrl ? (
            <div className="flex items-center gap-3">
              <img
                src={photoPreviewUrl}
                alt="Captured preview"
                className="h-20 w-20 rounded object-cover"
              />
              <button type="button" onClick={clearPhoto} className="text-sm text-maroon hover:underline">
                Retake / remove
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              <WebcamCapture onCapture={setPhoto} />
              <p className="text-xs text-gray-500">or upload a file instead:</p>
              <input
                type="file"
                accept="image/*"
                onChange={handlePhotoChange}
                className="rounded border border-gray-300 px-2 py-1.5 text-sm"
              />
            </div>
          )}
        </div>

        {formError && <p className="col-span-full text-sm text-red-600">{formError}</p>}

        <div className="col-span-full flex gap-2">
          <button
            type="submit"
            disabled={isSubmitting}
            className="rounded bg-maroon px-4 py-2 text-sm font-semibold text-white hover:bg-maroon-600 disabled:opacity-60"
          >
            {editingId ? "Save changes" : "Add user"}
          </button>
          {editingId && (
            <button
              type="button"
              onClick={resetForm}
              className="rounded border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700"
            >
              Cancel edit
            </button>
          )}
        </div>
      </form>

      <form onSubmit={handleBulkImport} className="rounded border border-gray-200 bg-white p-4 shadow-sm">
        <h2 className="mb-2 text-sm font-semibold text-gray-900">Bulk import (CSV/XLSX)</h2>
        <p className="mb-2 text-xs text-gray-500">
          Columns: full_name, role, student_or_employee_id, nfc_id, department_or_course. Photos
          must already be on the server in the configured bulk-photos folder, named
          &lt;student_or_employee_id&gt;.jpg.
        </p>
        <div className="flex items-center gap-3">
          <input
            type="file"
            accept=".csv,.xlsx"
            onChange={(e) => setBulkFile(e.target.files?.[0] || null)}
            className="text-sm"
          />
          <button
            type="submit"
            className="rounded bg-maroon px-3 py-1.5 text-sm font-medium text-white hover:bg-maroon-600"
          >
            Import
          </button>
        </div>
        {bulkReport && (
          <div className="mt-3 text-xs">
            {bulkReport.detail && <p className="text-red-600">{bulkReport.detail}</p>}
            {bulkReport.created !== undefined && (
              <p className="text-green-700">{bulkReport.created} row(s) imported successfully.</p>
            )}
            {bulkReport.errors?.length > 0 && (
              <ul className="mt-1 list-disc pl-5 text-red-600">
                {bulkReport.errors.map((rowError) => (
                  <li key={rowError.row}>
                    Row {rowError.row}: {rowError.error}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </form>

      <div className="overflow-x-auto rounded border border-gray-200 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            <tr>
              {["Name", "Role", "ID", "NFC ID", "Department", "Status", ""].map((h) => (
                <th key={h} className="px-4 py-2 text-left font-medium text-gray-600">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {people.map((person) => (
              <tr key={person.id}>
                <td className="px-4 py-2">{person.full_name}</td>
                <td className="px-4 py-2 capitalize">{person.role}</td>
                <td className="px-4 py-2">{person.student_or_employee_id}</td>
                <td className="px-4 py-2">{person.nfc_id}</td>
                <td className="px-4 py-2">{person.department_or_course}</td>
                <td className="px-4 py-2">{person.is_active ? "Active" : "Inactive"}</td>
                <td className="px-4 py-2 text-right">
                  <button onClick={() => handleEdit(person)} className="mr-2 text-maroon hover:underline">
                    Edit
                  </button>
                  {person.is_active ? (
                    <button onClick={() => handleDeactivate(person)} className="text-red-600 hover:underline">
                      Deactivate
                    </button>
                  ) : (
                    <button onClick={() => handleReactivate(person)} className="text-green-700 hover:underline">
                      Reactivate
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
