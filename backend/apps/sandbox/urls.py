from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="sandbox_index"),
    path("pay/<str:link_id>/", views.pay, name="sandbox_pay"),
]
