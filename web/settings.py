"""Django settings for the local ponishabot web GUI.

Local-only tool: bind 127.0.0.1, single process, always --noreload
(the bot owns a monitor thread + Chrome in this process).
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Repo root for `import ponishabot` (manage.py already inserts it; belt+suspenders)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "ponishabot-local-only-not-for-network",
)
# Local dev default True so runserver serves /static/ directly. Set
# DJANGO_DEBUG=0 on a VPS and let nginx serve collected static files.
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

# The placeholder below is fine for a loopback-only tool but must never reach a
# deployment: with it, session data and any signed value can be forged by
# anyone who has read this file. Refuse to start rather than run exposed.
if not DEBUG and SECRET_KEY.startswith("ponishabot-local-only"):
    raise RuntimeError(
        "DJANGO_SECRET_KEY is unset while DEBUG=0. Set DJANGO_SECRET_KEY to a "
        "long random value (deploy/bot.env.example) before running exposed."
    )

ALLOWED_HOSTS = [
    h.strip() for h in
    os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if h.strip()
]
# Behind a reverse proxy on a real domain, add it to CSRF_TRUSTED_ORIGINS
CSRF_TRUSTED_ORIGINS = [
    f"http://{h}" for h in ALLOWED_HOSTS if h not in ("127.0.0.1", "localhost")
] + [
    f"https://{h}" for h in ALLOWED_HOSTS if h not in ("127.0.0.1", "localhost")
]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.csrf",
            ],
        },
    },
]

WSGI_APPLICATION = "web.wsgi.application"

# No relational DB — bot state lives in config.yaml / SQLite session / memory.
DATABASES = {}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Tehran"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Vazirmatn Regular/Medium/Bold served from assets/fonts (web UI only)
STATICFILES_DIRS = [
    ("vazirmatn", BASE_DIR / "assets" / "fonts"),
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Hardening ──────────────────────────────────────────────────────────
# This dashboard has no login, so there is no session to steal — but it does
# hand out the Telegram token and AI key in the settings page HTML, and it is
# meant to sit behind nginx basic auth or an SSH tunnel. These settings close
# the cheap wins around that.

# Clickjacking: without this a page of the operator's could frame the
# dashboard and click its buttons.
X_FRAME_OPTIONS = "DENY"

# Keep the CSRF and session cookies out of reach of any script, and never send
# them over plain HTTP. Django's defaults are already HttpOnly; these make the
# other two explicit rather than implied.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Detect anything that sniffs a response as a different type.
SECURE_CONTENT_TYPE_NOSNIFF = True

# Trust the proxy's scheme only for HTTPS deployments; the dashboard itself
# speaks HTTP on loopback. SECURE_SSL_REDIRECT stays off for that reason —
# turning it on would break the documented nginx layout, where nginx already
# terminates TLS and redirects.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_REFERRER_POLICY = "same-origin"

# HSTS is only meaningful once the operator has committed to HTTPS. Enabled
# via env so a plain-HTTP tunnel deployment is not bricked by a year-long
# max-age the browser refuses to forget.
if os.environ.get("DJANGO_HSTS"):
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", 31_536_000))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

