import { useEffect, useState } from "react";
import apiClient from "../api/client";
import GuidedEnrollment from "../components/GuidedEnrollment";
import WebcamCapture from "../components/WebcamCapture";

const emptyForm = {
  full_name: "",
  role: "student",
  student_or_employee_id: "",
  nfc_id: "",
  department_or_course: "",
};

const emptyEnrollment = { mode: "guided", isReady: false, primaryPhoto: null, extraPhotos: [] };

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

const MAX_EMBEDDINGS_PER_PERSON = 5;

export default function Users() {
  const [people, setPeople] = useState([]);
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState(null);
  const [formError, setFormError] = useState("");
  const [enrollment, setEnrollment] = useState(emptyEnrollment);
  const [enrollmentResetKey, setEnrollmentResetKey] = useState(0);
  const [profilePhoto, setProfilePhotoState] = useState({ file: null, previewUrl: null });
  const [bulkFile, setBulkFile] = useState(null);
  const [bulkReport, setBulkReport] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [addPhotoError, setAddPhotoError] = useState("");
  const [isAddingPhoto, setIsAddingPhoto] = useState(false);

  const editingPerson = editingId ? people.find((person) => person.id === editingId) : null;

  const loadPeople = () => {
    apiClient.get("/users/", { params: { page_size: 100 } }).then(({ data }) => {
      setPeople(data.results || data);
    });
  };

  useEffect(loadPeople, []);

  const handleChange = (field) => (event) => {
    setForm((prev) => ({ ...prev, [field]: event.target.value }));
  };

  const setProfilePhoto = (file) => {
    setProfilePhotoState((prev) => {
      if (prev.previewUrl) URL.revokeObjectURL(prev.previewUrl);
      return { file, previewUrl: file ? URL.createObjectURL(file) : null };
    });
  };

  const resetForm = () => {
    setForm(emptyForm);
    setEditingId(null);
    setEnrollment(emptyEnrollment);
    setEnrollmentResetKey((k) => k + 1); // remounts GuidedEnrollment with fresh slot state
    setProfilePhoto(null);
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    setFormError("");
    setIsSubmitting(true);

    try {
      if (editingId) {
        const payload = new FormData();
        Object.entries(form).forEach(([key, value]) => {
          if (value !== null && value !== "") payload.append(key, value);
        });
        if (profilePhoto.file) payload.append("profile_picture", profilePhoto.file);
        await apiClient.put(`/users/${editingId}/`, payload, {
          headers: { "Content-Type": "multipart/form-data" },
        });
        resetForm();
        loadPeople();
      } else {
        if (!enrollment.primaryPhoto) {
          setFormError("Capture or upload at least one enrollment photo first.");
          return;
        }
        const payload = new FormData();
        Object.entries(form).forEach(([key, value]) => {
          if (value !== null && value !== "") payload.append(key, value);
        });
        payload.append("photo", enrollment.primaryPhoto);
        if (enrollment.mode === "fallback") payload.append("fallback_enrollment", "true");
        if (profilePhoto.file) payload.append("profile_picture", profilePhoto.file);

        const { data: created } = await apiClient.post("/users/", payload, {
          headers: { "Content-Type": "multipart/form-data" },
        });

        const extraPhotoErrors = [];
        for (const extraPhoto of enrollment.extraPhotos) {
          const extraPayload = new FormData();
          extraPayload.append("photo", extraPhoto);
          try {
            // eslint-disable-next-line no-await-in-loop
            await apiClient.post(`/users/${created.id}/photos/`, extraPayload, {
              headers: { "Content-Type": "multipart/form-data" },
            });
          } catch (err) {
            extraPhotoErrors.push(extractErrorMessage(err));
          }
        }

        resetForm();
        loadPeople();
        if (extraPhotoErrors.length > 0) {
          setFormError(
            `${created.full_name} was added, but ${extraPhotoErrors.length} of the extra photos failed ` +
              `(${extraPhotoErrors[0]}). Add the remaining photos from the Edit view.`
          );
        }
      }
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
    });
    setProfilePhoto(null); // clear any leftover preview from a previous edit session
  };

  const handleDeactivate = async (person) => {
    await apiClient.delete(`/users/${person.id}/`);
    loadPeople();
  };

  const handleReactivate = async (person) => {
    await apiClient.patch(`/users/${person.id}/`, { is_active: true });
    loadPeople();
  };

  const handleDeletePermanently = async (person) => {
    const confirmed = window.confirm(
      `Permanently delete ${person.full_name}? This cannot be undone. Their past entry/exit logs ` +
        `will stay, but will show "Unknown" instead of their name.`
    );
    if (!confirmed) return;
    await apiClient.delete(`/users/${person.id}/permanent/`);
    if (editingId === person.id) resetForm();
    loadPeople();
  };

  const handleAddPhoto = async (file) => {
    if (!editingId || !file) return;
    setAddPhotoError("");
    setIsAddingPhoto(true);
    const payload = new FormData();
    payload.append("photo", file);
    try {
      await apiClient.post(`/users/${editingId}/photos/`, payload, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      loadPeople();
    } catch (err) {
      setAddPhotoError(extractErrorMessage(err));
    } finally {
      setIsAddingPhoto(false);
    }
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
          <p className="text-sm font-medium text-gray-700">
            Profile picture <span className="font-normal text-gray-400">(optional)</span>
          </p>
          <p className="text-xs text-gray-500">
            Shown to the guard on a card tap or gate pass. Separate from the face-recognition photos
            below - doesn't need to be one of those, any clear photo works. Leave blank to keep using
            the enrollment photo.
          </p>
          <div className="flex items-center gap-3">
            {(profilePhoto.previewUrl || editingPerson?.photo_reference) && (
              <img
                src={profilePhoto.previewUrl || editingPerson.photo_reference}
                alt="Profile preview"
                className="h-20 w-20 rounded object-cover"
              />
            )}
            <div className="space-y-2">
              <WebcamCapture onCapture={setProfilePhoto} />
              <div className="flex items-center gap-2">
                <input
                  type="file"
                  accept="image/*"
                  onChange={(e) => setProfilePhoto(e.target.files?.[0] || null)}
                  className="rounded border border-gray-300 px-2 py-1.5 text-sm"
                />
                {profilePhoto.file && (
                  <button type="button" onClick={() => setProfilePhoto(null)} className="text-sm text-maroon hover:underline">
                    Clear selection
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>

        {!editingId && (
          <GuidedEnrollment key={enrollmentResetKey} onChange={setEnrollment} disabled={isSubmitting} />
        )}

        {formError && <p className="col-span-full text-sm text-red-600">{formError}</p>}

        <div className="col-span-full flex gap-2">
          <button
            type="submit"
            disabled={isSubmitting || (!editingId && !enrollment.isReady)}
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

      {editingPerson && (
        <div className="rounded border border-gray-200 bg-white p-4 shadow-sm">
          <h2 className="mb-1 text-sm font-semibold text-gray-900">
            Enrollment photos - {editingPerson.full_name}
          </h2>
          <p className="mb-3 text-xs text-gray-500">
            {editingPerson.face_embeddings?.length || 0}/{MAX_EMBEDDINGS_PER_PERSON} photos. A few
            photos with slight variation (angle, expression) match more reliably than just one.
          </p>
          <div className="mb-3 flex flex-wrap gap-2">
            {(editingPerson.face_embeddings || []).map((embedding) => (
              <div key={embedding.id} className="relative">
                <img
                  src={embedding.source_image}
                  alt=""
                  className="h-16 w-16 rounded object-cover"
                />
                {embedding.is_low_confidence && (
                  <span
                    title="Single-photo import, not a guided live capture"
                    className="absolute -top-1 -right-1 rounded-full bg-amber-500 px-1 text-[10px] text-white"
                  >
                    !
                  </span>
                )}
              </div>
            ))}
            {(editingPerson.face_embeddings || []).length === 0 && (
              <p className="text-xs text-gray-400">No enrollment photos yet.</p>
            )}
          </div>
          {(editingPerson.face_embeddings?.length || 0) < MAX_EMBEDDINGS_PER_PERSON ? (
            <div className="space-y-2">
              <WebcamCapture onCapture={handleAddPhoto} />
              <p className="text-xs text-gray-500">or upload a file instead:</p>
              <input
                type="file"
                accept="image/*"
                disabled={isAddingPhoto}
                onChange={(e) => handleAddPhoto(e.target.files?.[0] || null)}
                className="rounded border border-gray-300 px-2 py-1.5 text-sm"
              />
            </div>
          ) : (
            <p className="text-xs text-gray-500">Maximum photos reached.</p>
          )}
          {addPhotoError && <p className="mt-2 text-sm text-red-600">{addPhotoError}</p>}
        </div>
      )}

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
                <td className="px-4 py-2">
                  {person.full_name}
                  {person.face_embeddings?.some((e) => e.is_low_confidence) && (
                    <span
                      title="Enrolled from a single uncorroborated photo (bulk import or fallback capture)"
                      className="ml-2 inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-800"
                    >
                      Fallback enrollment
                    </span>
                  )}
                </td>
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
                    <button onClick={() => handleDeactivate(person)} className="mr-2 text-red-600 hover:underline">
                      Deactivate
                    </button>
                  ) : (
                    <button onClick={() => handleReactivate(person)} className="mr-2 text-green-700 hover:underline">
                      Reactivate
                    </button>
                  )}
                  <button
                    onClick={() => handleDeletePermanently(person)}
                    className="text-red-800 hover:underline"
                    title="Permanently remove this record - cannot be undone"
                  >
                    Delete permanently
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
