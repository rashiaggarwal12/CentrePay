import django_filters
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import CentreScopedMixin, IsCentreStaff, IsManager

from . import refunds
from .models import Payment, Refund
from .serializers import RefundRejectSerializer, RefundRequestSerializer, RefundSerializer


class PaymentRefundsView(APIView):
    """POST /payments/{id}/refunds/ — any staff member can request; a manager approves."""

    def post(self, request, payment_id):
        payment = get_object_or_404(
            Payment, pk=payment_id, invoice__centre=request.user.staff.centre
        )
        serializer = RefundRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        refund = refunds.request_refund(
            payment.pk, staff=request.user.staff, **serializer.validated_data
        )
        return Response(RefundSerializer(refund).data, status=status.HTTP_201_CREATED)


class RefundFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=Refund.Status.choices)

    class Meta:
        model = Refund
        fields = ["status", "invoice"]


class RefundViewSet(
    CentreScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """GET /refunds/?status=requested is the managers' approval queue."""

    queryset = Refund.objects.select_related(
        "invoice", "payment", "requested_by__user", "approved_by__user"
    )
    serializer_class = RefundSerializer
    filterset_class = RefundFilter
    centre_field = "invoice__centre"

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [IsAuthenticated(), IsManager()]
        return [IsAuthenticated(), IsCentreStaff()]

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        refund = refunds.approve_refund(self.get_object().pk, staff=self.staff)
        refund.refresh_from_db()
        return Response(RefundSerializer(refund).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        serializer = RefundRejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        refund = refunds.reject_refund(
            self.get_object().pk, staff=self.staff, note=serializer.validated_data["note"]
        )
        return Response(RefundSerializer(refund).data)
