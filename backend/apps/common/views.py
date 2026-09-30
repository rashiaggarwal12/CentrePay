from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """Liveness + DB check for the platform's health probe."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "error", "db": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
