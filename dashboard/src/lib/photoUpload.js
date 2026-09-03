// Photo-upload format policy, mirroring the backend's ALLOWED_UPLOAD_FORMATS
// (backend/users/insightface_utils.py). The backend stays the authority - it
// checks the file's actual decoded format, which is the only thing that can't
// be faked by renaming a file. This exists so a wrong file is caught the
// instant it's picked, instead of after an upload round-trip.

export const PHOTO_ACCEPT =
  "image/jpeg,image/png,image/heic,image/heif,.jpg,.jpeg,.png,.heic,.heif";

export const PHOTO_FORMAT_MESSAGE = "Only JPEG, PNG and HEIC photos are accepted.";

const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/heic", "image/heif"];
const ALLOWED_EXTENSIONS = [".jpg", ".jpeg", ".png", ".heic", ".heif"];

// HEIC is accepted for upload but no non-Apple browser can paint it, so the
// usual URL.createObjectURL preview would render as a broken image. Callers
// check this and show a labelled placeholder instead. The stored photo is
// fine either way - the backend re-encodes it to JPEG (normalize_to_jpeg).
const UNPREVIEWABLE_TYPES = ["image/heic", "image/heif"];
const UNPREVIEWABLE_EXTENSIONS = [".heic", ".heif"];

function matches(file, types, extensions) {
  const type = (file.type || "").toLowerCase();
  const name = (file.name || "").toLowerCase();
  // Windows has no MIME entry for HEIC unless the HEIF Image Extensions are
  // installed, so browsers there hand over an empty `type` for exactly the
  // format most likely to arrive off an iPhone. Falling back to the filename
  // is what keeps those uploads working.
  if (type) return types.includes(type);
  return extensions.some((extension) => name.endsWith(extension));
}

/**
 * Returns an error message if `file` isn't an accepted photo format, or null
 * if it looks fine. Null for a missing file too - "nothing selected" is the
 * caller's business, not a format problem.
 */
export function photoFormatError(file) {
  if (!file) return null;
  return matches(file, ALLOWED_TYPES, ALLOWED_EXTENSIONS) ? null : PHOTO_FORMAT_MESSAGE;
}

/**
 * Whether the browser can be expected to render this file in an <img>.
 * False for HEIC/HEIF - accepted uploads that simply can't be shown.
 */
export function canPreviewInBrowser(file) {
  if (!file) return false;
  return !matches(file, UNPREVIEWABLE_TYPES, UNPREVIEWABLE_EXTENSIONS);
}

/**
 * An object URL for `file`, or null when the browser couldn't render it
 * anyway - so callers never create a URL that only ever produces a broken
 * image icon (and never leak one that nothing will revoke).
 */
export function previewUrlFor(file) {
  if (!file || !canPreviewInBrowser(file)) return null;
  return URL.createObjectURL(file);
}

/**
 * Reads the chosen file out of a file-input change event, and clears the input.
 *
 * Every photo <input type="file"> must go through this rather than reading
 * event.target.files directly. Clearing does three things:
 *
 * 1. A rejected file must not leave its name sitting beside the button. A
 *    filename displayed next to an error message reads as though the upload
 *    went through, which is exactly backwards.
 * 2. It covers server-side rejections too - an unsupported file renamed to
 *    .jpg passes the checks this component can run, and is only caught once
 *    the backend decodes it. Clearing unconditionally means that name is gone
 *    as well, without threading a ref through every handler to clear later.
 * 3. It re-arms the input. Without this, picking the *same* file again after a
 *    failed quality check fires no change event at all, because the input's
 *    value never changed - so a registrar retrying the same photo would get
 *    silence.
 *
 * Nothing is lost by the native filename disappearing: what's selected is
 * shown by the preview thumbnail (or the HEIC placeholder) and its
 * Clear/Retake control, which is the state a registrar actually reads.
 *
 * The returned File stays valid after the input is cleared - it's a plain
 * reference, not a live view of the input.
 */
export function readPhotoInput(event) {
  const input = event.target;
  const file = input.files?.[0] || null;
  input.value = "";
  return file;
}
