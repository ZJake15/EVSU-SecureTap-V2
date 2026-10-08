"""The dashboard, served by the backend itself.

The dashboard is a single-page app: one index.html plus scripts that draw
every page (/, /logs, /users, /settings, ...) in the browser. WhiteNoise
serves the scripts, styles and icons from DASHBOARD_DIST; this view answers
every page address with index.html, so opening or refreshing a link like
http://localhost:8000/settings works. /api, /admin and /media never reach
here - urls.py routes them first.
"""

from django.conf import settings
from django.http import FileResponse, HttpResponse

NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>EVSU SecureTap</title>
<body style="font-family:sans-serif;max-width:560px;margin:80px auto;line-height:1.5">
<h1>The dashboard isn't built yet</h1>
<p>Open the SecureTap launcher and click <b>Dashboard</b> - it builds it for you.
Or run <code>npm run build</code> in the <code>dashboard</code> folder, then reload this page.</p>
</body>"""


def dashboard_index(request, *args, **kwargs):
    index = settings.DASHBOARD_DIST / "index.html"
    if not index.exists():
        return HttpResponse(NOT_BUILT, status=503)
    response = FileResponse(open(index, "rb"), content_type="text/html; charset=utf-8")
    # Always re-check index.html, so a rebuilt dashboard shows up on the next
    # reload (its scripts have new, content-hashed names each build).
    response["Cache-Control"] = "no-cache"
    return response
