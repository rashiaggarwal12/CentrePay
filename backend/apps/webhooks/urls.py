from django.urls import path

from .views import razorpay_webhook

urlpatterns = [
    path("razorpay/", razorpay_webhook, name="razorpay_webhook"),
]
