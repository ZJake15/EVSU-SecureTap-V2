"""
Django settings for the EVSU SecureTap backend.
"""
import os
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

# Where this copy keeps everything it writes - its .env, the database,
# photos, the covered-face model. An installed copy can't write inside its own
# program folder, so the launcher points SECURETAP_DATA_DIR at a data folder
# (see device_setup.py at the repo root); unset - running from the code
# folder - it's all inside backend/ as it always was.
DATA_DIR = Path(os.environ["SECURETAP_DATA_DIR"]) if os.environ.get("SECURETAP_DATA_DIR") else None
ENV_FILE = DATA_DIR / "backend.env" if DATA_DIR else BASE_DIR / ".env"

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(ENV_FILE)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DEBUG")
# Never on an installed copy: Django's debug error pages show the request
# that failed - form fields, names, ID numbers - and the system's internals.
# Developers running from the code folder keep whatever backend/.env says.
if DATA_DIR is not None:
    DEBUG = False
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
    "audit",
    "configuration",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves the dashboard's ready-made files (see DASHBOARD_DIST below), so
    # the backend alone runs the whole system - no Node.js needed to use it.
    "whitenoise.middleware.WhiteNoiseMiddleware",
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

# Which database to use: "sqlite" (one file, nothing to install - the default
# for a new setup) or "mysql" (a separate server, for a real deployment).
# Left unset, an older .env that already has DB_NAME keeps using MySQL, so
# upgrading never switches databases by surprise. `manage.py
# copy_mysql_to_sqlite` moves existing data across.
DB_ENGINE = env("DB_ENGINE", default="").strip().lower() or ("mysql" if env("DB_NAME", default="") else "sqlite")

# MySQL connection details - only used with DB_ENGINE=mysql (and as the
# source for copy_mysql_to_sqlite).
MYSQL_DATABASE = {
    "ENGINE": "django.db.backends.mysql",
    "NAME": env("DB_NAME", default="securetap"),
    "USER": env("DB_USER", default="securetap_app"),
    "PASSWORD": env("DB_PASSWORD", default=""),
    "HOST": env("DB_HOST", default="127.0.0.1"),
    "PORT": env("DB_PORT", default="3306"),
    "OPTIONS": {"charset": "utf8mb4"},
}

# The SQLite database file - in the data folder (DATA_DIR, above), or
# backend/ when running from the code folder. DB_PATH overrides both.
SQLITE_DATABASE = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": env("DB_PATH", default=str((DATA_DIR or BASE_DIR) / "db.sqlite3")),
    "OPTIONS": {
        # The gate camera writes several times a second while the dashboard
        # reads. WAL mode lets reads and a write happen at the same time;
        # a write that does collide waits (up to `timeout` seconds) instead
        # of failing with "database is locked"; IMMEDIATE claims the write
        # lock at the start of a transaction, so two never deadlock midway.
        "timeout": 20,
        "transaction_mode": "IMMEDIATE",
        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
    },
}

if DB_ENGINE not in ("sqlite", "mysql"):
    raise ValueError(f"DB_ENGINE must be 'sqlite' or 'mysql', not {DB_ENGINE!r}")
DATABASES = {"default": MYSQL_DATABASE if DB_ENGINE == "mysql" else SQLITE_DATABASE}

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
    # Raised from 8 - Admin and SASO accounts hold real system power (full
    # user management, deactivation approval), so 8 was on the short end for
    # accounts worth targeting in an offline crack attempt if a password hash
    # ever leaked. This only changes what a NEW/CHANGED password must meet -
    # existing accounts' already-hashed passwords keep working unchanged.
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
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
MEDIA_ROOT = (DATA_DIR or BASE_DIR) / "media"

# The dashboard's ready-made build (`npm run build` in dashboard/). The
# backend serves it at http://localhost:8000/ - its files (scripts, styles,
# icons) through WhiteNoise, and every page address through
# securetap_project.spa_views - so using the system needs no Node.js.
DASHBOARD_DIST = Path(env("DASHBOARD_DIST", default=str(BASE_DIR.parent / "dashboard" / "dist")))
WHITENOISE_ROOT = DASHBOARD_DIST
# Also serve Django's own static files (the /admin pages' styles) straight
# from the installed apps - with DEBUG off, runserver no longer does that,
# and there's no collectstatic step on an installed copy.
WHITENOISE_USE_FINDERS = True

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

# Confusable-pair detection (identical twins, or any two people a 2D face
# system genuinely cannot be expected to tell apart) - deliberately a
# SEPARATE, higher threshold from FACE_MATCH_SIMILARITY_THRESHOLD above, not
# a reuse of it: the match threshold answers "is this good enough to accept
# as a match", this one answers "is this so high that two DIFFERENT people
# scoring it means their faces are fundamentally too alike for this system to
# safely tell apart, no matter how the match itself turns out". See
# users/confusable_utils.py. 0.60 is a starting point, not a validated value -
# same tune-later caveat as every other threshold on this page.
CONFUSABLE_SIMILARITY_THRESHOLD = env.float("CONFUSABLE_SIMILARITY_THRESHOLD", default=0.60)

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

