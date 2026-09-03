import { useEffect, useState } from "react";
import apiClient from "../api/client";
import GuidedEnrollment from "../components/GuidedEnrollment";
import WebcamCapture from "../components/WebcamCapture";
import { PHOTO_ACCEPT, photoFormatError, previewUrlFor, readPhotoInput } from "../lib/photoUpload";
import { useAuth } from "../auth/AuthContext";

const emptyForm = {
  full_name: "",
  role: "student",
  student_or_employee_id: "",
  nfc_id: "",
  department_or_course: "",
  distinguishing_note: "",
};

const emptyConfusableForm = { person_a: "", person_b: "" };

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

// Fallback only, for the brief window before a person's own `max_embeddings`
// (from the API - see users/serializers.py's max_embeddings_for) has loaded.
// The real cap is per-person and higher once flagged in a confusable pair.
const DEFAULT_MAX_EMBEDDINGS = 5;

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-ink-500">{label}</span>
      {children}
    </label>
  );
}

const inputClass =
  "w-full rounded-lg border border-ink-200 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition-colors placeholder:text-ink-400 focus:border-maroon focus:outline-none focus:ring-1 focus:ring-maroon";

function IdCardIcon(props) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
      <rect x="3.5" y="5.5" width="17" height="13" rx="1.8" stroke="currentColor" strokeWidth="1.4" />
      <circle cx="8.5" cy="11" r="1.8" stroke="currentColor" strokeWidth="1.3" />
      <path d="M5.8 15.5c.5-1.6 1.7-2.4 2.7-2.4s2.2.8 2.7 2.4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M14 10h4M14 12.3h4M14 14.6h2.6" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

