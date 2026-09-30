from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import PaymentRefundsView, RefundViewSet

router = SimpleRouter()
router.register("refunds", RefundViewSet, basename="refund")

urlpatterns = [
    path(
        "payments/<int:payment_id>/refunds/", PaymentRefundsView.as_view(), name="payment_refunds"
    ),
    *router.urls,
]
