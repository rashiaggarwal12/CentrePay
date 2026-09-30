#!/usr/bin/env sh
# Web process for Render/Railway/Docker: migrate, optionally seed demo data, serve.
set -e
python manage.py migrate --noinput
if [ "$SEED_DEMO_DATA" = "true" ]; then
  python manage.py seed
fi
exec gunicorn config.wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout 30 \
  --access-logfile -
