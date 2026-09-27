"""Signed, short-lived access tokens for files under MEDIA_ROOT.

Why this exists: every enrollment photo, gate-capture photo, and face-
embedding source image used to be served by Django's plain static-file
helper with zero authentication whenever DEBUG=True (the currently active
setting) - anyone who could reach the server could download any photo by
filename, no login required. That's the single most serious finding from
the security audit (biometric photos are sensitive personal information
under RA 10173).

The fix isn't "require a Bearer token header" - the dashboard renders every
photo through a plain <img src="..."> tag (LiveMonitoring.jsx, Users.jsx),
and browsers don't attach custom headers to those requests, so header-based
auth would break every photo in the UI. Instead, the API responses that
already hand out a media URL (already gated behind JWT auth for the
dashboard, or the entry-agent's service token for /verify and /identify)
sign that URL's path at the moment it's generated. The media-serving view
below (see media_views.py) just checks that signature - it doesn't need to
know or care who's asking, only that SOME already-authenticated response
handed this exact URL to SOME client recently. This works unmodified with
both existing consumers: the dashboard's <img> tags (the full signed URL is
still just a URL) and the entry-agent's api_client.fetch_photo() (a plain
GET with no headers).
"""
from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

MEDIA_TOKEN_SALT = "securetap.media-access"
# One hour - long enough that a dashboard page (or the entry-agent, which
# fetches a photo once per card tap) never sees a link go stale mid-view,
# short enough that a URL copied out of a browser's network tab or history
# doesn't stay valid indefinitely. Every API response that includes a media
# URL regenerates a fresh token anyway, so in practice a page that's still
# open just keeps working - this bounds how long a URL is useful once
# it's left that context.
MEDIA_TOKEN_MAX_AGE_SECONDS = 60 * 60

_signer = TimestampSigner(salt=MEDIA_TOKEN_SALT)


def _relative_media_path(file_field_url):
    """A FieldFile's .url is MEDIA_URL-relative already (e.g.
    "/media/reference_photos/123.jpg") - strip the MEDIA_URL prefix so what
    gets signed is exactly the path the serving view will receive (the
    <path> part of the media/<path> URL pattern), independent of whatever
    MEDIA_URL happens to be configured as."""
    media_url_path = "/" + settings.MEDIA_URL.strip("/")
    if file_field_url.startswith(media_url_path + "/"):
        return file_field_url[len(media_url_path) + 1 :]
    return file_field_url.lstrip("/")


def verify_media_token(relative_path, token):
    """True if `token` is a currently-valid signature for exactly
    `relative_path` (no token, tampered token, wrong path, or expired all
    return False rather than raising) - the serving view treats this as a
    plain yes/no gate."""
    if not token:
        return False
    try:
        unsigned = _signer.unsign(token, max_age=MEDIA_TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return unsigned == relative_path


def build_signed_media_url(request, file_field_url):
    """Drop-in replacement for `request.build_absolute_uri(field.url)` at
    every point that hands a media URL back to a client - appends a signed
    token as a query parameter so the URL is independently checkable by the
    (unauthenticated-by-header) media-serving view. `file_field_url` is
    whatever a FieldFile's `.url` returns; `request` may be None (some
    internal call sites build these outside a request context), in which
    case a relative signed URL is returned instead of an absolute one."""
    relative_path = _relative_media_path(file_field_url)
    token = _signer.sign(relative_path)
    absolute = request.build_absolute_uri(file_field_url) if request else file_field_url
    separator = "&" if "?" in absolute else "?"
    return f"{absolute}{separator}token={token}"
