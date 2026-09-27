"""Replaces the old `if settings.DEBUG: urlpatterns += static(...)` line in
urls.py - see media_auth.py for the full "why" writeup. This view requires a
valid signed token (see media_auth.build_signed_media_url) for the exact
path requested, then delegates the actual file-serving to Django's own
django.views.static.serve - reusing it rather than hand-rolling file I/O
means the well-tested path-safety behavior (safe_join, which rejects any
"../" attempt to escape MEDIA_ROOT) and Content-Type/caching headers come
for free, and this view only adds the one thing static.serve doesn't do:
checking who's allowed to ask for the file at all.

Deliberately NOT gated behind `if settings.DEBUG` the way the old line was -
this needs to keep working the same way regardless of DEBUG's value, so
fixing this finding doesn't create a new dependency on a separate,
not-yet-made decision about flipping DEBUG off.
"""
from django.conf import settings
from django.http import HttpResponseForbidden
from django.views.static import serve as static_serve

from .media_auth import verify_media_token


def protected_media_serve(request, path):
    token = request.GET.get("token")
    if not verify_media_token(path, token):
        return HttpResponseForbidden("Missing or invalid media access token.")
    return static_serve(request, path=path, document_root=settings.MEDIA_ROOT)
