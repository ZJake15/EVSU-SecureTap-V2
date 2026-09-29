import { useEffect, useState } from "react";
import apiClient from "../api/client";
import GuidedEnrollment from "../components/GuidedEnrollment";
import WebcamCapture from "../components/WebcamCapture";
import { Avatar, Field, Icon, Notice, PageHeader, Segmented } from "../components/ui";
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

const PEOPLE_COLS = "minmax(0,2.3fr) 72px 110px 100px minmax(0,1.3fr) 90px 190px";

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

function formatDate(value) {
  return new Date(value).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// The one flag worth showing under a person's name, most urgent first.
function personFlag(person) {
  if (person.pending_deactivation) {
    return {
      label: "Pending deactivation",
      icon: "clock",
      className: "text-caution",
      title: "A SASO has requested this record be deactivated - awaiting Admin approval",
    };
  }
  if (person.confusable_partners?.length > 0) {
    return {
      label: "Lookalike pair",
      icon: "users-three",
      className: "text-ink-600",
      title: `Always requires a card tap at the gate - unusually similar to ${person.confusable_partners
        .map((p) => p.full_name)
        .join(", ")}`,
    };
  }
  if (person.face_embeddings?.some((e) => e.is_low_confidence)) {
    return {
      label: "Single-photo enrollment · lower confidence",
      icon: "image",
      className: "text-ink-600",
      title: "Enrolled from a single uncorroborated photo (bulk import or fallback capture)",
    };
  }
  return null;
}

function Tabs({ tabs, active, onChange }) {
  return (
    <div className="mt-s5 flex flex-none gap-s5 border-b border-line">
      {tabs.map((tab) => {
        const selected = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            onClick={() => onChange(tab.key)}
            className={`-mb-px flex items-center gap-s2 border-b-[3px] pb-s3 text-sm transition-colors ${
              selected ? "border-brass font-bold text-ink" : "border-transparent text-ink-600 hover:text-ink"
            }`}
          >
            {tab.label}
            {tab.count !== undefined && tab.count !== "" && (
              <span className="font-mono text-xs font-normal text-ink-600">{tab.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export default function Users() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [activeTab, setActiveTab] = useState("people");
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [people, setPeople] = useState([]);
  const [pendingRequests, setPendingRequests] = useState([]);
  const [requestNotes, setRequestNotes] = useState({});
  const [requestActionError, setRequestActionError] = useState("");
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
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
    setFormError("");
    setAddPhotoError("");
    setEnrollment(emptyEnrollment);
    setEnrollmentResetKey((k) => k + 1); // remounts GuidedEnrollment with fresh slot state
    setProfilePhoto(null);
    setIsDrawerOpen(false);
  };

  const openAddDrawer = () => {
    resetForm();
    setIsDrawerOpen(true);
  };

  // Escape closes the drawer, same as the X and Cancel.
  useEffect(() => {
    if (!isDrawerOpen) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape" && !isSubmitting) resetForm();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDrawerOpen, isSubmitting]);

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
          // enrollment-photos section (with its now-extended photo cap) and
          // the confusable-partners banner are both driven by editingPerson,
          // so this puts the "please capture more photos"/"please confirm
          // this is expected" prompts right in front of whoever's
          // enrolling, not one extra click away.
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
    setFormError("");
    setAddPhotoError("");
    setForm({
      full_name: person.full_name,
      role: person.role,
      student_or_employee_id: person.student_or_employee_id,
      nfc_id: person.nfc_id,
      department_or_course: person.department_or_course,
      distinguishing_note: person.distinguishing_note || "",
    });
    setProfilePhoto(null); // clear any leftover preview from a previous edit session
    setIsDrawerOpen(true);
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
    setRequestActionError("");
    try {
      await apiClient.post(`/deactivation-requests/${request.id}/reject/`, { note: requestNotes[request.id] || "" });
      setRequestNotes((prev) => {
        const next = { ...prev };
        delete next[request.id];
        return next;
      });
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
      setConfusableError("Select both people to flag them as a lookalike pair.");
      return;
    }
    if (confusableForm.person_a === confusableForm.person_b) {
      setConfusableError("A person can't be flagged as a lookalike of themselves.");
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
      `Remove the lookalike-pair flag between ${pair.person_a_name} and ${pair.person_b_name}? ` +
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
      setBulkReport({ ...data, fileName: bulkFile.name, at: new Date() });
      loadPeople();
    } catch (err) {
      setBulkReport({ ...(err.response?.data || { detail: "Bulk import failed." }), fileName: bulkFile.name, at: new Date() });
    }
  };

  const query = search.trim().toLowerCase();
  const visiblePeople = people.filter((person) => {
    if (roleFilter && person.role !== roleFilter) return false;
    if (statusFilter === "active" && !person.is_active) return false;
    if (statusFilter === "inactive" && person.is_active) return false;
    if (!query) return true;
    return [person.full_name, person.student_or_employee_id, person.nfc_id]
      .filter(Boolean)
      .some((value) => value.toLowerCase().includes(query));
  });

  const eyebrows = {
    people: isAdmin ? "People · Admin view" : "People · SASO view",
    requests: isAdmin ? "Requests & lookalike pairs" : "Lookalike pairs",
    bulk: "Bulk import",
  };

  const tabs = [
    { key: "people", label: "People", count: people.length },
    {
      key: "requests",
      label: isAdmin ? "Requests & pairs" : "Lookalike pairs",
      count: isAdmin ? `${pendingRequests.length} · ${confusablePairs.length}` : confusablePairs.length,
    },
    { key: "bulk", label: "Bulk import", count: "" },
  ];

  const bulkRows = bulkReport
    ? [
        ...(bulkReport.errors || []).map((rowError) => ({
          row: rowError.row,
          name: "—",
          detail: rowError.error,
          status: "Error",
          icon: "x-circle",
          className: "text-danger",
          bar: "#C62828",
        })),
        ...(bulkReport.confusable_warnings || []).map((warning) => ({
          row: warning.row,
          name: warning.imported_full_name,
          detail: `similar to ${warning.full_name} (${warning.student_or_employee_id}) · ${Math.round(
            warning.similarity * 100
          )}%`,
          status: "Lookalike warning",
          icon: "users-three",
          className: "text-caution",
          bar: "#9A5B00",
        })),
      ].sort((a, b) => a.row - b.row)
    : [];

  const photoCount = editingPerson?.face_embeddings?.length || 0;
  const photoMax = editingPerson?.max_embeddings || DEFAULT_MAX_EMBEDDINGS;

  return (
    <div className="flex flex-col">
      <PageHeader eyebrow={eyebrows[activeTab]} title="User Management">
        <button type="button" onClick={() => setActiveTab("bulk")} className="btn-secondary">
          <Icon name="upload-simple" size={16} />
          Bulk import
        </button>
        <button type="button" onClick={openAddDrawer} className="btn-primary">
          <Icon name="plus" bold size={16} />
          Add person
        </button>
      </PageHeader>

      <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />

      {loadError && (
        <Notice tone="danger" className="mt-s4">
          {loadError}
        </Notice>
      )}

      {activeTab === "people" && (
        <>
          <div className="mt-s5 flex flex-none flex-wrap gap-s4">
            <span className="relative block min-w-[220px] max-w-[360px] flex-1">
              <Icon name="magnifying-glass" size={16} className="pointer-events-none absolute left-[10px] top-3 text-ink-600" />
              <input
                type="text"
                placeholder="Search name, ID or NFC ID"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="input pl-9"
              />
            </span>
            <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)} className="input w-[150px]">
              <option value="">All roles</option>
              <option value="student">Student</option>
              <option value="staff">Staff</option>
            </select>
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="input w-[150px]">
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </div>

          <div className="card mt-s4 overflow-hidden">
            <div className="overflow-x-auto">
              <div className="min-w-[980px]">
                <div
                  className="table-head grid h-9 items-center gap-s3 border-b border-line px-s4"
                  style={{ gridTemplateColumns: PEOPLE_COLS }}
                >
                  <span>Name</span>
                  <span>Role</span>
                  <span>ID</span>
                  <span>NFC ID</span>
                  <span>Department / course</span>
                  <span>Status</span>
                  <span className="text-right">Actions</span>
                </div>
                {visiblePeople.map((person) => {
                  const flag = personFlag(person);
                  return (
                    <div
                      key={person.id}
                      className="grid min-h-[52px] items-center gap-s3 border-b border-line px-s4 py-s2 text-sm"
                      style={{ gridTemplateColumns: PEOPLE_COLS }}
                    >
                      <span className="flex min-w-0 items-center gap-s3">
                        <Avatar src={person.photo_reference} name={person.full_name} size={32} />
                        <span className="flex min-w-0 flex-col gap-0.5">
                          <span className="truncate font-bold">{person.full_name}</span>
                          {flag && (
                            <span title={flag.title} className={`flex items-center gap-s1 truncate text-xs font-bold ${flag.className}`}>
                              <Icon name={flag.icon} bold size={12} />
                              {flag.label}
                            </span>
                          )}
                        </span>
                      </span>
                      <span className="capitalize">{person.role}</span>
                      <span className="truncate font-mono text-[13px] font-medium">{person.student_or_employee_id}</span>
                      <span className="truncate font-mono text-[13px] text-ink-600">{person.nfc_id}</span>
                      <span className="truncate text-ink-600">{person.department_or_course}</span>
                      {person.is_active ? (
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
                      <span className="flex items-center justify-end gap-s4">
                        <button type="button" onClick={() => handleEdit(person)} className="link-action">
                          Edit
                        </button>
                        {person.is_active ? (
                          <button
                            type="button"
                            onClick={() => handleDeactivate(person)}
                            disabled={person.pending_deactivation}
                            className="link-danger"
                          >
                            {person.pending_deactivation ? "Pending…" : isAdmin ? "Deactivate" : "Request deactivation"}
                          </button>
                        ) : (
                          <button type="button" onClick={() => handleReactivate(person)} className="text-sm font-bold text-verified hover:underline">
                            Reactivate
                          </button>
                        )}
                        {isAdmin && (
                          <button
                            type="button"
                            onClick={() => handleDeletePermanently(person)}
                            title="Delete permanently - cannot be undone"
                            aria-label={`Delete ${person.full_name} permanently`}
                            className="flex h-8 w-8 items-center justify-center rounded-sm text-danger hover:bg-danger-tint"
                          >
                            <Icon name="trash" size={16} />
                          </button>
                        )}
                      </span>
                    </div>
                  );
                })}
                {visiblePeople.length === 0 && (
                  <p className="px-s4 py-s5 text-sm text-ink-600">
                    {people.length === 0 ? "No one enrolled yet. Use Add person to register the first." : "No one matches these filters."}
                  </p>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {activeTab === "requests" && (
        <div className="mt-s6 grid grid-cols-1 gap-s6 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          <div className="flex min-w-0 flex-col gap-s4">
            <div className="flex items-baseline justify-between gap-s4">
              <span className="t-section">Pending deactivation requests</span>
              <span className="text-sm text-ink-600">Admin approval required</span>
            </div>
            {requestActionError && <Notice tone="danger">{requestActionError}</Notice>}
            {!isAdmin && (
              <p className="border-t border-line pt-s4 text-sm leading-normal text-ink-600">
                Requests you file with &ldquo;Request deactivation&rdquo; on the People tab wait here for an Admin to
                approve or reject them.
              </p>
            )}
            {isAdmin && pendingRequests.length === 0 && (
              <p className="border-t border-line pt-s4 text-sm leading-normal text-ink-600">
                No pending requests. Deactivations filed by a Security Manager (SASO) appear here for review.
              </p>
            )}
            {pendingRequests.map((request) => (
              <div key={request.id} className="card flex flex-col gap-s4 p-s5">
                <div className="flex items-start gap-s4">
                  <Avatar name={request.person_name} size={56} />
                  <div className="flex min-w-0 flex-1 flex-col gap-s1">
                    <span className="text-xl font-bold">{request.person_name}</span>
                    <span className="text-sm text-ink-600">
                      <span className="font-mono font-medium text-ink">{request.student_or_employee_id}</span>
                    </span>
                  </div>
                  <span className="font-mono text-xs text-ink-600">{formatDate(request.requested_at)}</span>
                </div>
                <div className="grid grid-cols-[120px_1fr] gap-y-s2 border-t border-line pt-s4 text-sm">
                  <span className="text-ink-600">Requested by</span>
                  <span className="font-mono font-medium">{request.requested_by_username}</span>
                  <span className="text-ink-600">Reason</span>
                  <span className="font-bold">{request.reason}</span>
                </div>
                <input
                  placeholder="Note to requester (optional)"
                  value={requestNotes[request.id] || ""}
                  onChange={(e) => setRequestNotes((prev) => ({ ...prev, [request.id]: e.target.value }))}
                  className="input"
                />
                <div className="flex gap-s3">
                  <button type="button" onClick={() => handleApproveRequest(request)} className="btn-primary">
                    <Icon name="check" bold size={16} />
                    Approve
                  </button>
                  <button type="button" onClick={() => handleRejectRequest(request)} className="btn-secondary">
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div className="flex min-w-0 flex-col gap-s4">
            <span className="t-section">Lookalike pairs</span>
            <p className="text-sm leading-normal text-ink-600">
              Two people flagged here are always sent to a card-tap confirmation at the gate, regardless of face-match
              confidence - identical twins, or anyone else no camera can be expected to reliably tell apart.
            </p>
            {confusableError && <Notice tone="danger">{confusableError}</Notice>}
            <div className="flex flex-col border-t border-line">
              {confusablePairs.map((pair) => (
                <div key={pair.id} className="flex flex-col gap-s2 border-b border-line py-s4">
                  <div className="flex flex-wrap items-center gap-s3 text-base font-bold">
                    <span>{pair.person_a_name}</span>
                    <Icon name="arrows-left-right" bold size={16} className="text-ink-600" />
                    <span>{pair.person_b_name}</span>
                  </div>
                  <div className="flex items-center justify-between text-sm text-ink-600">
                    {pair.source === "manual" ? (
                      <span className="flex items-center gap-s2">
                        <Icon name="flag" size={16} />
                        Flagged by{" "}
                        <span className="font-mono font-semibold text-ink">{pair.flagged_by_username || "an admin"}</span>
                      </span>
                    ) : (
                      <span className="flex items-center gap-s2">
                        <Icon name="cpu" size={16} />
                        Auto-detected &middot; similarity{" "}
                        <span className="font-mono font-semibold text-ink">
                          {(pair.detected_similarity || 0).toFixed(2)}
                        </span>
                      </span>
                    )}
                    <button type="button" onClick={() => handleUnflagPair(pair)} className="link-danger">
                      Remove
                    </button>
                  </div>
                </div>
              ))}
              {confusablePairs.length === 0 && (
                <p className="py-s4 text-sm text-ink-600">No lookalike pairs on record.</p>
              )}
            </div>
            <form onSubmit={handleFlagConfusablePair} className="card mt-s5 flex flex-col gap-s3 p-s4">
              <span className="text-base font-bold">Flag two people as a pair</span>
              <select
                value={confusableForm.person_a}
                onChange={(e) => setConfusableForm((prev) => ({ ...prev, person_a: e.target.value }))}
                className="input"
              >
                <option value="">Person A</option>
                {people.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.full_name} ({p.student_or_employee_id})
                  </option>
                ))}
              </select>
              <select
                value={confusableForm.person_b}
                onChange={(e) => setConfusableForm((prev) => ({ ...prev, person_b: e.target.value }))}
                className="input"
              >
                <option value="">Person B</option>
                {people.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.full_name} ({p.student_or_employee_id})
                  </option>
                ))}
              </select>
              <button type="submit" className="btn-secondary self-start">
                <Icon name="users-three" size={16} />
                Flag as lookalike pair
              </button>
            </form>
          </div>
        </div>
      )}

      {activeTab === "bulk" && (
        <div className="mt-s6 grid grid-cols-1 gap-s6 xl:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
          <div className="flex min-w-0 flex-col gap-s3">
            <div className="flex flex-wrap items-baseline justify-between gap-s4">
              <span className="t-section">Last bulk import</span>
              {bulkReport && (
                <span className="font-mono text-xs text-ink-600">
                  {bulkReport.fileName} &middot; {formatDate(bulkReport.at)}
                </span>
              )}
            </div>
            {!bulkReport && (
              <p className="border-t border-line pt-s4 text-sm text-ink-600">
                Results appear here after an import: every row that failed, and every row that looks like someone
                already enrolled.
              </p>
            )}
            {bulkReport?.detail && <Notice tone="danger">{bulkReport.detail}</Notice>}
            {bulkReport?.created !== undefined && (
              <Notice tone="verified">
                <span className="font-bold">{bulkReport.created} row(s) imported.</span>
              </Notice>
            )}
            {bulkRows.length > 0 && (
              <div className="card overflow-hidden">
                {bulkRows.map((row, index) => (
                  <div
                    key={`${row.row}-${index}`}
                    className="grid min-h-10 grid-cols-[64px_minmax(0,1fr)_170px] items-center gap-s3 border-b border-l-[3px] border-b-line py-s1 pl-[13px] pr-s4 text-sm"
                    style={{ borderLeftColor: row.bar }}
                  >
                    <span className="font-mono text-xs text-ink-600">Row {row.row}</span>
                    <span className="truncate">
                      <b>{row.name}</b> <span className="text-ink-600">{row.detail}</span>
                    </span>
                    <span className={`flex items-center gap-s1 font-bold ${row.className}`}>
                      <Icon name={row.icon} bold size={14} />
                      {row.status}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <form onSubmit={handleBulkImport} className="card flex flex-col gap-s4 self-start p-s5">
            <div className="flex flex-col gap-s1">
              <span className="t-eyebrow">CSV or XLSX</span>
              <span className="t-section">Import a roster</span>
            </div>
            <p className="text-sm leading-normal text-ink-600">
              Columns: <span className="font-mono text-ink">full_name, role, student_or_employee_id, nfc_id,
              department_or_course</span>. Photos must already be on the server in the bulk-photos folder, named{" "}
              <span className="font-mono text-ink">&lt;student_or_employee_id&gt;.jpg</span>. These enrollments are
              single-photo and flagged lower confidence.
            </p>
            <input type="file" accept=".csv,.xlsx" onChange={(e) => setBulkFile(e.target.files?.[0] || null)} className="file-input" />
            <button type="submit" disabled={!bulkFile} className="btn-primary self-start">
              <Icon name="upload-simple" size={16} />
              Import
            </button>
          </form>
        </div>
      )}

      {isDrawerOpen && (
        <>
          <div className="fixed inset-0 z-40 bg-ink/30" onClick={() => !isSubmitting && resetForm()} aria-hidden="true" />
          <form
            onSubmit={handleSubmit}
            role="dialog"
            aria-modal="true"
            aria-label={editingId ? "Edit person" : "Add person"}
            className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[560px] flex-col bg-surface shadow-[-12px_0_40px_rgba(18,20,22,0.18)]"
          >
            <div className="flex flex-none items-start justify-between border-b border-line px-s5 pb-s4 pt-s5">
              <div className="flex flex-col gap-s1">
                <span className="t-eyebrow">User management</span>
                <span className="font-display stretch-semi text-[28px] font-extrabold leading-none">
                  {editingId ? "Edit person" : "Add person"}
                </span>
              </div>
              <button
                type="button"
                onClick={resetForm}
                aria-label="Close"
                className="flex h-8 w-8 items-center justify-center rounded-sm text-ink-600 hover:bg-canvas"
              >
                <Icon name="x" size={20} />
              </button>
            </div>

            <div className="flex min-h-0 flex-1 flex-col gap-s4 overflow-y-auto px-s5 py-s4">
              <div className="grid grid-cols-[minmax(0,7fr)_minmax(0,5fr)] gap-x-s4 gap-y-s3">
                <Field label="Full name" className="col-span-2">
                  <input required value={form.full_name} onChange={handleChange("full_name")} className="input" />
                </Field>
                <Field label="Student/Employee ID">
                  <input
                    required
                    placeholder="2021-00123"
                    value={form.student_or_employee_id}
                    onChange={handleChange("student_or_employee_id")}
                    className="input font-mono"
                  />
                </Field>
                <div className="flex flex-col gap-s2">
                  <span className="field-label">Role</span>
                  <Segmented
                    value={form.role}
                    onChange={(role) => setForm((prev) => ({ ...prev, role }))}
                    options={[
                      { value: "student", label: "Student" },
                      { value: "staff", label: "Staff" },
                    ]}
                  />
                </div>
                <Field label="Department/course">
                  <input
                    placeholder="BSIT"
                    value={form.department_or_course}
                    onChange={handleChange("department_or_course")}
                    className="input"
                  />
                </Field>
                <Field label="NFC ID">
                  <input
                    required
                    placeholder="Tap a card to fill this in"
                    value={form.nfc_id}
                    onChange={handleChange("nfc_id")}
                    className="input border-dashed border-ink-400 font-mono"
                  />
                </Field>
              </div>

              <Field label="Distinguishing note (optional)" hint="Visible to security staff, never used for matching.">
                <input
                  placeholder="e.g. mole on left cheek, wears glasses"
                  value={form.distinguishing_note}
                  onChange={handleChange("distinguishing_note")}
                  className="input"
                />
              </Field>

              <div className="flex gap-s4">
                {profilePhoto.file && !profilePhoto.previewUrl ? (
                  // Selected, accepted, just unrenderable in this browser (HEIC).
                  <div className="flex h-[72px] w-[72px] flex-none flex-col items-center justify-center rounded-sm bg-canvas text-[11px] text-ink-600">
                    <span className="font-bold">HEIC</span>
                    <span>no preview</span>
                  </div>
                ) : profilePhoto.previewUrl || editingPerson?.photo_reference ? (
                  <img
                    src={profilePhoto.previewUrl || editingPerson.photo_reference}
                    alt="Profile preview"
                    className="h-[72px] w-[72px] flex-none rounded-sm border border-line object-cover"
                  />
                ) : (
                  <div className="flex h-[72px] w-[72px] flex-none flex-col items-center justify-center gap-0.5 rounded-sm border border-dashed border-ink-400 text-[11px] text-ink-600">
                    <Icon name="image" size={20} />
                    Photo
                  </div>
                )}
                <div className="flex min-w-0 flex-1 flex-col gap-s2">
                  <span className="field-label">
                    Profile picture <span className="font-normal text-ink-600">(optional)</span>
                  </span>
                  <span className="text-xs leading-snug text-ink-600">
                    Shown to the guard on a card tap or gate pass. Separate from the face-recognition photos - any
                    clear photo works. JPEG, PNG or HEIC only.
                  </span>
                  <WebcamCapture onCapture={setProfilePhoto} />
                  <div className="flex items-center gap-s3">
                    <input
                      type="file"
                      accept={PHOTO_ACCEPT}
                      onChange={(e) => setProfilePhoto(readPhotoInput(e))}
                      className="file-input"
                    />
                    {profilePhoto.file && (
                      <button type="button" onClick={() => setProfilePhoto(null)} className="link-action whitespace-nowrap">
                        Clear
                      </button>
                    )}
                  </div>
                  {profilePhotoError && <span className="text-sm font-bold text-danger">{profilePhotoError}</span>}
                </div>
              </div>

              {!editingId && (
                <GuidedEnrollment key={enrollmentResetKey} onChange={setEnrollment} disabled={isSubmitting} />
              )}

              {editingPerson && (
                <div className="flex flex-col gap-s3 border-t border-line pt-s4">
                  {editingPerson.confusable_partners?.length > 0 && (
                    <Notice tone="caution" icon="users-three">
                      <b>
                        Unusually similar to{" "}
                        {editingPerson.confusable_partners.map((partner) => partner.full_name).join(", ")}.
                      </b>{" "}
                      Please confirm this is expected (e.g. twins/siblings) - a gate scan will always require a card
                      tap for {editingPerson.confusable_partners.length > 1 ? "any of them" : "both of them"},
                      regardless of face-match confidence. Capture a few extra photos below and add a distinguishing
                      note above. Remove the flag under Lookalike pairs if it was detected in error.
                    </Notice>
                  )}
                  <div className="flex items-baseline justify-between">
                    <span className="t-section">Face enrollment</span>
                    <span className="font-mono text-sm font-semibold">
                      {photoCount} / {photoMax} photos
                    </span>
                  </div>
                  <span className="text-xs text-ink-600">
                    A few photos with slight variation (angle, expression) match more reliably than just one.
                  </span>
                  <div className="flex flex-wrap gap-s2">
                    {(editingPerson.face_embeddings || []).map((embedding) => (
                      <div key={embedding.id} className="relative">
                        <img
                          src={embedding.source_image}
                          alt=""
                          className="h-16 w-16 rounded-sm border border-line object-cover"
                        />
                        {embedding.is_low_confidence && (
                          <span
                            title="Single-photo import, not a guided live capture"
                            className="absolute -right-1 -top-1 flex h-5 w-5 items-center justify-center rounded-sm bg-caution text-white"
                          >
                            <Icon name="warning" bold size={12} />
                          </span>
                        )}
                      </div>
                    ))}
                    {photoCount === 0 && <p className="text-xs text-ink-600">No enrollment photos yet.</p>}
                  </div>
                  {photoCount < photoMax ? (
                    <div className="flex flex-col gap-s2 rounded-sm border border-line p-s3">
                      <WebcamCapture onCapture={handleAddPhoto} />
                      <span className="text-xs text-ink-600">or upload a file instead (JPEG, PNG or HEIC only):</span>
                      <input
                        type="file"
                        accept={PHOTO_ACCEPT}
                        disabled={isAddingPhoto}
                        onChange={(e) => handleAddPhoto(readPhotoInput(e))}
                        className="file-input"
                      />
                    </div>
                  ) : (
                    <span className="text-xs font-bold text-ink-600">Maximum photos reached.</span>
                  )}
                  {addPhotoError && <Notice tone="danger">{addPhotoError}</Notice>}
                </div>
              )}

              {formError && <Notice tone="danger">{formError}</Notice>}
            </div>

            <div className="flex flex-none items-center justify-end gap-s3 border-t border-line px-s5 py-s4">
              <button type="button" onClick={resetForm} className="btn-ghost">
                Cancel
              </button>
              <button
                type="submit"
                disabled={isSubmitting || (!editingId && !enrollment.isReady)}
                className="btn-primary"
              >
                {isSubmitting ? "Saving…" : editingId ? "Save changes" : "Save person"}
              </button>
            </div>
          </form>
        </>
      )}
    </div>
  );
}