# How many CPU cores the face AI (detection, recognition, fake-face check)
# may use for each camera frame. 0 = decide automatically: half of this
# computer's processors, at most 4 - on a 4-core laptop like the Intel N100
# that's 2, leaving the other cores for the gate monitor's video, the
# browser and MySQL. Left uncapped, the AI starts a worker per processor on
# every frame and the rest of the system stutters. Needs a backend restart.
AI_THREADS = env.int("AI_THREADS", default=0)

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

# How far a face may be turned away from the camera before the gate scan
# skips it instead of matching it - the nose tip's offset from the midpoint
# between the eyes, as a fraction of the inter-eye distance (see
# insightface_utils.head_yaw_ratio). ~0.0 is dead-on frontal; ~0.35 is
# roughly a 30 degree turn.
#
# This exists because a turned face is the one input that fails *both* ways
# at once: ArcFace embeddings are trained on roughly frontal faces, so a
# profile view of an enrolled person matches nobody and gets voted through
# as Unknown, while the same odd angle can drag the liveness score under its
# threshold and log them as a spoof attempt instead. Skipping the face
# outright (not counting it as an attempt in either direction) means the
# scan simply waits for the frame where they look at the camera - which,
# walking through a gate, is usually a fraction of a second later.
#
# 0.35 is roughly a 30 degree turn, which is about where ArcFace embeddings
# (trained on near-frontal faces) start degrading badly - so the threshold is
# set where matching actually stops working, not at an arbitrary angle.
# Measured over this project's own stored captures for a baseline: posed
# reference photos ran 0.012-0.147 (none would be skipped), while real
# walk-by frames that failed to match ran a median of 0.130 with a long tail
# past 10.0 - the ratio blows up near profile, because the two eyes converge
# on the same point and collapse the denominator.
#
# Raise it to be more permissive (matches more angles, risks more false
# Unknowns), lower it to demand a squarer look at the camera. Being strict is
# cheap here: the scan runs every ~0.2s, so a skipped frame just means the
# person is matched a fraction of a second later, not that they're turned
# away. The real risk of going too low is a camera mounted off to one side of
# the walkway, where nobody ever reads as frontal.
FACE_MAX_YAW_RATIO = env.float("FACE_MAX_YAW_RATIO", default=0.35)

# Below this, a face is treated as possibly occluded (mouth/nose covered by a
# hand, mask, or high collar) rather than matched or voted through as
# Unknown - see insightface_utils.mouth_visibility_ratio for exactly what's
# measured and why. Only evaluated on frames that already passed the yaw
# check above, since a turned face naturally foreshortens mouth width too and
# would otherwise be misread as occluded.
#
# Unlike FACE_MAX_YAW_RATIO, this one does NOT have a comfortable margin.
# Painting a skin-toned patch over the mouth/nose of 10 real enrolled photos
# and re-running this exact detector: clean mouth-width ratio ran
# 0.776-1.018 (mean 0.847), the same photos with the patch ran 0.663-0.771
# (mean 0.709) - a real, measurable drop, but the two ranges sit right next
# to each other with only a hairline gap on this small sample, nothing like
# yaw's >10x separation. 0.73 sits closer to the clean-photo floor than the
# midpoint, deliberately: it will likely MISS lighter/partial occlusion
# (a hand covering just the chin, say) - the safe failure mode, since a
# missed occlusion just falls through to normal matching/Unknown handling,
# same as before this existed. A covered face is never logged either (see
# IdentifyView._occlusion_prompt) - a wrong read only costs a brief "please
# uncover your face" prompt, never a log entry.
#
# Treat this exactly like FACE_MATCH_SIMILARITY_THRESHOLD: a starting point
# for a specific detector and a small test set, not a validated value - watch
# real occlusion_detected rows once they exist and retune.
#
# UPDATE after real-world testing: on its own, this threshold badly
# under-detects partial coverage - a hand covering just the mouth/chin
# (without reaching the nose) very often doesn't shrink mouth width enough
# to trip it, and it can't be loosened further without misreading visible
# faces as covered (the clean-photo floor sits right against it - see the
# numbers above). FACE_MAX_MOUTH_TEXTURE_RATIO below is a second, independent
# signal added specifically to catch what this one misses.
FACE_MIN_MOUTH_VISIBILITY_RATIO = env.float("FACE_MIN_MOUTH_VISIBILITY_RATIO", default=0.73)

