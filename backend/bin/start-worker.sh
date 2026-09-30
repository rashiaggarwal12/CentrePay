#!/usr/bin/env sh
# Celery worker with Beat embedded (-B) for the 02:00 IST reconciliation. Embedded Beat is
# only safe with exactly ONE worker instance; scale out by running Beat as its own service.
set -e
exec celery -A config worker -B -l info --concurrency "${CELERY_CONCURRENCY:-2}"
