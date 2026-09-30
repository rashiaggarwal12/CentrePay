"""Development-only CORS, so the app's web build (another port) can call the API.

Only enabled in config/settings/dev.py. The native app doesn't need CORS at all, and
production doesn't serve a web client.
"""

import re

from django.http import HttpResponse

LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+)(:\d+)?$")


class DevCorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.headers.get("Origin", "")
        allowed = bool(LOCAL_ORIGIN.match(origin))
        if allowed and request.method == "OPTIONS":
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)
        if allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Headers"] = (
                "Authorization, Content-Type, Idempotency-Key"
            )
            response["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
            response["Vary"] = "Origin"
        return response
