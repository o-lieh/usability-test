import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _list(name, default=""):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


# --- Core -------------------------------------------------------------------
SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = os.environ.get("DEBUG", "0") == "1"
ALLOWED_HOSTS = _list("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = _list("CSRF_TRUSTED_ORIGINS")

# Everything persistent (SQLite file + recordings) lives under DATA_DIR.
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
RECORDINGS_DIR = DATA_DIR / "recordings"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

# --- Study-specific settings ------------------------------------------------
APP_URL = os.environ.get("APP_URL", "")
MANAGER_PASSWORD = os.environ.get("MANAGER_PASSWORD", "")

# --- Django plumbing --------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "study",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",  # this site must never be framed
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        "OPTIONS": {
            "timeout": 20,
            "transaction_mode": "IMMEDIATE",  # serialize writers instead of failing with "database is locked"
            "init_command": "PRAGMA journal_mode=WAL;",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "fa"
TIME_ZONE = "Asia/Tehran"
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Recording chunks are posted as raw request bodies (~1-2 MB each).
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

# --- Behind Caddy (TLS terminates there) -------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