export default function Users() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [people, setPeople] = useState([]);
  const [pendingRequests, setPendingRequests] = useState([]);
  const [requestActionError, setRequestActionError] = useState("");
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState(null);
  const [formError, setFormError] = useState("");
  const [enrollment, setEnrollment] = useState(emptyEnrollment);
  const [enrollmentResetKey, setEnrollmentResetKey] = useState(0);
  const [profilePhoto, setProfilePhotoState] = useState({ file: null, previewUrl: null });
  const [profilePhotoError, setProfilePhotoError] = useState("");
  const [bulkFile, setBulkFile] = useState(null);
  const [bulkReport, setBulkReport] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [addPhotoError, setAddPhotoError] = useState("");
  const [isAddingPhoto, setIsAddingPhoto] = useState(false);
  const [confusablePairs, setConfusablePairs] = useState([]);
  const [confusableForm, setConfusableForm] = useState(emptyConfusableForm);
  const [confusableError, setConfusableError] = useState("");

  const editingPerson = editingId ? people.find((person) => person.id === editingId) : null;

  const [loadError, setLoadError] = useState("");

  const loadPeople = () => {
    apiClient
      .get("/users/", { params: { page_size: 100 } })
      .then(({ data }) => setPeople(data.results || data))
      .catch(() => setLoadError("Could not load the user list. Try refreshing the page."));
  };

  // Admin-only: the maker-checker queue of SASO-filed deactivation requests
  // still awaiting a decision. A SASO's own requests are visible to them via
  // this same endpoint too, but they can't approve/reject (403 on those
  // actions - see DeactivationRequestViewSet), so there's no reason to
  // clutter their view with a queue they can't act on.
  const loadPendingRequests = () => {
    if (!isAdmin) return;
    // No server-side status filter on this endpoint - filtered client-side,
    // same pragmatic pattern Logs.jsx's "hide unknown" checkbox already uses.
    apiClient.get("/deactivation-requests/", { params: { page_size: 100 } }).then(({ data }) => {
      const all = data.results || data;
      setPendingRequests(all.filter((request) => request.status === "pending"));
    });
  };

  // Both Admin and SASO get full access here (this page is already gated to
  // those two roles - see App.jsx), matching their equal enroll/edit power
  // over Person records generally.
  const loadConfusablePairs = () => {
    apiClient
      .get("/confusable-pairs/", { params: { page_size: 100 } })
      .then(({ data }) => setConfusablePairs(data.results || data))
      .catch(() => setConfusableError("Could not load confusable pairs. Try refreshing the page."));
  };

  useEffect(loadPeople, []);
  useEffect(loadPendingRequests, [isAdmin]);
  useEffect(loadConfusablePairs, []);

  const handleChange = (field) => (event) => {
    setForm((prev) => ({ ...prev, [field]: event.target.value }));
  };

  const setProfilePhoto = (file) => {
    // photoFormatError(null) is null, so clearing the selection still works.
    const formatError = photoFormatError(file);
    setProfilePhotoError(formatError || "");
    if (formatError) return;
    setProfilePhotoState((prev) => {
      if (prev.previewUrl) URL.revokeObjectURL(prev.previewUrl);
      // null previewUrl for HEIC - accepted, but no browser here can paint it.
      return { file, previewUrl: previewUrlFor(file) };
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

        if (created.confusable_partners?.length > 0) {
          // Drop straight into the edit view instead of a full reset - the
          // enrollment-photos section right below (with its now-extended
          // photo cap) and the confusable-partners banner are both driven
          // by editingPerson, so this puts the "please capture more
          // photos"/"please confirm this is expected" prompts right in
          // front of whoever's enrolling, not one extra click away.
          setEditingId(created.id);
          setForm({
            full_name: created.full_name,
            role: created.role,
            student_or_employee_id: created.student_or_employee_id,
            nfc_id: created.nfc_id,
            department_or_course: created.department_or_course,
            distinguishing_note: created.distinguishing_note || "",
          });
          setEnrollment(emptyEnrollment);
          setEnrollmentResetKey((k) => k + 1);
          setProfilePhoto(null);
        } else {
          resetForm();
        }
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
      distinguishing_note: person.distinguishing_note || "",
    });
    setProfilePhoto(null); // clear any leftover preview from a previous edit session
  };

  const handleDeactivate = async (person) => {
    if (isAdmin) {
      await apiClient.delete(`/users/${person.id}/`);
      loadPeople();
      return;
    }
    // SASO: this never deactivates directly (see backend PersonViewSet.
    // destroy() - a SASO's DELETE becomes a pending request instead). A
    // reason is required, so ask for it up front rather than round-
    // tripping to the server just to find that out.
    const reason = window.prompt(
      `Request deactivation of ${person.full_name}. This won't take effect until an Admin approves it - why should this record be deactivated?`
    );
    if (reason === null) return; // cancelled
    if (!reason.trim()) {
      window.alert("A reason is required to request deactivation.");
      return;
    }
    try {
      const { data } = await apiClient.delete(`/users/${person.id}/`, { data: { reason: reason.trim() } });
      window.alert(data.detail);
      loadPeople();
    } catch (err) {
      window.alert(err.response?.data?.detail || "Could not submit the deactivation request.");
    }
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

  const handleApproveRequest = async (request) => {
    setRequestActionError("");
    try {
      await apiClient.post(`/deactivation-requests/${request.id}/approve/`, {});
      loadPendingRequests();
      loadPeople();
    } catch (err) {
      setRequestActionError(err.response?.data?.detail || "Could not approve this request.");
    }
  };

  const handleRejectRequest = async (request) => {
    const note = window.prompt(`Reject the deactivation request for ${request.person_name}? Optional note:`, "");
    if (note === null) return;
    setRequestActionError("");
    try {
      await apiClient.post(`/deactivation-requests/${request.id}/reject/`, { note });
      loadPendingRequests();
      loadPeople();
    } catch (err) {
      setRequestActionError(err.response?.data?.detail || "Could not reject this request.");
    }
  };

  const handleFlagConfusablePair = async (event) => {
    event.preventDefault();
    setConfusableError("");
    if (!confusableForm.person_a || !confusableForm.person_b) {
      setConfusableError("Select both people to flag them as a confusable pair.");
      return;
    }
    if (confusableForm.person_a === confusableForm.person_b) {
      setConfusableError("A person can't be flagged as confusable with themselves.");
      return;
    }
    try {
      await apiClient.post("/confusable-pairs/", {
        person_a: confusableForm.person_a,
        person_b: confusableForm.person_b,
      });
      setConfusableForm(emptyConfusableForm);
      loadConfusablePairs();
      loadPeople(); // so both records' confusable_partners/max_embeddings refresh
    } catch (err) {
      setConfusableError(extractErrorMessage(err));
    }
  };

  const handleUnflagPair = async (pair) => {
    const confirmed = window.confirm(
      `Remove the confusable-pair flag between ${pair.person_a_name} and ${pair.person_b_name}? ` +
        "A gate scan will no longer force a card tap for this pair specifically."
    );
    if (!confirmed) return;
    await apiClient.delete(`/confusable-pairs/${pair.id}/`);
    loadConfusablePairs();
    loadPeople();
  };

  const handleAddPhoto = async (file) => {
    if (!editingId || !file) return;
    const formatError = photoFormatError(file);
    if (formatError) {
      setAddPhotoError(formatError);
      return;
    }
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
        <h1 className="font-display text-2xl font-semibold text-ink-900">User Management</h1>
        <p className="mt-1 text-sm text-ink-500">
          {isAdmin
            ? "Register, edit, and deactivate students and staff."
            : "Register and edit students and staff. Deactivating a record requires Admin approval."}
        </p>
      </div>

      {loadError && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{loadError}</p>}

      {isAdmin && pendingRequests.length > 0 && (
        <div className="rounded-xl border border-gold-500/30 bg-gold-500/5 p-6 shadow-sm">
          <h2 className="font-display text-lg font-semibold text-ink-900">
            Pending deactivation requests ({pendingRequests.length})
          </h2>
          <p className="mt-1 text-xs text-ink-500">
            Filed by a Security Manager (SASO) - review the reason, then approve or reject.
          </p>
          {requestActionError && (
            <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{requestActionError}</p>
          )}
          <ul className="mt-4 space-y-3">
            {pendingRequests.map((request) => (
              <li
                key={request.id}
                className="flex items-center justify-between rounded-lg border border-ink-900/10 bg-white p-3"
              >
                <div>
                  <p className="text-sm font-semibold text-ink-900">
                    {request.person_name} <span className="font-normal text-ink-500">({request.student_or_employee_id})</span>
                  </p>
                  <p className="text-xs text-ink-500">
                    Requested by {request.requested_by_username} on{" "}
                    {new Date(request.requested_at).toLocaleString()}
                  </p>
                  <p className="mt-1 text-sm text-ink-700">&ldquo;{request.reason}&rdquo;</p>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button
                    onClick={() => handleApproveRequest(request)}
                    className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-emerald-700"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() => handleRejectRequest(request)}
                    className="rounded-lg border border-ink-200 px-3 py-1.5 text-sm font-medium text-ink-700 transition-colors hover:bg-parchment-100"
                  >
                    Reject
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <form
        onSubmit={handleSubmit}
        className="space-y-5 rounded-xl border border-ink-900/10 bg-white p-6 shadow-sm"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Full name">
            <input
              required
              placeholder="Juan Dela Cruz"
              value={form.full_name}
              onChange={handleChange("full_name")}
              className={inputClass}
            />
          </Field>
          <Field label="Role">
            <select value={form.role} onChange={handleChange("role")} className={inputClass}>
              <option value="student">Student</option>
              <option value="staff">Staff</option>
            </select>
          </Field>
          <Field label="Student / Employee ID">
            <input
              required
              placeholder="2021-00123"
              value={form.student_or_employee_id}
              onChange={handleChange("student_or_employee_id")}
              className={inputClass}
            />
          </Field>
          <Field label="NFC ID">
            <input
              required
              placeholder="Tap a card to fill this in"
              value={form.nfc_id}
              onChange={handleChange("nfc_id")}
              className={inputClass}
            />
          </Field>
          <Field label="Department / Course">
            <input
              placeholder="BSIT"
              value={form.department_or_course}
              onChange={handleChange("department_or_course")}
              className={inputClass}
            />
          </Field>
        </div>

        <Field label="Distinguishing note (optional)">
          <input
            placeholder="e.g. mole on left cheek, wears glasses - visible to security staff, never used for matching"
            value={form.distinguishing_note}
            onChange={handleChange("distinguishing_note")}
            className={inputClass}
          />
        </Field>

        <div className="space-y-3 rounded-xl border border-ink-900/10 bg-parchment-50 p-4">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-maroon/10 text-maroon">
              <IdCardIcon className="h-4.5 w-4.5" />
            </span>
            <div>
              <p className="text-sm font-semibold text-ink-900">
                Profile picture <span className="font-normal text-ink-400">(optional)</span>
              </p>
              <p className="text-xs text-ink-500">
                Shown to the guard on a card tap or gate pass. Separate from the face-recognition photos
                below - doesn&rsquo;t need to be one of those, any clear photo works. Leave blank to keep
                using the enrollment photo.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3 pl-11">
            {profilePhoto.file && !profilePhoto.previewUrl ? (
              // Selected, accepted, just unrenderable in this browser (HEIC).
              <div className="flex h-20 w-20 flex-col items-center justify-center rounded-lg bg-parchment-200 text-[10px] text-ink-500">
                <span>HEIC</span>
                <span className="text-ink-400">no preview</span>
              </div>
            ) : (
              (profilePhoto.previewUrl || editingPerson?.photo_reference) && (
                <img
                  src={profilePhoto.previewUrl || editingPerson.photo_reference}
                  alt="Profile preview"
                  className="h-20 w-20 rounded-lg border border-ink-900/10 object-cover"
                />
              )
            )}
            <div className="space-y-2">
              <WebcamCapture onCapture={setProfilePhoto} />
              <div className="flex items-center gap-2">
                <input
                  type="file"
                  accept={PHOTO_ACCEPT}
                  onChange={(e) => setProfilePhoto(readPhotoInput(e))}
                  className="rounded border border-ink-200 px-2 py-1.5 text-sm file:mr-3 file:rounded file:border-0 file:bg-maroon/10 file:px-2 file:py-1 file:text-xs file:font-medium file:text-maroon"
                />
                {profilePhoto.file && (
                  <button type="button" onClick={() => setProfilePhoto(null)} className="text-sm font-medium text-maroon hover:underline">
                    Clear selection
                  </button>
                )}
              </div>
              <p className="text-xs text-ink-400">JPEG, PNG or HEIC only.</p>
              {profilePhotoError && <p className="text-sm text-red-600">{profilePhotoError}</p>}
            </div>
          </div>
        </div>

        {!editingId && (
          <GuidedEnrollment key={enrollmentResetKey} onChange={setEnrollment} disabled={isSubmitting} />
        )}

        {formError && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{formError}</p>}

        <div className="flex gap-2 border-t border-ink-900/10 pt-4">
          <button
            type="submit"
            disabled={isSubmitting || (!editingId && !enrollment.isReady)}
            className="rounded-lg bg-maroon px-5 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-maroon-600 disabled:opacity-50"
          >
            {editingId ? "Save changes" : "Add user"}
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

      {editingPerson && (
        <div className="rounded-xl border border-ink-900/10 bg-white p-6 shadow-sm">
          {editingPerson.confusable_partners?.length > 0 && (
            <div className="mb-4 rounded-lg border border-gold-500/40 bg-gold-500/10 p-3">
              <p className="text-sm font-semibold text-ink-900">
                Unusually similar to{" "}
                {editingPerson.confusable_partners.map((partner) => partner.full_name).join(", ")}
              </p>
              <p className="mt-1 text-xs text-ink-700">
                Please confirm this is expected (e.g. twins/siblings) - a gate scan will always require a
                card tap for this person regardless of face-match confidence, and every future scan of{" "}
                {editingPerson.confusable_partners.length > 1 ? "either of them" : "them"} will too. Consider
                capturing a few extra photos below (more variation helps) and adding a distinguishing note
                above. Manage this flag in Confusable pairs further down if it was detected in error.
              </p>
            </div>
          )}
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="font-display text-lg font-semibold text-ink-900">
                Enrollment photos - {editingPerson.full_name}
              </h2>
              <p className="text-xs text-ink-500">
                {editingPerson.face_embeddings?.length || 0}/{editingPerson.max_embeddings || DEFAULT_MAX_EMBEDDINGS}{" "}
                photos. A few photos with slight variation (angle, expression) match more reliably than just
                one.
              </p>
            </div>
          </div>
          <div className="mb-4 flex flex-wrap gap-3">
            {(editingPerson.face_embeddings || []).map((embedding) => (
              <div key={embedding.id} className="relative">
                <img
                  src={embedding.source_image}
                  alt=""
                  className="h-16 w-16 rounded-lg border-2 border-gold-500 object-cover"
                />
                {embedding.is_low_confidence && (
                  <span
                    title="Single-photo import, not a guided live capture"
                    className="absolute -right-1 -top-1 rounded-full bg-amber-500 px-1 text-[10px] font-bold text-white shadow-sm"
                  >
                    !
                  </span>
                )}
              </div>
            ))}
            {(editingPerson.face_embeddings || []).length === 0 && (
              <p className="text-xs text-ink-400">No enrollment photos yet.</p>
            )}
          </div>
          {(editingPerson.face_embeddings?.length || 0) < (editingPerson.max_embeddings || DEFAULT_MAX_EMBEDDINGS) ? (
            <div className="space-y-2 rounded-lg border border-ink-900/10 bg-parchment-50 p-3">
              <WebcamCapture onCapture={handleAddPhoto} />
              <p className="text-xs text-ink-500">or upload a file instead (JPEG, PNG or HEIC only):</p>
              <input
                type="file"
                accept={PHOTO_ACCEPT}
                disabled={isAddingPhoto}
                onChange={(e) => handleAddPhoto(readPhotoInput(e))}
                className="rounded border border-ink-200 bg-white px-2 py-1.5 text-sm file:mr-3 file:rounded file:border-0 file:bg-maroon/10 file:px-2 file:py-1 file:text-xs file:font-medium file:text-maroon"
              />
            </div>
          ) : (
            <p className="text-xs font-medium text-gold-700">Maximum photos reached.</p>
          )}
          {addPhotoError && <p className="mt-2 text-sm text-red-600">{addPhotoError}</p>}
        </div>
      )}

      <div className="rounded-xl border border-ink-900/10 bg-white p-6 shadow-sm">
        <h2 className="font-display text-lg font-semibold text-ink-900">Confusable pairs</h2>
        <p className="mt-1 text-xs text-ink-500">
          Two people flagged here are always sent to a card-tap confirmation at the gate, regardless of
          face-match confidence - identical twins, or anyone else no camera can be expected to reliably tell
          apart. Auto-detected at enrollment when two records' photos come back unusually similar, or flagged
          by hand below (e.g. a guard reports a real mix-up).
        </p>
        {confusableError && (
          <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{confusableError}</p>
        )}
        <form onSubmit={handleFlagConfusablePair} className="mt-3 flex flex-wrap items-end gap-3">
          <Field label="Person A">
            <select
              value={confusableForm.person_a}
              onChange={(e) => setConfusableForm((prev) => ({ ...prev, person_a: e.target.value }))}
              className={inputClass}
            >
              <option value="">Select a person...</option>
              {people.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.full_name} ({p.student_or_employee_id})
                </option>
              ))}
            </select>
          </Field>
          <Field label="Person B">
            <select
              value={confusableForm.person_b}
              onChange={(e) => setConfusableForm((prev) => ({ ...prev, person_b: e.target.value }))}
              className={inputClass}
            >
              <option value="">Select a person...</option>
              {people.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.full_name} ({p.student_or_employee_id})
                </option>
              ))}
            </select>
          </Field>
          <button
            type="submit"
            className="rounded-lg bg-maroon px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-maroon-600"
          >
            Flag as confusable
          </button>
        </form>
        <ul className="mt-4 space-y-2">
          {confusablePairs.map((pair) => (
            <li
              key={pair.id}
              className="flex items-center justify-between rounded-lg border border-ink-900/10 bg-parchment-50 p-3 text-sm"
            >
              <span className="text-ink-900">
                {pair.person_a_name} <span className="text-ink-400">&harr;</span> {pair.person_b_name}
                <span className="ml-2 text-xs font-normal text-ink-500">
                  {pair.source === "manual"
                    ? `flagged by ${pair.flagged_by_username || "an admin"}`
                    : `auto-detected (${Math.round((pair.detected_similarity || 0) * 100)}% similarity)`}
                </span>
              </span>
              <button
                onClick={() => handleUnflagPair(pair)}
                className="text-sm font-medium text-red-600 hover:underline"
              >
                Remove
              </button>
            </li>
          ))}
          {confusablePairs.length === 0 && (
            <p className="text-xs text-ink-400">No confusable pairs on record.</p>
          )}
        </ul>
      </div>

      <form onSubmit={handleBulkImport} className="rounded-xl border border-ink-900/10 bg-white p-6 shadow-sm">
        <h2 className="font-display text-lg font-semibold text-ink-900">Bulk import (CSV/XLSX)</h2>
        <p className="mb-3 mt-1 text-xs text-ink-500">
          Columns: full_name, role, student_or_employee_id, nfc_id, department_or_course. Photos
          must already be on the server in the configured bulk-photos folder, named
          &lt;student_or_employee_id&gt;.jpg.
        </p>
        <div className="flex items-center gap-3">
          <input
            type="file"
            accept=".csv,.xlsx"
            onChange={(e) => setBulkFile(e.target.files?.[0] || null)}
            className="text-sm file:mr-3 file:rounded file:border-0 file:bg-maroon/10 file:px-2 file:py-1 file:text-xs file:font-medium file:text-maroon"
          />
          <button
            type="submit"
            className="rounded-lg bg-maroon px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-maroon-600"
          >
            Import
          </button>
        </div>
        {bulkReport && (
          <div className="mt-3 text-xs">
            {bulkReport.detail && <p className="text-red-600">{bulkReport.detail}</p>}
            {bulkReport.created !== undefined && (
              <p className="font-medium text-emerald-700">{bulkReport.created} row(s) imported successfully.</p>
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
            {bulkReport.confusable_warnings?.length > 0 && (
              <div className="mt-2 rounded-lg border border-gold-500/30 bg-gold-500/5 p-2">
                <p className="font-semibold text-ink-900">
                  {bulkReport.confusable_warnings.length} row(s) flagged as unusually similar to an existing
                  record - please review:
                </p>
                <ul className="mt-1 list-disc pl-5 text-ink-700">
                  {bulkReport.confusable_warnings.map((warning, index) => (
                    <li key={index}>
                      Row {warning.row} ({warning.imported_full_name}) is unusually similar to{" "}
                      {warning.full_name} ({warning.student_or_employee_id}) -{" "}
                      {Math.round(warning.similarity * 100)}% similarity.
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </form>

      <div className="overflow-x-auto rounded-xl border border-ink-900/10 bg-white shadow-sm">
        <table className="min-w-full divide-y divide-ink-900/10 text-sm">
          <thead className="bg-parchment-100">
            <tr>
              {["Name", "Role", "ID", "NFC ID", "Department", "Status", ""].map((h) => (
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
            {people.map((person) => (
              <tr key={person.id} className="transition-colors hover:bg-parchment-50">
                <td className="px-4 py-2.5 font-medium text-ink-900">
                  {person.full_name}
                  {person.face_embeddings?.some((e) => e.is_low_confidence) && (
                    <span
                      title="Enrolled from a single uncorroborated photo (bulk import or fallback capture)"
                      className="ml-2 inline-flex items-center rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-800"
                    >
                      Fallback enrollment
                    </span>
                  )}
                  {person.confusable_partners?.length > 0 && (
                    <span
                      title={`Always requires a card tap at the gate - unusually similar to ${person.confusable_partners.map((p) => p.full_name).join(", ")}`}
                      className="ml-2 inline-flex items-center rounded-full border border-gold-500/40 bg-gold-500/10 px-2 py-0.5 text-[10px] font-bold text-gold-800"
                    >
                      Confusable pair
                    </span>
                  )}
                </td>
                <td className="px-4 py-2.5 capitalize text-ink-700">{person.role}</td>
                <td className="px-4 py-2.5 text-ink-700">{person.student_or_employee_id}</td>
                <td className="px-4 py-2.5 text-ink-700">{person.nfc_id}</td>
                <td className="px-4 py-2.5 text-ink-700">{person.department_or_course}</td>
                <td className="px-4 py-2.5">
                  <span
                    className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ${
                      person.is_active
                        ? "border border-emerald-200 bg-emerald-50 text-emerald-800"
                        : "border border-ink-200 bg-ink-50 text-ink-500"
                    }`}
                  >
                    {person.is_active ? "Active" : "Inactive"}
                  </span>
                  {person.pending_deactivation && (
                    <span
                      title="A SASO has requested this record be deactivated - awaiting Admin approval"
                      className="ml-2 inline-flex items-center rounded-full border border-gold-500/40 bg-gold-500/10 px-2.5 py-1 text-xs font-semibold text-gold-800"
                    >
                      Pending deactivation
                    </span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-right">
                  <button onClick={() => handleEdit(person)} className="mr-3 text-sm font-medium text-maroon hover:underline">
                    Edit
                  </button>
                  {person.is_active ? (
                    <button
                      onClick={() => handleDeactivate(person)}
                      disabled={person.pending_deactivation}
                      className="mr-3 text-sm font-medium text-red-600 hover:underline disabled:cursor-not-allowed disabled:text-ink-300 disabled:no-underline"
                    >
                      {isAdmin ? "Deactivate" : "Request deactivation"}
                    </button>
                  ) : (
                    <button onClick={() => handleReactivate(person)} className="mr-3 text-sm font-medium text-emerald-700 hover:underline">
                      Reactivate
                    </button>
                  )}
                  {isAdmin && (
                    <button
                      onClick={() => handleDeletePermanently(person)}
                      className="text-sm font-medium text-red-800 hover:underline"
                      title="Permanently remove this record - cannot be undone"
                    >
                      Delete permanently
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
