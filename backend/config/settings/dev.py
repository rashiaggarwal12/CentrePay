import os

import dj_database_url

from .base import *  # noqa: F403
from .base import BASE_DIR

DEBUG = True
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-secret-key-do-not-use-in-prod")  # noqa: S105
ALLOWED_HOSTS = ["*"]

# Without DATABASE_URL, fall back to SQLite so the project runs with zero setup.
# Row locks (select_for_update) are no-ops on SQLite; use Postgres for anything concurrency-related.
if not os.environ.get("DATABASE_URL"):
    DATABASES = {"default": dj_database_url.parse(f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}

# Run Celery tasks inline when no broker is configured.
CELERY_TASK_ALWAYS_EAGER = not os.environ.get("REDIS_URL")
