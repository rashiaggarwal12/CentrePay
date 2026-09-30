"""Domain errors and the API-wide error envelope.

Every error response has the same shape:
    {"error": {"code": "INVALID_TRANSITION", "message": "...", "details": {...}}}
"""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.views import exception_handler


class DomainError(exceptions.APIException):
    """Base class for business-rule violations raised from the service layer."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "DOMAIN_ERROR"
    default_detail = "The request violates a business rule."

    def __init__(self, message: str | None = None, code: str | None = None):
        super().__init__(detail=message or self.default_detail, code=code or self.default_code)


class InvalidTransition(DomainError):
    default_code = "INVALID_TRANSITION"
    default_detail = "This action is not allowed in the invoice's current status."


class InvoiceNotEditable(DomainError):
    default_code = "INVOICE_NOT_EDITABLE"
    default_detail = "Only draft invoices can be edited."


class StaleVersion(DomainError):
    default_code = "STALE_VERSION"
    default_detail = "The invoice was modified by someone else. Reload and try again."


class ManagerRequired(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "MANAGER_REQUIRED"
    default_detail = "Only a manager can perform this action."


class IdempotencyKeyReused(DomainError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "IDEMPOTENCY_KEY_REUSED"
    default_detail = "This Idempotency-Key was already used for a different request."


class RequestInProgress(DomainError):
    default_code = "REQUEST_IN_PROGRESS"
    default_detail = "The same request is already being processed. Retry in a few seconds."


class PaymentGatewayError(DomainError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_code = "GATEWAY_ERROR"
    default_detail = "The payment gateway could not be reached. Try again."


class PaymentGatewayNotConfigured(DomainError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "GATEWAY_NOT_CONFIGURED"
    default_detail = "Online payments are not configured on this server."


class BusinessValidationError(DomainError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "VALIDATION_ERROR"
    default_detail = "Invalid input."


# DRF exception class -> our error code
_CODE_BY_EXCEPTION = [
    (exceptions.ValidationError, "VALIDATION_ERROR"),
    (exceptions.ParseError, "PARSE_ERROR"),
    (exceptions.AuthenticationFailed, "AUTHENTICATION_FAILED"),
    (exceptions.NotAuthenticated, "NOT_AUTHENTICATED"),
    (exceptions.PermissionDenied, "PERMISSION_DENIED"),
    (exceptions.NotFound, "NOT_FOUND"),
    (exceptions.MethodNotAllowed, "METHOD_NOT_ALLOWED"),
    (exceptions.Throttled, "RATE_LIMITED"),
]


def api_exception_handler(exc, context):
    # DRF converts Django's Http404 / PermissionDenied internally; do the same here so they
    # map to NOT_FOUND / PERMISSION_DENIED below.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)
    if response is None:
        return None  # unhandled -> 500, reported to Sentry

    if isinstance(exc, DomainError):
        code = exc.get_codes()
        message = str(exc.detail)
        details = None
    else:
        code = next(
            (c for cls, c in _CODE_BY_EXCEPTION if isinstance(exc, cls)),
            "ERROR",
        )
        if isinstance(exc, exceptions.ValidationError):
            message = "Invalid input."
            details = response.data
        else:
            message = str(getattr(exc, "detail", exc))
            details = None

    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    response.data = body
    return response
