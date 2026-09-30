from django.db import transaction
from rest_framework import filters, mixins, viewsets

from apps.accounts.permissions import CentreScopedMixin
from apps.audit.services import audit, snapshot

from .models import Customer
from .serializers import CustomerSerializer


class CustomerViewSet(
    CentreScopedMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "phone", "email"]

    @transaction.atomic
    def perform_create(self, serializer):
        customer = serializer.save(centre=self.staff.centre)
        audit("customer.created", customer, actor=self.request.user)

    @transaction.atomic
    def perform_update(self, serializer):
        before = snapshot(serializer.instance)
        customer = serializer.save()
        audit("customer.updated", customer, actor=self.request.user, before=before)