# The second occlusion signal (see insightface_utils.lower_face_texture_ratio)
# - how texture-rich the lower third of the face (mouth/chin) is relative to
# the upper-middle (forehead/eyes, always visible). A real mouth has lips,
# teeth edges, and a shadow under the nose; a hand or flat cloth over it is
# comparatively smooth, so this ratio drops. Below this, occlusion is flagged
# even if mouth_visibility_ratio didn't catch it - occlusion is confirmed if
# EITHER signal fires, since empirically they miss different cases (mouth
# width degrades geometrically; texture degrades regardless of exactly where
# SCRFD regresses the mouth-corner keypoints under occlusion).
#
# Measured alongside FACE_MIN_MOUTH_VISIBILITY_RATIO's own test: clean ran
# 0.463-1.103; several partial-occlusion cases that ratio missed entirely
# (mouth-only coverage, not reaching the nose) ran 0.000-0.328 here - a much
# cleaner gap. 0.40 sits comfortably below the clean floor. Same caveat as
# every other threshold here: a starting point for one small test set, not a
# validated value - a beard, heavy uneven lighting, or a very smooth-skinned
# mouth could plausibly read low without anything actually covering it.
FACE_MAX_MOUTH_TEXTURE_RATIO = env.float("FACE_MAX_MOUTH_TEXTURE_RATIO", default=0.40)

# The third occlusion signal: the detector's own per-face detection
# confidence (det_score - "how face-like is this region", not a match
# score). Unlike the two ratios above, this doesn't derive from the 5
# keypoints at all, so it fails independently of them - a face partly
# covered genuinely looks less face-like to the detector even when its
# regressed keypoints still land in a plausible-looking arrangement (see
# insightface_utils.mouth_visibility_ratio's docstring for why keypoint
# *plausibility* specifically was tested and does NOT work as a signal -
# every plausibility measure tried came back statistically indistinguishable
# between clean and occluded faces on this project's test set; SCRFD's
# regression head produces a wrong-but-self-consistent guess, not a
# distorted one). det_score is the more direct thing the user actually
# wanted checked: it doesn't need geometry to be off at all.
#
# Measured on the same test as the two ratios above: clean det_score ran
# 0.71-0.84; occluded frames ran as low as 0.50-0.65, though with real
# overlap into the clean range too - not clean separation on its own, which
# is why this is a THIRD OR-condition alongside the other two rather than a
# replacement for either. 0.65 sits below the observed clean floor (0.71)
# with some margin. Same caveat as every threshold on this page: a starting
# point for one detector and one small test set, not validated - and
# because det_score already factors into nothing else in this codebase,
# there's no other existing behavior this could disturb by being wrong.
FACE_MIN_DET_SCORE_UNOCCLUDED = env.float("FACE_MIN_DET_SCORE_UNOCCLUDED", default=0.65)

# Which rule decides whether a face is covered (see users/occlusion_utils.py):
#   "rules"      - the three thresholds above, OR-ed together (the original
#                  behavior, and the default).
#   "classifier" - the Random Forest trained by `manage.py
#                  train_occlusion_classifier`, fed the same three measurements.
# Classifier mode falls back to "rules" on its own - for the whole process if
# the model file is missing or won't load, or for a single frame if one of the
# measurements it needs couldn't be taken - so switching it on can never stop
# the gate scan from running. Anything other than "classifier" means "rules".
# Read once at startup (the model file is loaded once, then kept in memory).
# Under `manage.py runserver` - which is how the launcher runs the backend -
# the server restarts itself when backend/.env or the model file changes (see
# users/apps.py); any other server needs a manual restart.
# Either way, a face that already matches an enrolled person is never flagged
# as covered - see IdentifyView._already_recognizable.
OCCLUSION_DETECTION_MODE = env("OCCLUSION_DETECTION_MODE", default="rules").strip().lower()
OCCLUSION_CLASSIFIER_PATH = env(
    "OCCLUSION_CLASSIFIER_PATH",
    default=str(DATA_DIR / "occlusion_classifier.joblib" if DATA_DIR
                else BASE_DIR / "users" / "occlusion_classifier.joblib"),
)
# Labeled photos for training it (collect_occlusion_training_data).
OCCLUSION_TRAINING_DIR = (DATA_DIR or BASE_DIR) / "occlusion_training_data"

# The face models' folder (InsightFace's "root"). The installer ships the two
# the system uses in face-models/ next to backend/, so an installed copy never
# needs the internet; otherwise InsightFace's own ~/.insightface, where it
# downloads them on first use.
_BUNDLED_FACE_MODELS = BASE_DIR.parent / "face-models"
INSIGHTFACE_ROOT = env(
    "INSIGHTFACE_ROOT",
    default=str(_BUNDLED_FACE_MODELS if _BUNDLED_FACE_MODELS.is_dir() else Path.home() / ".insightface"),
)
# The classifier's own "probably covered" cutoff, on its 0-1 probability.
# 0.5 is exactly what the training command's evaluation measured (sklearn's
# plain predict()). Raise it if live testing shows too many false "please
# uncover your face" prompts; lower it if real coverings slip through.
OCCLUSION_CLASSIFIER_THRESHOLD = env.float("OCCLUSION_CLASSIFIER_THRESHOLD", default=0.5)

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
