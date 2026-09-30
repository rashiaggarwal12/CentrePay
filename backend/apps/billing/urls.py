from rest_framework.routers import SimpleRouter

from .views import InvoiceViewSet, ServiceViewSet

router = SimpleRouter()
router.register("services", ServiceViewSet, basename="service")
router.register("invoices", InvoiceViewSet, basename="invoice")

urlpatterns = router.urls
