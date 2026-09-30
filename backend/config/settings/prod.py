import os

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration

from .base import *  # noqa: F403
from .base import ALLOWED_HOSTS, DATABASES, SECRET_KEY, env_bool, env_list

if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set in production")

DEBUG = False

# Render (and most PaaS) terminate TLS at a proxy and tell us the public hostname.
if render_host := os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, render_host]
    os.environ.setdefault("PUBLIC_BASE_URL", f"https://{render_host}")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS") + [
    f"https://{host}" for host in ALLOWED_HOSTS if host and not host.startswith(".")
]
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]  # the platform's probe calls plain HTTP
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
SECURE_HSTS_INCLUDE_SUBDOMAINS = False  # the domain is the PaaS's, not ours
# W005/W021 ask for includeSubDomains and preload. Both would make promises about a
# domain we don't own (*.onrender.com), so they are deliberately off.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True

DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Login rate limiting needs a cache shared by all web workers; per-process memory
# would let each gunicorn worker allow its own 10 attempts.
if redis_url := os.environ.get("REDIS_URL"):
    CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": redis_url}
    }

# Free-tier mode: no separate worker process, so Celery tasks run inline in the web
# request. Webhooks are then processed before the 200 is returned (slower, still correct:
# the raw event is stored first and processing is idempotent). Leave off when a worker runs.
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER")

if dsn := os.environ.get("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=dsn,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        release=os.environ.get("RENDER_GIT_COMMIT"),
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,
    )
