import os

import dj_database_url

from .base import *  # noqa: F403

SECRET_KEY = "test-secret-key-that-is-long-enough-for-hs256"  # noqa: S105
DEBUG = False
ALLOWED_HOSTS = ["*"]

# CI sets DATABASE_URL to a real Postgres. Locally, fall back to in-memory SQLite.
if not os.environ.get("DATABASE_URL"):
    DATABASES = {"default": dj_database_url.parse("sqlite://:memory:")}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
RAZORPAY_KEY_ID = "rzp_test_dummykey"
RAZORPAY_KEY_SECRET = "dummy-secret"  # noqa: S105
RAZORPAY_WEBHOOK_SECRET = "test-webhook-secret"  # noqa: S105
