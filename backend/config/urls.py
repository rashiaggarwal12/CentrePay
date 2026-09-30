from django.contrib import admin
from django.urls import include, path

from apps.common.views import healthz

api_v1 = [
    path("auth/", include("apps.accounts.urls")),
    path("", include("apps.customers.urls")),
    path("", include("apps.billing.urls")),
    path("", include("apps.payments.urls")),
    path("reports/", include("apps.reports.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz, name="healthz"),
    path("api/v1/", include(api_v1)),
    path("webhooks/", include("apps.webhooks.urls")),
    path("sandbox/", include("apps.sandbox.urls")),  # 404s unless PAYMENT_GATEWAY=fake
]
