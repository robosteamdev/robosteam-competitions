"""
Django settings for scoreboard project.
"""
import environ

root = environ.Path(__file__) - 3  # get root of the project
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    STATIC_ROOT=(str, "./static"),
    DATABASE_URL=(str, "sqlite:///db.sqlite3"),
    REDIS_URL=(str, ""),
)
env.smart_cast = False
environ.Env.read_env(root(".env"))

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# No default: a deployment without SECRET_KEY in .env must fail loudly rather
# than run on a shared hardcoded key (Phase G2, finding H8). See .env.example.
SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# One switch for everything that only works once TLS actually terminates in front
# of us. Every setting below breaks a plain-HTTP deployment — a Secure cookie is
# never sent back, so nobody can log in, and SECURE_SSL_REDIRECT loops forever —
# so they stay off until the Nginx TLS terminator is live. Flip USE_HTTPS=True in
# .env as part of the DNS cutover, not before. (Decision 037.)
USE_HTTPS = env.bool('USE_HTTPS', default=False)
SECURE_SSL_REDIRECT            = USE_HTTPS
SESSION_COOKIE_SECURE          = USE_HTTPS
CSRF_COOKIE_SECURE             = USE_HTTPS
SECURE_HSTS_INCLUDE_SUBDOMAINS = USE_HTTPS
# HSTS is cached by browsers and hard to walk back, so it is opt-in even under
# USE_HTTPS: start at 0, raise it once you are sure the certificate renews.
SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=0)
SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=False)

INSTALLED_APPS = [
    "daphne",
    "channels",
    "contest.apps.ContestConfig",
    "devices",
    "mqtt_bridge",
    "scoring",
    "public",
    "ckeditor",
    "ckeditor_uploader",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "smart_selects",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    # Upstream scoreboard omitted this one. Nothing here is meant to be framed
    # (security.W002), and the judge controls are exactly what a clickjack would
    # target — a hidden START/STOP on a live run.
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "scoreboard.urls"

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
                "contest.context_processors.user_roles",
            ],
        },
    },
]

WSGI_APPLICATION = "scoreboard.wsgi.application"
ASGI_APPLICATION = 'scoreboard.asgi.application'

# Channel layer: Redis for real (cross-process) deployment so lap-event and
# timeout WebSocket pushes reach browsers from the mqtt_bridge / timeout_runs
# processes. Falls back to in-memory when REDIS_URL is unset or under test
# (tests run single-process and must not need a broker).
REDIS_URL = env("REDIS_URL")
_RUNNING_TESTS = 'test' in sys.argv

if REDIS_URL and not _RUNNING_TESTS:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [{
                    'address': REDIS_URL,
                    # MUST stay well above channels_redis's brpop_timeout (5s).
                    # An idle consumer sits in BRPOP with a 5s server-side window;
                    # redis-py >= 8 defaults socket_timeout to 5s too, so the two
                    # race, redis.exceptions.TimeoutError escapes the consumer and
                    # every board WebSocket dies with a 1011 after ~5s of quiet.
                    # (Found in Phase H; older redis-py defaulted this to None.)
                    'socket_timeout': env.int('REDIS_SOCKET_TIMEOUT', default=60),
                }],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }

DATABASES = {
    "default": env.db()
}

# SQLite serialises writers with a single lock, and this deployment runs three
# writing processes at once (Daphne, mqtt_bridge, timeout_runs). On the default
# rollback journal with a 5s busy timeout that surfaces as
# `OperationalError: database is locked` mid-competition — seen in Phase H while
# the timeout_runs sweep overlapped a ranking recalculation.
#   WAL          — readers no longer block behind a writer.
#   timeout      — a writer queues instead of failing.
#   IMMEDIATE    — take the write lock at BEGIN rather than upgrading mid
#                  transaction, which is the classic deadlock source and now
#                  matters more because of the G3 select_for_update blocks.
# A MySQL deployment ignores all of this.
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    DATABASES["default"].setdefault("OPTIONS", {})
    DATABASES["default"]["OPTIONS"].update({
        "timeout": env.int("SQLITE_TIMEOUT", default=30),
        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        "transaction_mode": "IMMEDIATE",
    })

# Phase G1 (H9): blocks `paho.mqtt.publish.single` for the whole suite so no test
# can open a socket to the broker. See scoreboard/test_runner.py.
TEST_RUNNER = 'scoreboard.test_runner.HermeticTestRunner'

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = env("STATIC_ROOT")

MEDIA_ROOT = root("media")
MEDIA_URL = "/media/"

CKEDITOR_UPLOAD_PATH = "uploads/"
CKEDITOR_CONFIGS = {
    'default': {
        'toolbar': 'full',
        'extraPlugins': 'image2,uploadimage',
        'filebrowserUploadUrl': '/ckeditor/upload/',
        'filebrowserBrowseUrl': '/ckeditor/browse/',
        'image2_alignClasses': ['image-left', 'image-center', 'image-right'],
        'image2_disableResizer': False,
        'height': 400,
        'width': '100%',
    },
}

JQUERY_URL = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/contest/post-login/'   # role-aware landing (contest.views.post_login_redirect)
LOGOUT_REDIRECT_URL = '/'

# Email: console backend for dev (prints messages to stdout) so the already-routed
# password-reset flow works without an SMTP server. Override via EMAIL_* env in prod.
EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', default='')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='no-reply@robosteam.eu')

# Seconds without a heartbeat or lap event before the offline sweep in the
# `timeout_runs` command flips a device from ONLINE to OFFLINE.
DEVICE_OFFLINE_SECONDS = env.int('DEVICE_OFFLINE_SECONDS', default=90)

# Optional shared secret for POST /devices/register/. Empty (the default) leaves
# self-registration open on the competition LAN; devices still need admin approval
# before they can score anything.
DEVICE_REG_SECRET = env('DEVICE_REG_SECRET', default='')

MQTT_BROKER_HOST = env('MQTT_BROKER_HOST', default='localhost')
MQTT_BROKER_PORT = env.int('MQTT_BROKER_PORT', default=51883)
MQTT_USERNAME = env('MQTT_USERNAME')
MQTT_PASSWORD = env('MQTT_PASSWORD')
