"""
Django settings for the EVSU SecureTap backend.
"""
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "accounts",
    "users",
    "logs",
    "reports",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "securetap_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "securetap_project.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("DB_NAME", default="securetap"),
        "USER": env("DB_USER", default="securetap_app"),
        "PASSWORD": env("DB_PASSWORD", default=""),
        "HOST": env("DB_HOST", default="127.0.0.1"),
        "PORT": env("DB_PORT", default="3306"),
        "OPTIONS": {"charset": "utf8mb4"},
    }
}

# bcrypt first so new passwords are hashed with bcrypt, per project spec.
# The other hashers stay listed so Django can still verify against them if
# a hash in one of those formats is ever encountered.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="Asia/Manila")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
    ),
    "DEFAULT_PAGINATION_CLASS": "securetap_project.pagination.StandardPagination",
    "PAGE_SIZE": 25,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=12),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS", default=["http://localhost:5173", "http://127.0.0.1:5173"]
)

# Shared secret the entry-agent sends via the X-Service-Token header when
# calling /api/verify. Deliberately not JWT-based: the agent is a trusted
# device/service, not a logged-in dashboard user.
ENTRY_AGENT_SERVICE_TOKEN = env("ENTRY_AGENT_SERVICE_TOKEN")

# ArcFace (InsightFace buffalo_s) embeddings are compared by cosine
# similarity, not the raw Euclidean distance the old dlib/face_recognition
# pipeline used - HIGHER = MORE similar here (the opposite of the old
# FACE_MATCH_THRESHOLD, which was lower = stricter). Named differently on
# purpose so the two can never be silently confused. 0.45 is a starting
# point, not a validated value - run `manage.py evaluate_threshold` once
# enough real people are enrolled and use whatever it recommends instead.
FACE_MATCH_SIMILARITY_THRESHOLD = env.float("FACE_MATCH_SIMILARITY_THRESHOLD", default=0.45)

# Passive liveness (anti-spoofing) check - see users/liveness_utils.py. Runs
# on a standard 2D webcam frame (no depth/IR hardware available at the gate),
# so this is classical texture/frequency/reflectance analysis, not a depth
# check. Same "higher = more likely real" sense as FACE_MATCH_SIMILARITY_
# THRESHOLD above. 0.5 is a starting point, not a validated value - same
# caveat as the face-match threshold: tune against real photo/screen-replay
# test attempts once they exist.
LIVENESS_SCORE_THRESHOLD = env.float("LIVENESS_SCORE_THRESHOLD", default=0.5)

# A face match is sent to the NFC tiebreak flow (see IdentifyView) instead
# of being auto-accepted/rejected when either: the top match's similarity is
# within this margin of the threshold (a "barely passed/failed" call), or
# the top-1 and top-2 candidates are within this margin of each other (two
# people who look similar enough to be genuinely ambiguous).
TIEBREAK_MARGIN = env.float("TIEBREAK_MARGIN", default=0.05)
# How long a "please tap your card" prompt stays open before the gate scan
# gives up waiting and logs the attempt as unresolved/ambiguous instead.
TIEBREAK_TIMEOUT_SECONDS = env.int("TIEBREAK_TIMEOUT_SECONDS", default=10)

# Rolling-window majority vote: a face match isn't confirmed into a real
# EntryLog row from a single frame - the same person has to be the top match
# in at least VOTE_REQUIRED_AGREEMENT of the last VOTE_WINDOW_SIZE scan
# attempts for this gate, within VOTE_WINDOW_SECONDS, before it's trusted.
# The same voting logic now also gates "Unknown" (see IdentifyView) - widened
# slightly from 3/3s so someone walking through gets a few more real chances
# to land a usable frame within the window.
VOTE_WINDOW_SIZE = env.int("VOTE_WINDOW_SIZE", default=4)
VOTE_REQUIRED_AGREEMENT = env.int("VOTE_REQUIRED_AGREEMENT", default=2)
VOTE_WINDOW_SECONDS = env.int("VOTE_WINDOW_SECONDS", default=4)

# Detector input size for the gate-scan path specifically (see
# insightface_utils._get_scan_app) - smaller than enrollment's fixed 640x640
# so per-frame detection is faster during the continuous scan, at the cost
# of some range on faces far from the camera. Not a security-relevant
# threshold like the ones below, just a speed/range tradeoff - tune based on
# your camera's typical distance-to-face at the gate.
GATE_SCAN_DET_SIZE = env.int("GATE_SCAN_DET_SIZE", default=480)

# Below this Laplacian variance, a gate-scan frame is treated as too
# motion-blurred to trust - skipped entirely (not counted as "no match"),
# waiting for a sharper frame instead. Deliberately more lenient than
# enrollment's MIN_BLUR_VARIANCE (80.0, in insightface_utils.py) since a
# walk-by frame is never going to be as sharp as a posed enrollment photo -
# a starting point to tune, same as FACE_MATCH_SIMILARITY_THRESHOLD.
GATE_SCAN_MIN_BLUR_VARIANCE = env.float("GATE_SCAN_MIN_BLUR_VARIANCE", default=25.0)

# A face this close to the edge of the frame (as a fraction of frame
# width/height) is likely partially cut off - skipped rather than matched,
# since a partial face produces unreliable embeddings.
FACE_EDGE_MARGIN_RATIO = env.float("FACE_EDGE_MARGIN_RATIO", default=0.02)

# The continuous camera scan re-checks the gate every couple of seconds, so a
# person lingering nearby would otherwise create a new log row on every pass.
# Recognitions of the same person within this window are deduped server-side.
RECOGNITION_COOLDOWN_SECONDS = env.int("RECOGNITION_COOLDOWN_SECONDS", default=60)

# An unrecognized face has no Person to key a per-identity cooldown on, so
# this compares the face encoding itself against recently-unmatched faces: one
# judged the same stranger (within this many seconds) reuses the existing log
# row instead of creating a new one and capturing another photo, so someone
# lingering at the gate unrecognized doesn't flood the log/Live Monitoring the
# way idle no-face scans used to.
UNENROLLED_CAPTURE_COOLDOWN_SECONDS = env.int("UNENROLLED_CAPTURE_COOLDOWN_SECONDS", default=30)

# Same "same attempt still there" dedup idea as
# UNENROLLED_CAPTURE_COOLDOWN_SECONDS above, but tracked separately for
# spoof-suspected faces so it can be tuned independently once real
# spoof-attempt data exists.
SPOOF_CAPTURE_COOLDOWN_SECONDS = env.int("SPOOF_CAPTURE_COOLDOWN_SECONDS", default=30)
