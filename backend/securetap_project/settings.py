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

# face_recognition compare distance threshold - lower is stricter. 0.6 is the
# upstream library's own documented default, but that's tuned for general
# face-matching demos, not an access-control gate - at 0.6 an unenrolled
# stranger can easily land within range of *some* enrolled face by chance,
# especially as more people get enrolled. 0.45 trades a few more "please
# scan again" false rejects for far fewer wrongly-granted false accepts,
# which is the right tradeoff for a security gate.
FACE_MATCH_THRESHOLD = env.float("FACE_MATCH_THRESHOLD", default=0.45)

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
